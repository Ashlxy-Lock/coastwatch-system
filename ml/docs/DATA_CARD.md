# CoastWatch ImpactNet v2 Data Card

Status: engineering scaffold; no confirmed-impact training dataset is currently
registered.

## Intended scope

- Geography: approved England coastal zones only.
- Storage time zone: UTC. Europe/London may be used for display only.
- Prediction history: 72 hours of observations.
- Forecast horizon: 24 hours, using only forecasts issued by the prediction time.
- Primary table format: Parquet with manifests and SHA-256 checksums.

The existing v1 Open-Meteo dataset is a weak-rule demonstrator and is not a v2
confirmed-impact dataset.

## Source registry and current availability

| Source | Owner | Intended use | Access/authentication | Current raw location/checksum | Allowed modes | Current blocker or limitation |
|---|---|---|---|---|---|---|
| Historic Flood Warnings | Environment Agency | warning auxiliary labels and event evidence | manual ZIP/folder import; OGL metadata must be retained | not supplied | research/backtest after import | warnings are not proof of impact |
| Flood Warning Areas | Environment Agency | coastal-zone definition and spatial joins | manual GeoJSON/GPKG/SHP import | not supplied | all after approved mapping | requires geographic extras and human mapping approval |
| Historic Flood Map / Recorded Flood Outlines | Environment Agency | spatial evidence | manual geospatial import | not supplied | research evidence | cannot create an exact onset hour by itself |
| Tide Gauge API/archive | Environment Agency | observed water level | public API or manual archive | not supplied | research/live subject to age | local datum must not be mixed with mAOD |
| WaveNet | Cefas | wave observations and quality flags | manual CSV/NetCDF preferred | not supplied | research/live if interface stable | direction reference/convention must be known |
| Tide-surge forecasts | NTSLF | physical water-level baseline | manual issued-forecast archive | not supplied | operational backtest only with issue times | final observations cannot substitute for issued forecasts |
| Weather DataHub | Met Office | issued weather forecasts | API key from environment only | not supplied | backtest/live | historic forecast runs required for backtest |
| North-West Shelf products | Copernicus Marine | optional forecast/hindcast context | authenticated optional adapter | not supplied | explicit research/backtest mode | product/dataset/version must be recorded |
| AIMS Asset Bundle | Environment Agency | defence/static exposure | manual geospatial import | not supplied | research | present-day assets may be temporally inconsistent historically |
| RoFRS | Environment Agency | long-term static risk | manual geospatial import | not supplied | research | not an event label and may be a current snapshot |

Every imported raw file must record source URL, retrieval time, temporal coverage,
licence, original filename, parser version, SHA-256, and notes. Raw files are
immutable; re-importing creates a new version or returns the existing hash match.
The machine-readable source registry also records owner, access method,
authentication, expected update-frequency semantics, and explicitly allowed
`hindcast_research` / `operational_backtest` / `live_shadow` modes. Exact raw
locations, coverage, checksums, parser versions, and any licence override are
completed by each immutable import manifest; they remain `not supplied` until a
real file is actually imported.

## Synthetic fixture

The Phase-1 synthetic fixture contains three fictional sites, controlled tides,
storm surges, sparse synthetic events, issued forecasts with error, missing values,
and stale-source examples. It exists only to exercise engineering paths and carries
`synthetic_data=true`. No synthetic metric is a scientific result.

## Known missing evidence

- reviewed A/B confirmed coastal-impact events;
- approved Flood Warning Area mappings for Brighton, Portsmouth, and Plymouth;
- datum-compatible tide and defence elevations;
- historical issued NTSLF/Met Office/Copernicus forecast runs;
- WaveNet archive and direction metadata;
- licences/checksums for each actual imported file.
