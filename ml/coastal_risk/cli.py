"""Command-line entry point for historical download and model training."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Sequence

from .data import DEFAULT_LOCATIONS_PATH, download_dataset, load_locations
from .train import train_and_export


ML_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ML_ROOT / "data" / "processed" / "coastal_history.csv.gz"
DEFAULT_CACHE = ML_ROOT / "data" / "raw"
DEFAULT_ARTIFACT = ML_ROOT.parent / "server" / "models" / "coastal_risk_v1.json"
DEFAULT_REPORT = ML_ROOT / "reports" / "coastal_risk_v1_metrics.json"
DEFAULT_UK_LOCATIONS = (
    "uk_brighton",
    "uk_portsmouth",
    "uk_plymouth",
    "uk_aberdeen",
    "uk_cardiff",
    "uk_bangor_ni",
)


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc


def _location_ids(value: str) -> Sequence[str] | None:
    if value.strip().lower() == "all":
        return None
    result = tuple(item.strip() for item in value.split(",") if item.strip())
    if not result:
        raise argparse.ArgumentTypeError("locations must be comma-separated IDs or 'all'")
    return result


def _add_download_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", type=_date, default=date(2024, 1, 1))
    parser.add_argument("--end", type=_date, default=date(2025, 12, 31))
    parser.add_argument(
        "--locations",
        type=_location_ids,
        default=DEFAULT_UK_LOCATIONS,
        help="comma-separated IDs from locations.json, or 'all'",
    )
    parser.add_argument("--locations-file", type=Path, default=DEFAULT_LOCATIONS_PATH)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--chunk-days", type=int, default=90)
    parser.add_argument(
        "--weather-source",
        choices=("archive", "historical-forecast"),
        default="archive",
        help="archive is reproducible and faster; historical-forecast matches the live API more closely",
    )
    parser.add_argument("--refresh", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Historical-data pipeline for the coastal risk demonstrator"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download", help="download and label history")
    _add_download_arguments(download)

    train = subparsers.add_parser("train", help="train from an existing dataset")
    train.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    train.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    train.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    train.add_argument("--model-version", default="coastal-risk-logreg-v1")

    run = subparsers.add_parser("run", help="download, label, train and evaluate")
    _add_download_arguments(run)
    run.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    run.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    run.add_argument("--model-version", default="coastal-risk-logreg-v1")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command in {"download", "run"}:
        locations = load_locations(args.locations_file, args.locations)
        download_dataset(
            locations,
            args.start,
            args.end,
            args.cache,
            args.dataset,
            chunk_days=args.chunk_days,
            refresh=args.refresh,
            weather_source=args.weather_source,
        )
    if args.command in {"train", "run"}:
        train_and_export(
            args.dataset,
            args.artifact,
            args.report,
            model_version=args.model_version,
        )


if __name__ == "__main__":
    main()
