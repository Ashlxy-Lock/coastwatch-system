# CoastWatch system source

This private repository contains the reproducible source for the CoastWatch
course project outside the public website:

- `firmware/` — ESP32 display/uplink, STM32 ultrasonic bridge and OpenMV code.
- `server/` — FastAPI device gateway, authenticated admin console, data store,
  model training/evaluation code and sensor-proxy experiments.
- `ml/` — the research-grade ImpactNet/data/evaluation pipeline.
- `ops/` — Windows startup, deployment, rollback and health-check tooling.
- `docs/` — architecture, wiring, monitoring and operating notes.

The public website is maintained separately at
[`Ashlxy-Lock/coastwatch-website`](https://github.com/Ashlxy-Lock/coastwatch-website).

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

## Verification snapshot

Before the initial GitHub export, the FastAPI server suite passed 185 tests and
`ruff check app tests` completed without findings. Hardware builds and physical
sensor tests remain separate because they require the connected boards.
