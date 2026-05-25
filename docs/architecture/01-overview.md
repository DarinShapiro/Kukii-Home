# 01 — Overview & Goals

**Purpose:** What SentiHome is, who it's for, and the constraints that shape every other decision.
**Status:** drafting

---

## Vision

**SentiHome** is a home AI system that watches camera streams and Home Assistant device state to detect what matters, remember patterns, and act intelligently — without constant false alarms and without invading privacy.

Core idea: Turn raw sensor feeds into _trusted intelligence_ by combining:

- **Visual reasoning** (understanding what cameras see)
- **Behavioral memory** (learning who's who, what's normal)
- **Home automation integration** (knowing what devices can do and executing safely)
- **Explicit user control** (rules are written by the user, not discovered in a blackbox)

Unlike cloud security cameras (requires subscription, privacy trade-off) or dumb motion sensors (constant false alarms), SentiHome lives on the LAN, learns the home's specific patterns, and keeps raw data local by default.

---

## Primary users & scenarios

### Who uses SentiHome

- **Homeowners** (primary) — single family, multiple residents, multi-generational
- **Rental properties** — property managers wanting visibility without landlord-tenant friction
- **Small businesses** — cafés, studios, or offices wanting security tied to Home Assistant
- **Technical users** (required) — comfortable with HA, willing to calibrate zones/cameras, write rules

Not for: non-technical users, renters without LAN access, those demanding zero-config simplicity.

### Canonical scenarios

| Scenario                             | SentiHome advantage                                                                                                       |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------- |
| **Pool safety (S15, S20)**           | Continuous monitoring + immediate drowning detection (< 15s). Not safe to rely on periodic human checks.                  |
| **Unexpected visitor (S1)**          | Knows which residents are home, whether guest is expected, whether face matches known profiles. Explains why alert fired. |
| **Package theft prevention (S8)**    | Detects package delivery + dwell time (package sits 30min = likely stolen). Human verification via app.                   |
| **Dog escape (S16)**                 | Recognizes pet + detects in unauthorized area + triggers alert before they're hit by car. Critical for family safety.     |
| **Repeated unwanted visitors (S17)** | Tracks visit frequency + pattern over weeks. Distinguishes "persistent annoyance" from "potential threat."                |
| **Security at night (S18, S2)**      | Unknown person at night outside quiet hours → escalate faster than daytime. Respects sleep but acts urgently.             |
| **Elderly fall detection**           | Slow-motion alert for elderly parent motionless on floor. Alert goes to children, not emergency services immediately.     |

---

## Non-goals

**SentiHome does NOT:**

- Replace professional security systems (no armed/disarmed state, no 24/7 monitoring service, no legal liability shielding)
- Provide real-time threat detection at CCTV quality (local inference trades accuracy for privacy & latency)
- Work on public clouds exclusively (philosophy is local-first; cloud is optional emergency fallback only)
- Compete with Ring/Nest on convenience (no phone number recognition, no "unknown person at 3am" without app; Siri shortcuts optional)
- Handle multi-tenancy (philosophy is one household per system, simple trust model)
- Guarantee 100% detection (no system catches everything; graceful degradation is the goal, not certainty)
- Replace human judgment (VLM confidence scores are tools for the user, not automation directives)

---

## Design principles

### 1. Local-first

**Everything that can run locally does.**

- Cameras → Agent DVR continuous recording (on local NAS/server)
- Frame inference → local GPU (fast detector, VLM on Ollama/local LLM)
- Memory → local SQL + vector DB (not cloud SaaS)
- Device actions → direct HA MCP (no cloud round-trip latency)

**When cloud is used (optional):**

- VLM inference if local GPU saturated (explicit user permission)
- Backup / disaster recovery (encrypted, user-controlled)
- Advanced analytics (historical pattern queries, reports)

**User can audit:** every inference, every decision, every data retention decision is logged + visible.

### 2. Explainable decisions

**The user always knows why an alert fired.**

