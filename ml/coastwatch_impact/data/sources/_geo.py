"""Optional geospatial helpers with safe shapefile packaging."""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import pandas as pd

from .base import NoSourceDataError, UnsupportedSourceFormatError

_REQUIRED_SHAPEFILE_SUFFIXES = (".shp", ".shx", ".dbf")
_OPTIONAL_SHAPEFILE_SUFFIXES = (".prj", ".cpg", ".qpj", ".sbn", ".sbx")


def assert_geojson_wgs84(payload: bytes, *, source_name: str) -> None:
    """Reject legacy GeoJSON that explicitly declares a non-WGS84 CRS."""

    try:
        document = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{source_name}: invalid GeoJSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{source_name}: GeoJSON root must be an object")
    declared = document.get("crs")
    if declared is None:
        return
    crs_text = json.dumps(declared, sort_keys=True).upper().replace(" ", "")
    if not any(token in crs_text for token in ("EPSG:4326", "CRS84", "WGS84", "WGS-84")):
        raise ValueError(
            f"{source_name}: explicit GeoJSON CRS is not WGS84/EPSG:4326; "
            "use a reviewed reprojection before import"
        )


def package_shapefile(path: str | Path) -> bytes:
    """Create a deterministic ZIP retaining every required shapefile byte."""

    shape_path = Path(path)
    companions = {
        suffix: shape_path.with_suffix(suffix)
        for suffix in (*_REQUIRED_SHAPEFILE_SUFFIXES, *_OPTIONAL_SHAPEFILE_SUFFIXES)
    }
    missing = [
        suffix for suffix in _REQUIRED_SHAPEFILE_SUFFIXES if not companions[suffix].is_file()
    ]
    if missing:
        raise UnsupportedSourceFormatError(
            f"shapefile {shape_path.name} is incomplete; missing sidecars {missing}. "
            "Provide the complete shapefile set or a ZIP/GeoPackage."
        )
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for suffix, companion in sorted(companions.items()):
            if not companion.is_file():
                continue
            info = zipfile.ZipInfo(f"{shape_path.stem}{suffix}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, companion.read_bytes())
    return output.getvalue()


def read_geospatial(path: str | Path) -> pd.DataFrame:
    """Read GeoPackage/shapefile data with an explicit CRS and WGS84 output."""

    try:
        import geopandas as gpd
    except ImportError as exc:
        raise UnsupportedSourceFormatError(
            "GeoPackage/shapefile parsing requires the 'geo' optional dependencies; "
            "install coastwatch-impact[geo] or import GeoJSON"
        ) from exc

    source = Path(path)
    if source.suffix.lower() != ".zip":
        return _normalise_geodataframe(gpd.read_file(source), source.name)

    with (
        zipfile.ZipFile(source) as archive,
        tempfile.TemporaryDirectory(prefix="coastwatch_geo_") as temporary,
    ):
        root = Path(temporary)
        safe_infos: list[zipfile.ZipInfo] = []
        for info in archive.infolist():
            member = PurePosixPath(info.filename)
            if info.is_dir() or member.is_absolute() or ".." in member.parts:
                continue
            if member.suffix.lower() in {
                ".gpkg",
                *_REQUIRED_SHAPEFILE_SUFFIXES,
                *_OPTIONAL_SHAPEFILE_SUFFIXES,
            }:
                safe_infos.append(info)
        candidates = [
            info
            for info in safe_infos
            if PurePosixPath(info.filename).suffix.lower() in {".gpkg", ".shp"}
        ]
        if len(candidates) != 1:
            raise UnsupportedSourceFormatError(
                f"{source.name}: expected exactly one GeoPackage or shapefile dataset, "
                f"found {len(candidates)}"
            )
        for info in safe_infos:
            member = PurePosixPath(info.filename)
            target = root.joinpath(*member.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
        selected = PurePosixPath(candidates[0].filename)
        dataset_path = root.joinpath(*selected.parts)
        return _normalise_geodataframe(gpd.read_file(dataset_path), source.name)


def _normalise_geodataframe(frame: object, source_name: str) -> pd.DataFrame:
    if len(frame) == 0:  # type: ignore[arg-type]
        raise NoSourceDataError(f"{source_name}: no geospatial features")
    if frame.crs is None:  # type: ignore[attr-defined]
        raise ValueError(f"{source_name}: CRS is missing; refusing to guess")
    normalised = frame.to_crs("EPSG:4326")  # type: ignore[attr-defined]
    normalised["geometry_json"] = normalised.geometry.map(  # type: ignore[attr-defined,index]
        lambda item: json.dumps(item.__geo_interface__, separators=(",", ":"))
    )
    normalised["coordinate_reference_system"] = "EPSG:4326"  # type: ignore[index]
    return pd.DataFrame(normalised.drop(columns="geometry"))  # type: ignore[attr-defined]


__all__ = ["assert_geojson_wgs84", "package_shapefile", "read_geospatial"]
