# CoastWatch system source

This private repository is the canonical source for the CoastWatch course
project. The active hardware architecture is **ESP32-S3 + OpenMV**; the former
external sensor bridge has been retired and is not part of this repository.

- `firmware/esp32/` — sensor acquisition, local safety rules, LCD/touch,
  networking, collection sessions and model control.
- `firmware/openmv/` — camera setup, person detection, VIS/CTL protocol and
  red/yellow/green status-light controller.
- `server/` — FastAPI device gateway, authenticated admin console, data store,
  model training/evaluation code and sensor-proxy experiments.
- `ml/` — the research-grade ImpactNet/data/evaluation pipeline.
- `ops/` — Windows startup, deployment, rollback and health-check tooling.
- `docs/` — current architecture, wiring, hardware, training and operating
  notes.
- `website/` — Git submodule pinned to the independently deployable public
  website source.

The public website source remains independently deployable at
[`Ashlxy-Lock/coastwatch-website`](https://github.com/Ashlxy-Lock/coastwatch-website).
The submodule preserves its Sites deployment history without duplicating a
second writable copy. Clone the complete project with:

```powershell
git clone --recurse-submodules https://github.com/Ashlxy-Lock/coastwatch-system.git
```

## Deliberately excluded

This repository does **not** contain device/admin credentials, Cloudflare
credentials, production SQLite databases, raw or licensed datasets, trained
model artifacts, generated reports, build products, virtual environments or
vendor reference archives. The root `.gitignore` is deny-by-default so new
workspace content is not uploaded accidentally.

The Great Yarmouth 15-feature model currently shown on the public website is an
exploratory single-site baseline. Its inputs, generated dataset and browser
artifact live in the website repository. The backend research pipelines in this
repository remain separate and must not be represented as an already deployed
official-warning model.

## Hardware source included

All project-owned OpenMV Python files, ESP32 headers/sources/tests and the
FastAPI/ML/operations code are tracked. OpenMV's person classifier is the
firmware ROM model `/rom/person_detect.tflite`, so there is no omitted local
model binary.

See [current architecture](docs/architecture.md),
[wiring](docs/wiring.md), and
[hardware inventory](docs/hardware_inventory.md) before connecting or flashing
hardware.
