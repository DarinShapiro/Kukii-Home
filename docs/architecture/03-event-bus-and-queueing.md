# 03 — Event Bus & Queueing

**Purpose:** How events flow from triggers to workers, how the system stays responsive under load, and how it sheds work when the VLM is the bottleneck.
**Status:** drafting

---

## Goals & constraints

- VLM is the scarce resource — design around it
- Bursty multi-camera concurrency must not collapse latency for urgent events
- HA state is a polled snapshot, not a pushed event stream
- Tier-1 safety events (smoke, CO, flood) bypass the VLM queue entirely
- Load shedding must be graceful — degraded quality preferred over dropped urgent alerts

---

## Ingress sources

Two distinct ingress mechanisms feed the bus. Camera-side events come from whatever frame source the user has — service-mode adapter, built-in adapter (Frigate), native adapter, or direct RTSP. The bus and downstream pipeline do not care which.

```
Camera events (push)                HA state changes (poll → synthetic)
─────────────────────               ──────────────────────────────────
On-camera AI events                 Scheduled poller queries HA agent:
  (via NVR adapter — §03.5)           ha.get_changes(since_ts)
Motion events                         ha.query("anything needing attention?")
  (NVR or preprocessor service)     Diff → synthetic state-change events
ONVIF events normalized by HA       HA-native AI alerts surfaced here
Fast detector synthetic events        (Thread network, add-on outputs, etc.)
```

Camera-side events are time-sensitive and push-capable — they use their native mechanisms (NVR webhooks, MQTT from Frigate, ONVIF events, on-camera AI signals). The NVR Adapter Layer (§03.5) normalizes them into the unified event schema before they hit the bus. HA state is eventually consistent — polling is more reliable than webhooks and requires no HA automations.

**Frame source is decoupled from the pipeline.** Whether frames come from Agent DVR, Frigate, Blue Iris, direct RTSP, or no NVR at all, the bus sees the same normalized event. Downstream workers don't know (and don't need to know) which adapter produced the event.

---

## HA polling strategy

Different entity groups are polled at different cadences based on alert urgency and state change frequency:

| Group | Entities | Cadence | Mechanism |
|-------|----------|---------|-----------|
| Safety-critical | Smoke, CO, flood, alarm state | 15s | `ha.get_changes` — targeted entity list |
| Security | Locks, doors, windows, garage, gates | 30s | `ha.get_changes` — targeted entity list |
| Presence | Who's home, occupancy sensors | 60s | `ha.get_changes` |
| Ecosystem sweep | HA-native alerts, repairs, integration health | 60s | `ha.query("anything needing attention?")` — LLM-backed |
| Context | Calendar, weather, scenes, modes | 5 min | `ha.get_changes` |
| Inventory | All other entity states | 5 min | `ha.get_snapshot()` — full refresh |

The ecosystem sweep cadence (60s) uses the LLM-backed `ha.query()` to surface HA-native AI findings (Thread network assessments, integration-generated alerts, repair completions) as synthetic events. The poller doesn't need to understand Thread networking or any specific HA add-on — the HA agent's LLM layer translates.

The world state cache (used for VLM context assembly) is updated on every inventory poll. VLM calls never hit HA directly — they read the cache.

### Synthetic event structure

State-change events from the poller use the same schema as camera events (see §05), with no frames:

```json
{
  "event_id": "ha-poll-{hash}",
  "source": "ha_poller",
  "source_kind": "sensor | lock | presence | ecosystem_alert | ...",
  "entity_id": "lock.back_door",
  "area_id": "back_door",
  "ts": "...",
  "previous_state": "locked",
  "current_state": "unlocked",
  "privacy_tier": "local_only",
  "clip_uri": null,
  "world_state_snapshot_ref": "..."
}
```

---

## Bus topology

```
Camera events ──► [ingress.camera]  ──┐
HA synthetic  ──► [ingress.ha]      ──┤
                                      ▼
                             [Triage worker pool]
                             - dedup
                             - coalesce into sessions
                             - priority scoring
                             - Tier-1 safety bypass
                                      │
              ┌───────────────────────┼──────────────────────┐
              ▼                       ▼                       ▼
     [sensor.bypass]         [vlm.urgent]              [vlm.normal]
     (no VLM — direct        highest priority           standard events
      action dispatch)       < 10s SLA
                                                    [vlm.background]
                                                    pattern mining,
                                                    report generation
              └───────────────────────┴──────────────────────┘
                                      │
                             [VLM worker pool]
                             (N = GPU slots)
                                      │
                             [action dispatch]
```

---

## Triage worker

The triage worker is deterministic — no LLM. It runs fast and cheap.

### Dedup

Same camera + same track_id + scene-hash similar within N seconds → drop or extend the existing event window. Kills the majority of the burst from a loitering subject.