- Every alert cites which rules matched and why
- Confidence scores are transparent (0.87 → user knows it's good but not certain)
- Limiting factors are surfaced ("face detected but oblique angle, confidence reduced")
- History is queryable ("show me all alerts from this camera in the last week")
- Rules are human-editable (not discovered from black-box training)

**Consequence:** complexity is pushed to upfront rule definition, not hidden in a model.

### 3. Low false-positive cost

**A false alarm is worse than a missed real event.**

This is the inverse of most security products. A Ring doorbell at high sensitivity sends 20 false alerts for every real doorbell press. SentiHome prefers silence to noise — the cost of an unnecessary alert (annoyance, trust erosion) outweighs the benefit of catching everything.

**Mechanisms:**

- High confidence thresholds on the hot path (0.90+ for Tier 2+ alerts)
- Conversational confirmation for borderline events ("Was that Sarah?")
- Graduated escalation (silent log → in-app → push → wake → siren) with multiple exit ramps
- Rule authoring explicitly tunes confidence vs. recall per area
- Dismissal feedback trains thresholds (user feedback is gold)

### 4. Graceful degradation

**The system works better with more pieces, but doesn't break when pieces are missing.**

- No GPU? → use CPU-based detector, slower but works
- No second camera? → single-camera zones, skip stereo
- No calibration? → work in image-space zones only (lose height/distance reasoning)
- Cloud VLM unavailable? → escalate to higher confidence thresholds locally, or use cloud fallback, or take conservative action + notify
- LLM down? → deterministic rule matching only (no VLM reasoning)

**Philosophy:** Missing capability = reduced scope, not failure. Operator knows what degrades and can make explicit trade-offs.

### 5. Separation of concerns

**Four clear boundaries:**

1. **Observation** (VLM): reports what it sees — faces, poses, objects, behavior. Does not prescribe actions.
2. **Memory** (rules + contexts + intents): normalizes observations into meaningful patterns. Does not execute.
3. **Reasoning** (dispatcher): applies memory to observations → decides action. Does not access HA directly.
4. **Execution** (HA MCP): executes actions. Does not decide (policy gates handle hard decisions).

**Each piece can be swapped independently:** change VLM model, change rule engine, change dispatch policy, change HA device config — without cascading rewrites.

### 6. Privacy by design

- Resident embeddings never leave LAN unless explicitly enabled
- Unknown faces auto-delete after 30 days
- Clips are local-only by default; cloud archive is user opt-in
- Rules are written by the user, not trained on their data
- No usage telemetry sent back to vendor

---

## Success criteria

### Technical

- **Latency (hot path):** 1–2 VLM calls per event, < 12s end-to-end (from trigger to decision)
- **Accuracy:** < 10% false-positive rate on high-confidence alerts (Tier 2+); < 5% false-negative rate on critical events (pool, entry)
- **Uptime:** 99%+ (consumer-grade hardware, graceful degradation on failures)
- **Scalability:** 4–8 cameras, 2–3 concurrent VLM calls, single GPU slot, HA integration for 20+ entities
- **Memory efficiency:** episodic + identity + rules fit in 16GB RAM + 500GB SSD

### User-facing

- **Trust:** users understand why each alert fired and can edit rules without re-training models
- **False-alarm fatigue:** < 2 dismissed alerts per 10 valid alerts (high SNR)
- **Adoption:** tech-forward homeowners can set up and maintain system independently in < 2 hours
- **Utility:** user-defined scenarios (pool, package theft, elderly fall, unexpected visitor) work as designed 90%+ of the time

### Privacy & governance

- **Local operation:** raw video never leaves home (unless explicitly opted into cloud fallback)
- **Audit trail:** every inference, rule fire, dismissal, action logged and queryable
- **User control:** user can delete all memories, disable recording, audit data retention
- **Transparency:** all retention policies documented and configurable

---

## Key insight: VLM as a reasoning tool, not automation

SentiHome uses vision models to _understand_ what's in frames, but the system treats VLM outputs as _information for the user_, not _directives for action_.

```
Traditional security camera:
  Motion detected → alert fired → user checks camera
  (low sensitivity = misses important, high sensitivity = noise)

Ring / Nest model:
  Video analyzed → ML model guesses "person" or "package" → alert
  (user trusts model or dismisses)

SentiHome model:
  Video analyzed → VLM reports: "person at door, face 0.75 confidence,
  behavior: standing still, time: 22:00 (unusual), rules fired: [guest_alert]"
  User sees context → decides alert tier based on their rules + confidence
  (users decide; model informs)
```

The goal is not autonomous agents that lock doors and call police. The goal is a **smart assistant that knows your home well enough to ask the right questions at the right time.**

---

## Glossary

| **Term**                 | **Definition**                                                                                                                                |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| **AttentionMode**        | Sustained high-cadence monitoring for life-safety (e.g., "pool occupied" → 4fps continuous). Bypasses normal event queuing.                   |
| **Episodic memory**      | Curated, significant records of past sessions/events. Queryable by SQL (structured) or semantic search (vector).                              |
| **Gallery entry**        | Raw biometric data (face embedding, plate, pet face). May be linked to a KnownActor.                                                          |
| **Home Assistant (HA)**  | Smart home hub running automations, integrations, AI add-ons. SentiHome's source of truth for device state.                                   |
| **Identity resolution**  | Probabilistic claim about who a detected person/vehicle is. Includes confidence + evidence sources + alternatives.                            |
| **KnownActor**           | A recognized entity (resident, service worker, pet, vehicle) with access profiles, behavioral models, and visit history.                      |
| **MemoryMCP**            | MCP server providing read/write access to all memory layers (rules, sessions, episodic, identity).                                            |
| **MCP**                  | Model Context Protocol. RPC interface to external services (HA agent, DVR, detector, memory, notifications).                                  |
| **PTZ**                  | Pan-tilt-zoom camera. Can move and zoom; treated as virtual static cameras at each preset position.                                           |
| **Remediation registry** | Deterministic lookup table: (confidence_limiting_factor + area_resource) → action (turn on lights, slew PTZ).                                 |
| **SituationalContext**   | Temporal world knowledge that reframes reasoning (e.g., "Halloween trick-or-treat tonight" changes what's suspicious).                        |
| **TransientIntent**      | User-expressed, short-lived watch (e.g., "notify when Bob arrives"). Self-expires; fire-once semantics.                                       |
| **VLM**                  | Vision language model. Takes annotated frames + context + rules → outputs structured decision (criticality, confidence, rules_fired, action). |
| **VLM prompt contract**  | Standardized input format (image budget, prompt caching, context structure) and output schema (decision JSON).                                |
| **World frame**          | Shared coordinate system (origin, axes, units) for all spatial reasoning. Cameras + zones registered relative to this frame.                  |
| **Zone**                 | Precise spatial region — either 2D (image-space polygon) or 3D (world-space volume). Tied to a camera or set of cameras.                      |
| **Journey / session**    | Multi-camera tracking of a subject from entry to exit. Accumulates segments, journey_score, and rules_fired over time.                        |
