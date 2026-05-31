# Kukii-Home

A vision-intelligence and rule-engine layer for Home Assistant. Kukii-Home adds VLM-based reasoning, conversational rule creation, multi-camera identity fusion, and feedback-driven optimization on top of your existing camera infrastructure — without locking you into a specific NVR.

> **Status:** Pre-implementation. Architecture is documented and stable; code scaffolding in progress.

---

## What it does

- **Watches your cameras** through whatever NVR you already have (Agent DVR, Frigate, Blue Iris, Synology, QNAP, UniFi Protect) — or direct RTSP, no NVR needed
- **Reasons about what it sees** using a local Vision Language Model with cloud fallback for hard cases
- **Creates rules conversationally** ("Let me know when the mailman arrives and announce it on Sonos") — no YAML, no UI builders
- **Acts through Home Assistant** — every notification, light, lock, speaker action goes through HA services
- **Learns from your feedback** — automatically tests rule variants against archived clips when you dismiss alerts as false positives
- **Respects privacy** — local-first by design, data classes enforced at the data-plane, GDPR/CCPA-aligned

---

## Architecture at a glance

```
Cameras  →  NVR Adapter (pluggable)  →  Preprocessor  →  Kukii-Home Core  →  HA  →  Devices
                                                            ↓
                                                       VLM reasoning
                                                       Rule engine
                                                       Memory + learning
```

Full architecture: [`docs/architecture/`](docs/architecture/) — 21 sections covering every component.

Start here: [Architecture index](docs/architecture/README.md)

---

## Repo layout

```
Kukii-Home/
├── docs/                    Architecture, requirements, decision logs
├── services/                Long-running Python services (core, VLM router, memory, etc.)
├── adapters/                Pluggable NVR adapters (one per platform)
├── ha-integration/          Home Assistant custom integration (Python)
├── frontend/                TypeScript: HA custom cards + optional operator dashboard
├── shared/                  JSON schemas, MCP protos, shared libraries (Python + TS)
├── infrastructure/          Docker compose, NATS config, DB migrations
├── tests/                   Integration + e2e tests
├── scripts/                 Setup, dev, release tooling
├── planning/                Source of truth for GitHub epics and issues
└── .github/                 Issue templates, CI workflows
```

Each top-level folder has its own README explaining scope, conventions, and pointers into the architecture docs.

---

## Project status

- **Architecture:** stable (see [docs/architecture/](docs/architecture/))
- **Implementation:** scaffolding phase
- **Target:** v1 ships service-mode NVR adapters, direct-RTSP support, core VLM pipeline, HA integration, conversational rules
- **Long-term:** v3–v4 makes the NVR layer optional (Kukii-Home absorbs motion detection, archival, clip generation)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and conventions.

Open issues and epics live in [GitHub Issues](https://github.com/DarinShapiro/Kukii-Home/issues). Implementation work tracks against the epics defined in [planning/](planning/).

---

## License

See [LICENSE](LICENSE).
