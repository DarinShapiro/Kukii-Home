# infrastructure/nats/

NATS JetStream stream + consumer configuration declared as YAML. See §03 for the architecture.

## Streams

| Stream     | Subjects                                                                                 | Retention      | Purpose                         |
| ---------- | ---------------------------------------------------------------------------------------- | -------------- | ------------------------------- |
| `EVENTS`   | `sensor.bypass`, `vlm.urgent`, `vlm.normal`, `vlm.background`                            | 24h, 1M msgs   | Hot-path camera/HA event triage |
| `ACTIONS`  | `action.notify`, `action.device`, `action.ask`                                           | 7d, 500K msgs  | Outbound dispatch               |
| `SESSIONS` | `session.opened`, `session.segment`, `session.closed`                                    | 30d, 200K msgs | Multi-camera session lifecycle  |
| `AUDIT`    | `audit.cloud_egress`, `audit.rule_fire`, `audit.rule_dismiss`, `audit.identity_decision` | 365d, 10M msgs | Audit trail (§16)               |

## Apply

The streams/consumers are applied at boot by `services/core` (or manually via `nats` CLI):

```bash
# Manual application
nats --server nats://localhost:4222 stream add EVENTS --config infrastructure/nats/streams.yaml
# (or programmatically — see services/core/src/kukiihome_core/jetstream_setup.py)
```

## Conventions

- **Stream names:** `UPPER_SNAKE`
- **Subject names:** `lowercase.dot.namespaced`
- **Consumer names:** `<role>` or `<tier>_<worker>` (e.g. `triage`, `vlm_urgent_worker`)

## Backpressure & load shedding (§03)

`max_ack_pending` controls in-flight per consumer; reaching it applies backpressure.
The triage worker downgrades event priority when upstream queue depth grows
(`vlm.urgent` → `vlm.normal` → `vlm.background`) per the load-shedding policy.
