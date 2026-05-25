# tests/

Cross-service integration and end-to-end tests. Unit tests live alongside the code they test (in each service's `tests/` subfolder); this directory is for tests that span multiple services or require the full runtime.

## Layout

| Folder                         | Purpose                                                                                                                                          |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`integration/`](integration/) | Multi-service tests run against a docker-compose stack — e.g., "event flows from NVR adapter → triage → VLM → action dispatch → HA service call" |
| [`e2e/`](e2e/)                 | Full system tests including real (or recorded) camera streams, real HA instance, real rule firings. Slowest tier; gated to nightly.              |
| [`fixtures/`](fixtures/)       | Shared test fixtures: recorded RTSP clips, mock VLM responses, sample event payloads, identity gallery seed data                                 |

## Conventions

- **Framework:** `pytest` with `pytest-asyncio` for Python; `vitest` for TypeScript
- **Test stack:** `infrastructure/docker/test.yml` brings up a minimal stack with mock external dependencies
- **Mocking strategy:** real services where feasible; mocks only at the edges (cameras, HA, cloud VLM)
- **Determinism:** fixtures are versioned; recorded clips include checksum verification
- **Speed targets:**
  - integration tests: <60s for the whole suite
  - e2e tests: <10min for the nightly suite
  - any test >5s gets a `@pytest.mark.slow` decorator
- **CI:** integration tests run on every PR; e2e runs nightly + before releases

## Recorded fixtures

Replay-based tests (used heavily for feedback-driven optimization validation, §10.5) rely on recorded camera clips with known ground truth:

```
fixtures/clips/
├── mailman/                       (~20 clips with mailman arrivals)
├── dog-escape/                    (clips with real escapes + false alarms)
├── package-delivery/
├── unknown-visitor/
└── night-time/
```

Each clip is annotated with ground truth (`fixtures/clips/<scenario>/labels.json`) so optimization variants can be measured deterministically.

## Adding a new test

- Unit test? → put it in the service's own `tests/` folder
- Spans multiple services but no external deps? → `integration/`
- Requires real camera / HA / cloud VLM? → `e2e/` and tag `@pytest.mark.e2e`
- Needs a fixture? → add to `fixtures/` and document its provenance