Idempotency key: `SHA256(source + camera_id + round(ts, 5s) + track_id_primary)`

### Tier-1 safety bypass

Smoke, CO, flood, glass break, panic button — routed directly to `sensor.bypass`, which goes straight to action dispatch without touching the VLM queue. These events carry their own certainty; LLM reasoning adds latency, not value.

### Session coalesce

If an open session exists for the same subject (re-ID similarity ≥ threshold + spatial plausibility + recency window), append to session rather than creating a new VLM event. The session manager decides whether a new VLM pass is warranted for this segment or if detector-only enrichment is sufficient.

### Priority scoring

```
base_score = trigger_kind_weight[onvif | synthetic | fast_detector]
           + area_sensitivity_weight[interior | perimeter | public]
           + time_weight[quiet_hours | daytime]

boost = +3 if active TransientIntent matches this event
        +2 if alarm_armed
        +2 if subject unknown + night
        +1 if open session with elevated journey_score
        -1 if known_actor within access profile window

final_tier = urgent  if score ≥ 8
             normal  if score 4–7
             background if score < 4
```

---

## Load-shedding levers (ordered by preference)

1. **Dedup** — same subject, same scene, drop duplicate events. Eliminates most of the burst.
2. **Session coalesce** — new segment folded into existing session; VLM not necessarily re-run.
3. **Frame-budget down-shift** — under load, reduce from 8 frames → 4 → 2 + 1 crop before dropping. Degrades gracefully.
4. **Priority preemption** — `vlm.urgent` always jumps the queue. `vlm.background` paused when `vlm.normal` is deep.
5. **Detector-only fallback** — when all VLM backends saturated, emit enrichment JSON through structured rule matching only (no VLM reasoning). Events are logged with `vlm_skipped: true` for potential replay. This is distinct from the two-step fallback for weaker backends (§04) — here no VLM call is made at all due to capacity, not model capability.
6. **Backpressure to triggers** — if `vlm.normal` depth > threshold for > 60s, drop new `vlm.background` events entirely. Log drops to ops. Never drop `vlm.urgent`.

---

## Idempotency & TTLs

Every event carries an idempotency key. Redelivered events (NATS redelivery on worker crash) are deduped by the triage worker using the same key.

Per-event TTLs — stale work is worse than no work:

| Queue | TTL | Rationale |
|-------|-----|-----------|
| `sensor.bypass` | 30s | Safety alert must be acted on immediately or not at all |
| `vlm.urgent` | 90s | "Someone at the door" is useless 2 minutes later |
| `vlm.normal` | 5 min | Delivery confirmation can wait briefly |
| `vlm.background` | 1 hour | Pattern mining, report generation |

Expired events are logged with `expired: true` and event_id retained for audit.

---

## Concurrency caps

- Per-camera: max 2 concurrent VLM events (prevents a flapping camera from consuming all slots)
- Per-session: max 1 concurrent VLM event (avoids race conditions on session state)
- Per-backend: max concurrency declared in backend registry (§04)

---

## Tech selection

**NATS JetStream** — recommended.

- Work-queue consumer semantics with redelivery on worker crash
- Stream subjects map directly to the tiered queue topology
- Lightweight — runs on the HA host or the processing host
- Per-subject TTLs and max-age policies supported natively
- Consumer groups for the VLM worker pool

Alternatives considered:
- **Redis Streams** — viable if Redis is already in the stack; consumer groups work well; TTL per-message less clean
- **RabbitMQ** — priority queues first-class, heavier ops burden
- **MQTT alone** — HA's native protocol; no work-queue or replay semantics; use as ingress protocol only, bridge into NATS

MQTT bridges the ingress gap: HA-side components publish to MQTT (native), a lightweight bridge promotes to NATS JetStream for all queue semantics downstream.

---

## Failure & redelivery semantics

- Worker crashes mid-processing: NATS redelivers after ack timeout; idempotency key prevents double-processing
- Bus down: camera events buffer in DVR webhook retry queue (DVR-side); HA poller misses poll cycles (acceptable — state is eventually consistent); events lost during bus downtime are not replayed (TTL-bounded anyway)
- Worker pool exhausted: events queue up; TTLs expire stale ones; load-shedding levers activate
- NATS itself down: watchdog (§21) alerts immediately; system enters degraded mode

---

## Observability hooks

Metrics emitted per event (see §17 for full observability spec):

- Queue depth per tier (p50, p99, max)
- Triage decision distribution (dedup rate, coalesce rate, tier assignment)
- Event TTL expiry count by tier and reason
- VLM worker utilisation per backend
- Load-shedding activations by lever
- HA poller: last successful poll per group, diff size, synthetic events emitted
- Ecosystem sweep: HA-native alerts surfaced, significance flags
