# Markup Efficacy Harness (v0)

**Question this answers**: when we hand a VLM a frame for grounding, does
drawing pixel-burned bounding boxes around recognized entities (Alice,
Rex, Bob's truck) actually *improve* its reasoning compared to handing
it the same raw frame plus a JSON metadata block describing the same
entities in text?

We assumed yes. This harness empirically tests that.

## Architecture

For each **fixture** (a captured camera frame with ground-truth labels),
we generate **two variants** suitable as a VLM prompt:

| Variant | What VLM sees |
|---|---|
| `raw_with_metadata` | Raw JPEG + text describing identified entities ("Alice at bbox (0.2, 0.3, 0.4, 0.8) via face recognition, confidence 0.92") |
| `annotated_with_metadata` | Same JPEG with our markup pipeline applied (green/yellow boxes around the same entities) + the same text description |

The third hypothetical variant (raw + no metadata) is omitted from v0
because we always have *some* JSON to pass — we'd never deploy
zero-grounding to the VLM. The interesting question is whether the
pixel-burned channel adds value on top of the JSON channel.

We run each `(fixture × variant × question)` through Claude via the
Anthropic SDK, score the response against the ground truth, and
produce a delta report.

## What outcome changes the architecture

* **Annotated ≫ Raw**: confirms current design. Continue burning
  identity markups into pixels.
* **Annotated ≈ Raw**: simplify. Stop rendering annotated JPEGs; pass
  JSON metadata only. Saves the OpenCV draw cost + the annotation
  cache storage. *Object detection (YOLO) still runs — for gating /
  dismissal-policy routing / memory grounding — but its output never
  hits the pixels.*
* **Annotated < Raw**: markup is actively confusing the VLM. Drop the
  whole markup pipeline. Big simplification.

## Running the harness

Requires `ANTHROPIC_API_KEY` set in the environment.

```bash
# (1) Capture fresh frames from configured cameras into fixtures/.
#     Connects to whatever preprocessor is running on localhost:8090
#     and grabs the most-recent buffered frame from each camera.
python eval/markup_efficacy/harness.py capture --camera dahua_test
python eval/markup_efficacy/harness.py capture --camera reolink_front

# (2) Hand-label fixtures/<frame_id>.yaml with ground-truth entities.
#     See fixtures/EXAMPLE.yaml for the schema.

# (3) Render the annotated variants from the labels.
python eval/markup_efficacy/harness.py render

# (4) Run all (variant × question) pairs through Claude. Caches
#     responses so re-runs are free.
python eval/markup_efficacy/harness.py run --model claude-opus-4-5

# (5) Produce the comparison report.
python eval/markup_efficacy/harness.py report
```

## Cost budget

* 10 fixtures × 2 variants × 5 questions = **100 VLM calls**
* Claude Opus 4.5 at ~$0.015 per call (small frame + short prompt) ≈
  **$1.50 per full run**
* Response cache keys on `(fixture_id, variant, question_id, model)`
  so repeated runs of identical inputs are free.

## Where this becomes the VLM dev loop (#49)

The same harness shape is what task #49 needs:

* Fixtures + ground truth = the eval corpus
* Variants = "before / after" the change being tested (prompt, model,
  pipeline knob, …)
* Question battery + scoring = the regression signal

So this harness is bootstrap infrastructure, not a one-off.
