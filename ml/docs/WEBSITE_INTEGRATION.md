# Website Integration Note

ImpactNet v2 is intentionally separate from the current browser-side v1 model. The
website must not load safetensors or private source credentials in client code.

Recommended flow:

```text
browser -> same-origin read-only server route -> v2 Shadow API
```

The server route should allow-list sites, apply timeouts/cache, validate a reduced
response, and keep credentials server-only. The UI must display model name,
`label_mode`, coverage, calibration state, data quality, `shadow_mode`, and the
research disclaimer. `insufficient_data` must not be replaced by a plausible-looking
number. Research bands must not imitate official Flood Alert/Flood Warning names.

