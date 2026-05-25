# 19 — Failure Modes & Degradation

**Purpose:** What happens when something breaks — and what the system does instead of nothing (or worse, the wrong thing).
**Status:** drafting

---

## Principle: degrade, don't fail

When a component fails:

1. **Isolate** — don't let one failure cascade
2. **Log** — record what failed + when for diagnostics
3. **Alert** — notify operator if it's serious (not every transient glitch)
4. **Degrade** — reduce scope but keep operating
5. **Recover** — attempt restart/recovery if safe

**No silent failures.** If a critical path component fails, the system:
- Stops pretending to certainty (raises confidence gate)
- Notifies operator (health dashboard, optional push notification)
- Falls back to safe defaults (conservative action, human review)

---

## Failure mode inventory & degradation behavior

### F1: Camera offline (disconnects, crashes, reboots)

**Detection:** no RTSP frames for > 10s

**Immediate (0–10s):**
- Stop sampling frames from this camera
- Mark camera `status: offline`
- Flush any pending events from this camera from the queue

**In-flight sessions/events:**
- Sessions with multi-camera segments: continue (other cameras cover)
- Sessions from only this camera: hold open (subject may return to view)
- AttentionMode active on this camera: escalate alert ("pool camera lost while monitoring active")

**Recovery:**
- Watchdog tries reconnect every 5s for first 60s
- Then back off to 30s intervals
- On reconnect: validate stream (request keyframe, check metadata)
- Resume normally

**Degraded state:** other cameras still working; this area has reduced coverage

---

### F2: RTSP stutter / packet loss (temporary stream degradation)

**Detection:** frame arrival latency spike OR frame drops (sequence number jumps)

**Threshold:** < 5% loss = tolerate; 5–20% loss = warn operator; > 20% = treat as F1 (camera offline)

