"""Auditable source adapters for CoastWatch ImpactNet v2.

Networked and credentialed sources are opt-in.  Every concrete import preserves
the exact raw bytes and writes a content-addressed provenance manifest.
"""

from .base import (
    ForecastAvailabilityError,
    NetworkAccessDisabledError,
    NoSourceDataError,
    RawImportResult,
    SourceAdapter,
    SourceAdapterError,
    SourceCredentialsError,
    UnsupportedSourceFormatError,
)
from .copernicus import (
    CopernicusMarineForecastAdapter,
    CopernicusMarineHindcastAdapter,
    CopernicusRequestConfig,
)
from .ea_tide import EATideGaugeArchiveAdapter, EATideGaugeRealtimeAdapter
from .ea_warnings import EAHistoricWarningsAdapter, HistoricWarningsManualAdapter
from .flood_outlines import FloodOutlinesManualAdapter
from .forecast_contracts import (
    assert_available_at_prediction_time,
    select_latest_as_of,
    validate_issued_forecasts,
)
from .metoffice import MetOfficeDataHubAdapter
from .ntslf import NTSLFForecastManualAdapter, NTSLFManualArchiveAdapter
from .registry import SOURCE_REGISTRY, SourceMetadata, source_metadata
from .static_geo import AIMSStaticGeoAdapter, RoFRSStaticGeoAdapter, StaticGeoManualAdapter
from .warning_areas import WarningAreasManualAdapter
from .wavenet import WaveNetManualArchiveAdapter, WaveNetRealtimeAdapter

__all__ = [
    "SOURCE_REGISTRY",
    "AIMSStaticGeoAdapter",
    "CopernicusMarineForecastAdapter",
    "CopernicusMarineHindcastAdapter",
    "CopernicusRequestConfig",
    "EAHistoricWarningsAdapter",
    "EATideGaugeArchiveAdapter",
    "EATideGaugeRealtimeAdapter",
    "FloodOutlinesManualAdapter",
    "ForecastAvailabilityError",
    "HistoricWarningsManualAdapter",
    "MetOfficeDataHubAdapter",
    "NTSLFForecastManualAdapter",
    "NTSLFManualArchiveAdapter",
    "NetworkAccessDisabledError",
    "NoSourceDataError",
    "RawImportResult",
    "RoFRSStaticGeoAdapter",
    "SourceAdapter",
    "SourceAdapterError",
    "SourceCredentialsError",
    "SourceMetadata",
    "StaticGeoManualAdapter",
    "UnsupportedSourceFormatError",
    "WarningAreasManualAdapter",
    "WaveNetManualArchiveAdapter",
    "WaveNetRealtimeAdapter",
    "assert_available_at_prediction_time",
    "select_latest_as_of",
    "source_metadata",
    "validate_issued_forecasts",
]
