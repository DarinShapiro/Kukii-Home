# Validation findings — live two-camera bring-up (2026-06-01)

Empirical results from running the real pipeline + models against a live
corpus captured on the maintainer's two cameras (Dahua pool cam, Reolink
Duo 3 driveway). Single subject (`darin`) so far — see caveats.

## Corpus

| clip                     | cam             | outfit        | frames | res     |
| ------------------------ | --------------- | ------------- | ------ | ------- |
| pool_darin_1101_main     | pool (top-down) | A             | 2098   | 4K      |
| pool_darin_1626_outfitB  | pool            | B (shirt+hat) | 3555   | 4K      |
| drive_darin_1101_main    | driveway        | A             | 122    | 8K-wide |
| drive_darin_1626_outfitB | driveway        | B             | 57     | 8K-wide |

(`C:/Users/darin_jwxgczt/Kukii-Home/face_debug/corpus/`)

## What's validated ✅

- **Detection** (YOLO11x) on real footage: `person 0.86` on the pool deck,
  `car 0.95` on the driveway. Solid.
- **Face fails on the pool cam** — top-down + distance → "no face detected"
  on every attempted enrollment frame. This is the designed-for case that
  motivates the durable body modalities; confirmed, not assumed.
- **Body enrollment + recognition end-to-end** via OSNet: enrolled `darin`
  from auto-cropped person frames → `ActorCache` (`actors_cached: 1`) →
  held-out same-pass frames matched at **0.79–0.85** cosine.
- **The decode→queue→workers capture rework** (committed `7b0448a`):
  proven on live streams; 8K ceiling pinned to the decode stage (NVDEC
  territory), not the pipeline.

## The CC-ReID problem — four independent measurements ⚠️

CC-ReID (`ccreid_cal_ltcc.onnx`), the model that is _supposed_ to be the
durable, clothes-invariant body anchor (weighted **0.85** in fusion, above
OSNet's 0.6), **underperforms OSNet in every test run on this footage:**

| Test                                 | OSNet     | CC-ReID              | DINOv2 |
| ------------------------------------ | --------- | -------------------- | ------ |
| Same-pass self-sim (pool, outfit A)  | 0.79–0.85 | 0.63–0.81            | —      |
| Cross-pass self-sim (pool, outfit A) | 0.60–0.64 | 0.50–0.55            | —      |
| **Cross-OUTFIT (A→B), run 1**        | **0.79**  | **0.54** (MISS <0.6) | —      |
| **Cross-OUTFIT (A→B), run 2**        | **0.77**  | **0.52** (MISS <0.6) | —      |
| Genuine-consistency (ensemble bench) | **0.639** | **0.468**            | 0.552  |

The cross-outfit test is the one CC-ReID exists to win (its whole premise
is clothes-invariance). It **lost** — OSNet recognized the subject in a
changed shirt + hat at 0.77–0.79 while CC-ReID fell below the match
threshold. A _generic_ embedder never trained for ReID (DINOv2) also beats
CC-ReID. This is not sampling noise: two independent cross-outfit runs +
two self-sim regimes + the bench all agree.

### Compute makes it decisive (accuracy alone is not the verdict)

Ensemble bench, pool cam, CPU, per-crop steady-state:

| model     | embed/crop | load    | size  | dim  | genuine | value (genuine/ms) |
| --------- | ---------- | ------- | ----- | ---- | ------- | ------------------ |
| **osnet** | **34 ms**  | 262 ms  | 9 MB  | 512  | 0.639   | **0.019**          |
| dinov2    | 115 ms     | 1240 ms | 88 MB | 384  | 0.552   | 0.005              |
| ccreid    | 437 ms     | 1203 ms | 94 MB | 4096 | 0.468   | 0.001              |

CC-ReID is **~13× slower, ~10× larger, 8× the embedding dim, AND least
accurate.** It is not a speed/quality tradeoff — it is dominated on every
axis. On accuracy-per-millisecond it is ~18× worse than OSNet.

### Likely root cause (unconfirmed — for follow-up)

Most probable, in order: (1) **preprocessing mismatch** — we feed CC-ReID
through OSNet's ImageNet pipeline at 384×192, but CAL/AIM models may expect
different normalization; (2) **unvalidated ONNX export** — we confirmed it
_loads + outputs finite vectors_, never that the embeddings are
_discriminative_ on any data; (3) **domain mismatch** — trained on
frontal/walking pedestrians, applied to steep top-down distant figures.
The fix is to validate CC-ReID against a public ReID set (isolates
export/preprocessing bug from domain) before trusting it.

## Actions taken from these findings

- **CC-ReID fusion weight dropped** 0.85 → 0.0 (effectively disabled in
  the combiner) until it is validated. Its current weight was an
  unevidenced prior; the evidence says it actively hurts. OSNet stays the
  body anchor it has earned.
- **Ensemble bench** (`scripts/dev/ensemble_bench.py`) added: model-agnostic
  registry + per-model accuracy×compute telemetry + a fusion sweep that
  reports the latency×accuracy Pareto frontier. Runs the moment a second
  subject lands (the sweep self-skips at 1 subject — honest, no fake
  rankings). Crops are cached so re-runs are fast.

## Caveats / what's NOT yet proven

- **Single subject.** Every accuracy number is genuine-only (self-
  consistency), not separability. A model that says 0.9 for everyone would
  look "consistent." True AUC/EER ranking + the fusion sweep need a second
  person's clips — the one gap. (The CC-ReID _underperformance_ conclusion
  is robust regardless, since it loses the genuine comparison too.)
- **Compute numbers are CPU + contended** (laptop shared with other jobs).
  Absolute ms will drop hugely on the 4090; the _relative_ ordering and
  the value-density gaps are the durable signal.
- **Driveway person-recognition not tested** — 8K CPU decode too slow to
  catch a walking person (0 person frames). Car arrival/departure
  transitions captured fine. Per the "never trade quality for compute"
  decision, the fix is NVDEC, not lower-res capture.

## Next experiments (when a second subject is available)

1. Re-run `ensemble_bench` → real separability + the fusion Pareto frontier.
2. Validate CC-ReID against a public ReID set to root-cause the failure.
3. Add more candidate models to the registry (other OSNet variants, CLIP,
   transformer ReID) for a wide field comparison.
4. Gait probe on the pool dense walks (durable modality #2, still untested
   on real footage).
