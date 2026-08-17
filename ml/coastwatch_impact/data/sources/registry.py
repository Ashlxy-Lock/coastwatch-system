"""Central registry for external CoastWatch data sources.

URLs intentionally live here rather than in individual parsers so a changed
landing page or API can be reviewed in one place.  A registry entry documents
access; it does not claim that credentials or historical archives are present.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """Stable provenance metadata used by raw-file manifests."""

    name: str
    owner: str
    source_url: str
    license_name: str
    access_method: str
    authentication: str
    update_frequency: str
    allowed_modes: tuple[str, ...]
    notes: str = ""


SOURCE_REGISTRY: dict[str, SourceMetadata] = {
    "ea_historic_warnings": SourceMetadata(
        name="ea_historic_warnings",
        owner="Environment Agency",
        source_url=("https://environment.data.gov.uk/dataset/88bed270-d465-11e4-8669-f0def148f590"),
        license_name="Open Government Licence",
        access_method="manual ZIP, folder, CSV or JSON import",
        authentication="none for supplied public archive",
        update_frequency="source-dependent historic archive release",
        allowed_modes=("hindcast_research", "operational_backtest"),
    ),
    "ea_warning_areas": SourceMetadata(
        name="ea_warning_areas",
        owner="Environment Agency",
        source_url=("https://environment.data.gov.uk/dataset/87e5d78f-d465-11e4-9343-f0def148f590"),
        license_name="Open Government Licence",
        access_method="manual GeoJSON, GeoPackage or shapefile import",
        authentication="none for supplied public archive",
        update_frequency="source snapshot; review on every import",
        allowed_modes=("hindcast_research", "operational_backtest", "live_shadow"),
    ),
    "ea_flood_outlines": SourceMetadata(
        name="ea_flood_outlines",
        owner="Environment Agency",
        source_url=("https://environment.data.gov.uk/dataset/889885c0-d465-11e4-9507-f0def148f590"),
        license_name="Open Government Licence",
        access_method="manual geospatial archive import",
        authentication="none for supplied public archive",
        update_frequency="source-dependent historic archive release",
        allowed_modes=("hindcast_research", "operational_backtest"),
        notes="Spatial/date evidence only; not an exact-hour onset label.",
    ),
    "ea_tide_realtime": SourceMetadata(
        name="ea_tide_realtime",
        owner="Environment Agency",
        source_url="https://environment.data.gov.uk/flood-monitoring/id/stations",
        license_name="Open Government Licence",
        access_method="optional Flood Monitoring API download",
        authentication="none for public API",
        update_frequency="near-real-time; verify at retrieval",
        allowed_modes=("live_shadow",),
    ),
    "ea_tide_archive": SourceMetadata(
        name="ea_tide_archive",
        owner="Environment Agency",
        source_url="https://environment.data.gov.uk/flood-monitoring/archive",
        license_name="Open Government Licence",
        access_method="manual archive import",
        authentication="none for supplied public archive",
        update_frequency="source-dependent archive release",
        allowed_modes=("hindcast_research", "operational_backtest"),
    ),
    "wavenet": SourceMetadata(
        name="wavenet",
        owner="Cefas",
        source_url="https://www.cefas.co.uk/data-and-publications/wavenet/",
        license_name="Source licence must be confirmed from the downloaded archive",
        access_method="manual CSV or NetCDF archive import",
        authentication="source-specific; record with imported archive",
        update_frequency="source-dependent; record with imported archive",
        allowed_modes=("hindcast_research", "operational_backtest", "live_shadow"),
    ),
    "ntslf": SourceMetadata(
        name="ntslf",
        owner="National Tidal and Sea Level Facility",
        source_url="https://ntslf.org/storm-surges/surge-model",
        license_name="Source terms must be confirmed for the supplied archive",
        access_method="manual issued-forecast archive import",
        authentication="source-specific; record with imported archive",
        update_frequency="issued model runs; record exact run cadence",
        allowed_modes=("operational_backtest", "live_shadow"),
    ),
    "metoffice_datahub": SourceMetadata(
        name="metoffice_datahub",
        owner="Met Office",
        source_url="https://datahub.metoffice.gov.uk/",
        license_name="Met Office DataHub terms",
        access_method="credentialed API; archived runs required for backtests",
        authentication="API credential from environment only",
        update_frequency="product-specific; freeze before use",
        allowed_modes=("operational_backtest", "live_shadow"),
    ),
    "copernicus_marine": SourceMetadata(
        name="copernicus_marine",
        owner="Copernicus Marine Service",
        source_url="https://data.marine.copernicus.eu/",
        license_name="Copernicus Marine Service licence",
        access_method="credentialed client; product and dataset IDs required",
        authentication="Copernicus credential from environment only",
        update_frequency="product-specific; freeze before use",
        allowed_modes=("hindcast_research", "operational_backtest", "live_shadow"),
    ),
    "static_geo": SourceMetadata(
        name="static_geo",
        owner="Environment Agency and project-reviewed sources",
        source_url="https://environment.data.gov.uk/dataset",
        license_name="Licence must be recorded per imported source",
        access_method="manual CSV or geospatial import",
        authentication="source-specific; record with imported snapshot",
        update_frequency="snapshot; review on every import",
        allowed_modes=("hindcast_research", "operational_backtest", "live_shadow"),
        notes="Current snapshots may be temporally inconsistent with historic events.",
    ),
    "ea_aims_assets": SourceMetadata(
        name="ea_aims_assets",
        owner="Environment Agency",
        source_url=("https://environment.data.gov.uk/dataset/019a8eaa-b27f-4ae6-a9fd-e8e27cdd101a"),
        license_name="Open Government Licence",
        access_method="manual reviewed static snapshot import",
        authentication="none for supplied public archive",
        update_frequency="snapshot; review on every import",
        allowed_modes=("hindcast_research", "operational_backtest", "live_shadow"),
        notes=(
            "Current asset state may be temporally inconsistent with historic events; "
            "crest heights require a confirmed vertical datum."
        ),
    ),
    "ea_rofrs": SourceMetadata(
        name="ea_rofrs",
        owner="Environment Agency",
        source_url=("https://environment.data.gov.uk/dataset/96ab4342-82c1-4095-87f1-0082e8d84ef1"),
        license_name="Open Government Licence",
        access_method="manual reviewed static snapshot import",
        authentication="none for supplied public archive",
        update_frequency="snapshot; review on every import",
        allowed_modes=("hindcast_research", "operational_backtest", "live_shadow"),
        notes="Risk categories are static context, not event occurrence labels.",
    ),
}


def source_metadata(name: str) -> SourceMetadata:
    """Return a known source or fail instead of inventing provenance."""

    try:
        return SOURCE_REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown source registry key: {name!r}") from exc


__all__ = ["SOURCE_REGISTRY", "SourceMetadata", "source_metadata"]
