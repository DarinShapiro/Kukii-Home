# Validation Pass 1 — smoke test on the two live cameras

**Goal:** prove the pipeline + feedback loop work end-to-end on _real_ footage
from both cameras, at smoke-test depth. Not a full separability matrix —
just enough to confirm each link in the chain is wired and learning.

**Cameras under test**

| Id         | Hardware               | View                            | Geometry note                                                       |
| ---------- | ---------------------- | ------------------------------- | ------------------------------------------------------------------- |
| `pool`     | Dahua (192.168.68.89)  | pool / backyard, steep top-down | Face usually fails here → the camera that should prove gait/CC-ReID |
| `driveway` | Reolink (192.168.1.20) | driveway / front approach       | Face-favorable; the classic security scenarios (S1/S2/S3/S17)       |

> The driveway RTSP URL/creds aren't in the repo yet (only the pool cam is
> hardcoded in `capture_corpus.py`). **First action below captures it.**

---

## Track A — Recognition eval (offline, corpus-based)

Answers: _does recognition actually discriminate on each camera's geometry?_
Uses the existing harness; produces replayable AUC/EER/d-prime numbers.

**A1. Capture labeled clips — 1 known subject, both cameras.**
Per camera, capture short dense-fps clips with controlled-axis labels.
Minimum smoke set (≈6 clips):

```
# Driveway (face-favorable) — subject "darin", day
python scripts/dev/capture_corpus.py --rtsp <DRIVEWAY_RTSP> \
  --name drive_darin_day_outfitA_walk --camera driveway \
  --subject darin --outfit outfitA --lighting day --activity approach --seconds 12

# same subject, second outfit (tests CC-ReID clothes-invariance)
... --name drive_darin_day_outfitB_walk --outfit outfitB ...

# Pool cam (steep top-down) — same subject, dense walk for GAIT
python scripts/dev/capture_corpus.py            # already defaults to pool cam
  --name pool_darin_day_outfitA_walk --camera dahuapoolcam \
  --subject darin --outfit outfitA --activity walk --seconds 15 --stream sub
```

For an **imposter baseline** (required for separability to mean anything),
capture ≥1 clip of a _second_ subject per camera (`--subject guest`). With
one subject the harness honestly reports "separability untestable."

**A2. Run the eval harness per camera + per model.**

```
# Face/body discrimination on the driveway
uv run --project services/preprocessor python scripts/dev/eval_identity.py \
  --model osnet  --camera driveway
uv run --project services/preprocessor python scripts/dev/eval_identity.py \
  --model ccreid --camera driveway          # clothes-invariant
uv run --project services/preprocessor python scripts/dev/eval_identity.py \
  --model ccreid --camera dahuapoolcam       # the face-fail camera

# Gait on the pool cam (needs the dense walk clips)
uv run --project services/preprocessor python scripts/dev/gait_probe.py \
  --corpus face_debug/corpus --min-frames 20
```

**A3. Read the numbers.** Expected/interesting outcomes:

- Driveway: face should be strong; CC-ReID should hold across outfitA↔outfitB
  (cross-outfit genuine sim stays high) where OSNet drops.
- Pool cam: face weak/absent; gait + CC-ReID are the anchors — this is the
  whole point of the durable-modality work. Record the d-prime so later
  threshold tuning has a baseline.

**Exit criteria (A):** a separability number per (camera, model), and a
clear statement of which modality carries each camera.

---

## Track B — Pipeline + feedback (live, event-driven)

Answers: _does motion → enrich → reason → notify → feedback → store actually
work and record the learning signal?_ Synthetic first (de-risk), then real.

**B0. Pick the smoke rules (2–3 total).** Match each camera's realistic view:

| Rule         | Camera   | Scenario | Output         | Why it's a good smoke test                                         |
| ------------ | -------- | -------- | -------------- | ------------------------------------------------------------------ |
| R1           | driveway | S3       | `notify`       | Known resident arrives — exercises identity → low alert            |
| R2           | driveway | S1       | `urgent_alert` | Unknown person at approach — exercises the alert path              |
| R3 (stretch) | pool     | S8-lite  | `urgent_alert` | Person in pool area — exercises the pool cam + AttentionMode-style |

Start with **R1 + R2** (driveway only); add R3 if the first two pass clean.

**B1. Author the rules.** Via the conversational rule path / default pack
(Epic #105). Keep them minimal and explicit (area + time + identity
condition) so the firing is unambiguous to verify.

**B2. Synthetic round-trip (per camera).** Use the add-on's **Send test
alert** button:

1. Click it for each camera → captures a real snapshot, records a `[TEST]`
   alert, fires the notifier.
2. Confirm: push lands on phone → tap → per-alert page renders with the
   snapshot.
3. Exercise feedback: tap ✓ (good catch) on one, ✗ (false alarm) on
   another.
4. Confirm the feedback persisted: `GET /alert/<id>` shows the recorded
   feedback; the EventStore per-event dir has it.

**B3. Real-motion round-trip (per camera).**

1. Walk in front of the driveway cam (unknown → should trend toward R2);
   then again after enrolling yourself (known → R1).
2. Confirm the full chain via the add-on status page + `/logs`:
   `motion → snapshot → (preprocessor enrich if inference box reachable) →
reasoning decision → notify`.
3. Repeat the ✓/✗ feedback on the real alert; confirm it lands.

**B4. Verify the learning signal.** After a ✗ (false alarm) on a real
alert, confirm the dismissal/feedback is captured as the signal the
weight machinery consumes (citation-weight delta / dismissal policy
candidate). This is the "system gets smarter from being wrong" check —
even if the full Neo4j weight update isn't wired, the _signal_ must be
recorded for the feedback loop.

**Exit criteria (B):** one synthetic + one real feedback round-trip per
camera, with the feedback verifiably stored, and the reasoning decision
visible per event.

---

## What I need from you to start

1. **Driveway RTSP URL + creds** (user/pass/path) — to capture from it and,
   if not already, to confirm it's the camera HA discovered.
2. **Inference box reachable?** Is `KUKIIHOME_PREPROCESSOR_URL` set and the
   preprocessor running? If not, Track B still works (alerts fire with HA
   snapshot + rule) but skips the recognition-enrichment leg — note which
   mode we're testing.
3. **A second person** for the imposter baseline in Track A (even one short
   clip), else separability is directional only.

## Sequencing

Smoke order: **A1 (driveway only, 2 clips) → B2 (synthetic, both cams) →
B3 (real, driveway) → A2/A3 (eval the clips) → B4.** Pool-cam gait (A1
pool + gait_probe) and R3 are the second lap once the driveway loop is
green end-to-end.
