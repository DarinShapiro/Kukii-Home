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

## Update — 5-model bench (2026-06, OpenCLIP + DINOv2-L added)

Exported + benched three more embedders (research shortlist). Pool cam,
CPU, single subject (genuine-consistency, NOT separability — see trap below):

| model        | embed/crop | size     | dim  | genuine   |
| ------------ | ---------- | -------- | ---- | --------- |
| openclip_b32 | 112 ms     | 352 MB   | 512  | **0.895** |
| osnet        | **24 ms**  | **9 MB** | 512  | 0.639     |
| dinov2_s     | 167 ms     | 88 MB    | 384  | 0.552     |
| dinov2_l     | 12073 ms   | 1218 MB  | 1024 | 0.488     |
| ccreid       | 547 ms     | 94 MB    | 4096 | 0.468     |

Findings:

- **OpenCLIP 0.895 is striking but UNCONFIRMED.** With one subject, a high
  genuine-mean is exactly what an over-smooth embedder produces — CLIP is
  known to rate any two same-context images as similar. Cannot distinguish
  "great recognizer" from "everything looks alike" without an imposter.
  OpenCLIP is the model where the single-subject trap bites hardest →
  flagged as PROMISING, not proven. The 2nd subject is decisive here.
- **DINOv2-L: rejected.** ~12 s/crop (500× OSNet), 1.2 GB, AND lower
  accuracy than DINOv2-S. Bigger is worse here; off the candidate list.
- **OSNet remains the value champion** (0.639 @ 24 ms / 9 MB). Even if
  OpenCLIP proves better, OSNet is ~5× faster + 40× smaller — stays in
  contention for the edge box on accuracy-per-ms.
- **CC-ReID still last on accuracy, 2nd-worst on speed** (5th confirmation).

Next: capture a 2nd subject → re-run for real separability + fusion sweep.
This is what tells us if OpenCLIP's 0.895 is real discrimination or just
context-smoothing.

## Update — pet-ID (DINOv2) walkthrough with dog (2026-06)

Captured a dog walkthrough on the pool cam (`pool_rex_*_dog`, 1208 frames,
4K) to validate DINOv2 pet-ID (`pet_dinov2`) — its actual production role,
never before tested on real footage.

**Result: pet-ID could not be measured — blocked one layer upstream by
detection.**

- **Dog IS in the footage** (visually confirmed; one annotated frame shows
  `person 0.74` + `dog 0.34`).
- **But YOLO11x barely detects the dog**: a single marginal hit at 0.34
  conf; a DENSE scan (~242 frames at conf≥0.20) harvested ~0 usable dog
  crops. A standing person reads 0.74–0.86 on the same camera; a low,
  top-down dog reads ~0.34 or nothing.
- **Couldn't get ≥2 dog crops** → DINOv2 pet self-consistency untestable.

**Findings (actionable):**

1. **Pet detection, not pet recognition, is the bottleneck on this camera.**
   The default `conf=0.5` motion-gate would MISS the dog entirely
   (0.34 < 0.5) → recognition never receives a crop. **S16 (dog in yard /
   escaped pet) would fail outright as currently configured.**
2. **Fix: per-class detection thresholds** — `dog`/`cat` at ~0.25 vs
   `person` at 0.5. Without this, pets are invisible to the pool cam.
3. **Camera-geometry finding:** the pool cam's steep top-down angle is
   hostile to dog detection (small + foreshortened from above). Pet-ID
   wants a lower/side-angle camera; placement matters for S16.
4. **DINOv2 pet-ID stays an unvalidated assumption** (as CC-ReID was) — but
   the first thing to fix is detection. No point benchmarking the embedder
   until detection reliably yields crops.

Next: lower the dog/cat detection threshold + re-capture (ideally a
side-angle view of the dog), THEN test DINOv2 pet-ID. A neighbor's dog
would give the imposter for real pet separability.

## Update — detection was downsampling 4K to 640 (root cause of the dog miss)

Two corrections to the pet-detection finding above, both from the maintainer:

1. **The detector ran at `imgsz=640` on a 3840x2160 feed — a ~6x
   downsample.** A distant top-down dog (~150px in 4K) shrank to ~25px
   before YOLO ever saw it, well below its small-object floor. This — not
   the model — is the dominant cause of the 0.34 score. The earlier
   "YOLO11x can't detect the dog / consider replacing it" line was
   **premature**: it was a resolution-config bug, caught before any model
   swap. (Crop side was already correct — `_crop_person` crops from the
   full-res frame, so only detection leaked 4K detail.)
2. **The pool cam was shooting THROUGH a glass deck rail (temporary
   placement), causing glare that washes out detail.** Permanent mount on
   the clean side of the rail should materially improve image quality.

**Consequence — re-validate on clean footage:** every pool-cam number in
this doc (CC-ReID, OpenCLIP 0.895, OSNet enrollment, the dog) was measured
on glare-degraded footage AND/OR 640-downsampled detection. They remain
directionally useful but must be re-confirmed once (a) the camera is
permanently mounted (no glare) and (b) detection runs at full res.

**Fixed now:** `detection_image_size` default 640 -> 1280 (config +
DetectionConfig), still env-configurable, raise toward 1920+ on the GPU
box. The crop path already uses full-res.

**Deferred (proper full-res answer): TILED detection.** "Never downsample
the 4K" is only literally true with tiling — slice the 4K frame into
overlapping native-res tiles, detect per-tile (a distant dog gets full
pixels), merge boxes. Crank-imgsz is the crude version; tiling is correct.
NOT shipped yet because it interacts with the `model.track()` path that
produces the track_ids the identity/correlation pipeline depends on:
tiling needs track-id merging across tile boundaries, which can't be
validated on the current glare/sparse-dog footage. Build + validate it on
clean footage. (Detect-on-4K-then-crop-from-4K, the maintainer's
principle, == tiled detection.)