**Degraded behavior:**
- Continue processing available frames (don't wait for lost frames)
- Degrade frame budget for detector (use fewer frames if stream is choppy)
- Flag in enrichment output: `stream_quality: degraded`
- VLM gets flagged context: "frames may be delayed or skipped due to network"

**Recovery:** auto-corrects when stream stabilizes (back-off counter resets on first good frame sequence)

**User visibility:** "Camera network unstable" on health dashboard; no alerts if this is only issue

---

### F3: Agent DVR down (loss of recorded clips + snapshots)

**Detection:** DVR API timeout 5s for second consecutive request

**Immediate:**
- Mark DVR `status: offline`
- Triage worker: incoming events can still be detected + scored (DVR feeds the detection pipeline, but DVR is not on the hot path)
- VLM calls: proceed without clip context (frames still flowing from RTSP, just no archived reference)

**In-flight events:**
- Events already enriched: proceed (have detections + frames)
- Requests for old clips (> 30s old): fail with "DVR offline, clip unavailable"

**Memory impact:**
- New episodic records can't reference clips (store `clip_ref: null`)
- Session stitching (multi-frame montages) skipped
- Journey-close VLM call gets single frame instead of stitched clips

**Recovery:**
- Watchdog restart attempt every 30s
- On restart: check for hung processes, disk space, database corruption
- If database corrupt: fall back to ephemeral clips (recorded in memory, not persisted; lost on restart)

**Degraded state:** system operates as live-frame-only (no clip archive), works but loses forensic capability

---

### F4: Home Assistant down (device state unavailable)

**Detection:** HA agent MCP timeout 3s for two consecutive calls

**Immediate:**
- Mark HA `status: offline`
- Cache world-state snapshot (last known state becomes frozen)
- Stop polling HA for state changes (HA is down, polling is pointless)

**Dependent features:**
- World state context assembly: uses cached snapshot (gets stale)
- Device actions (lights, locks): all fail with policy error ("HA unavailable")
- Proactive planning agent: pauses (can't query calendar, weather, energy)
- HA-native alert synthesis (Thread network, add-on outputs): stops

**Continued operation:**
- Camera events still process (don't need HA)
- VLM still runs (context assembly degrades to partial)
- Rules still fire (pattern matching doesn't need HA)
- Notifications still work (notify MCP independent from HA MCP)

**Degradation in reasoning:**
```
Normal: "Alert if person at door AND alarm_armed"
  (check HA state, evaluate rule)

Degraded: "Can't confirm alarm state (HA offline), assume worst case"
  → If rule requires HA state, raise confidence threshold (be more conservative)
  → If rule is conditional on HA state, may not fire (lack of confirmation worse than false positive)
```

**Recovery:**
- Watchdog restarts HA (if SentiHome has permission)
- If HA won't restart: operator must handle out-of-band
- On reconnect: full re-sync (pull current state, not stale cache)

**User impact:** Lights can't turn on, locks can't engage, automations paused. Alerts still work but lack context.

---

### F5: Event bus (NATS JetStream) down

**Detection:** publisher rejects message with "no connection"; ack timeout after 5s

**Immediate:**
- Triage worker: backpressure on ingress (incoming events buffer but don't stall)
- VLM worker: stalls on waiting for work (no new events in queue)
- Action dispatcher: stalls (no events to dispatch)

**Incoming events:**
- DVR/camera webhooks: DVR's internal queue buffers (DVR can hold 100+ events locally)
- HA poller: messages queued in memory (up to 1000 events), if full: oldest events dropped
- Fast detector: synthetic events dropped (can't reach bus)

**Watchdog:**
- Detects bus down immediately (publisher failure)
- Attempts restart: `nats-server start` if running locally
- If remote bus: logs "bus unreachable, waiting for network recovery"
- Alerts operator: "event bus offline" (Tier 3 alert if critical systems waiting)

**Recovery:**
- NATS restart completes in < 5s typically
- Triage worker resumes processing buffered events
- VLM worker resumes accepting work
- No message loss if restart < backpressure buffer

**Degraded state:** Events may queue up; processing backlog after recovery; time-sensitive events may expire (Tier 1 safety events have 30s TTL)

---

### F6: Local GPU saturated (VLM task backlog growing)

**Detection:** VLM queue depth > 10 events for > 30s

**Immediate:**
- Load shedding activates (see §03):
  1. Dedup already enabled → drops more aggressively
  2. Frame budget downshift: 8 → 4 → 2 frames
  3. Background tasks paused (report generation, history mining)
  4. Tier 3 events still prioritized (never preempted)

**Degradation:**
- Tier 2 (push alert) events may experience latency (60s → 120s)
- Tier 1 (in-app) events silent (no VLM call)
- Detector-only fallback kicks in: use rule matching without VLM (deterministic, no confidence feedback)

**User visibility:** "GPU busy" on dashboard; some alerts may be brief/silent

**Recovery:**
- As queue clears, gradually un-throttle
- If sustained saturation (> 2min): suggest GPU upgrade or reduce camera count

---

### F7: Local VLM down (model crashes, OOM, bad weights)

**Detection:** VLM process exits; model inference times out 8s for second time

**Immediate:**
- Mark VLM backend `status: offline`
- Circuit breaker opens: don't retry VLM calls (fail fast, not slow)
- Try next backend (cloud fallback, if configured)

**If no other backends available:**
- Escalate to detector-only fallback (rules + deterministic matching)
- Flag all outputs: `vlm_available: false`
- Continue operation (reduced capability but not stopped)

**Recovery:**
- Watchdog restarts model: `ollama pull model_name && ollama serve`
- Reload model weights
- Sanity test: run inference on test image
- If test passes: re-enable (circuit breaker resets)

**User impact:** All alerts become "rule match" instead of "VLM confidence + rule"; less nuance

---

### F8: Internet down (cloud backends unreachable)

**Detection:** HTTP requests to cloud services timeout (5s), DNS resolves but connection refused

**Immediate:**
- Mark all cloud backends `status: offline`
- Local inference unaffected (continues normally)
- Disable cloud VLM fallback (if configured)

**Queued data:**
- Attempt to upload later (episodic summaries, backups) queued for retry
- Raw clips: stay local only (already local-first default)
- Notifications: stay on LAN (push to local app only)

**Recovery:**
- Watchdog monitors internet connectivity (ping cloudflare.com or similar)
- On restore: flush queue (send buffered uploads)

**User impact:** None (system designed to work offline; cloud is optional)

---

### F9: Memory pressure (Vector DB or SQL running out of space / RAM)

**Detection:** SQL insert fails with "disk full" OR OOM errors from Vector DB

**Vector DB (embeddings):**
- Auto-truncate: delete oldest unknown-face embeddings (30-day retention anyway)
- If still full: stop accepting new embeddings (new rules/faces auto-reject until space)
- Queries still work (read-only)

**SQL (sessions, rules, logs):**
- Auto-archive: move old closed sessions to cold storage (archive.db)
- If still full: stop accepting new session writes (new sessions can't open; severity escalates)
- Queries of recent data still work

**Alert:** "Storage full" (Tier 2, operator must add disk or delete old data)

**Recovery:**
- Operator adds storage (NAS expansion, bigger SSD)
- Manual cleanup: delete oldest archived sessions, expired intents, etc.

---

### F10: Power loss / restart (process crash or hardware reboot)

**Detection:** N/A (no detection needed; happens)

**Recovery on boot:**

```
Startup sequence:
  1. Check database integrity (SQL, Vector DB)
    → If corrupted: restore from backup (if available)
  2. Restore hot state:
    → Active sessions from SQL (in-memory cache repopulated)
    → Active AttentionModes (resume monitoring from §08 state)
  3. Resume polling:
    → HA poller restarts
    → Camera connections re-establish
  4. Resume worker pool (triage, VLM)
    → Catch up on queued events
  5. Health check: all backends online?
    → Alert if any are offline
```

**Data preservation:**
- Sessions: durable in SQL, resume from last committed segment
- Episodic records: committed, preserved
- Raw event log: append-only, survives
- In-memory caches: lost (small cost, repopulated quickly)
- Active TransientIntents: re-queried from SQL on boot

**User experience:** Brief outage (< 30s); system resumes; user sees "system recovered from restart" in app

---

## Safe defaults matrix

For each major failure mode, this table defines what actions are still auto-allowed:

| Failure | Lights OK? | Notifications OK? | Lock OK? | Unlock OK? | Siren OK? | Speaker OK? |
|---------|-----------|------------------|---------|-----------|----------|------------|
| Camera offline | ✓ | ✓ | ✗ (need visual context) | ✗ | ✗ | ✓ |
| DVR down | ✓ | ✓ | ✗ | ✗ | ✗ | ✓ |
| HA down | ✗ (can't control) | ✓ | ✗ | ✗ | ✗ | ✗ |
| Bus down | ✗ (no dispatch) | ✗ (can't queue) | ✗ | ✗ | ✗ | ✗ |
| GPU saturated | ✓ | ✓ (simplified) | conditional (rule-only) | ✗ | ✗ | ✓ |
| VLM down | ✓ | ✓ (rule-only) | conditional (rule-only) | ✗ | ✗ | ✓ |
| Internet down | ✓ | ✓ (local only) | ✓ | ✓ | ✓ | ✓ |

**Legend:**
- ✓ = allowed (full capability)
- ✗ = blocked (can't execute safely)
- conditional = allowed if rule pre-authorizes, else ask

---

## User-visible health surface

### Health dashboard (in-app)

```
Status: Healthy / Degraded / Critical

Components:
  ✓ Cameras: 4/4 online (doorbell, driveway, backyard, interior)
  ✓ Home Assistant: online
  ✓ GPU: 67% utilization, VLM queue depth 2
  ✓ Storage: 340GB / 500GB used
  ⚠ Internet: 850ms latency (slow but ok)
  
Last issues:
  3h ago — Camera "interior" offline for 45s (recovered)
  1d ago — Database query slow (took 3s; normal < 200ms)
```

### Alerts for operator

Shown as in-app notification + optional push:

- **Critical:** Bus down, HA down for > 60s, storage full
- **Warning:** Camera offline > 30s, GPU saturation, VLM latency > 8s
- **Info:** Camera reconnected, VLM model updated, new rule added

### Logging & diagnostics

Every failure logged with:
- Timestamp
- Component + error message
- Duration (if intermittent)
- Impact (what degraded)
- Recovery action (what the system did)

Example:
```
2026-05-23T14:33:22Z [ERROR] HA connection timeout after 3s; marking offline
2026-05-23T14:33:22Z [WARN] World state using cached snapshot from 14:30:00 (3min stale)
2026-05-23T14:33:30Z [INFO] HA reconnected; pulling fresh state
2026-05-23T14:33:32Z [INFO] World state synchronized; resume normal operation
```

Queryable via: `diagnostics.log` file + in-app logs UI (last 100 entries)
