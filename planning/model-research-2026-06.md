# Model research — candidates for the identity/detection stack (2026-06)

Triggered by: the CC-ReID validation failure (see `validation-findings.md`)
and the question "what are the best models to try?" — incl. NVIDIA's new
"Find Anything" (actually **LocateAnything-3B**). Goal: a shortlist to add
to `ensemble_bench.py` and a verdict on each slot.

## TL;DR recommendations

| Slot                            | Try next                                              | Why                                                                                                                                       |
| ------------------------------- | ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **Detection**                   | **YOLO26** (n/s)                                      | ~43% faster CPU than YOLO11; NMS-free, cleaner ONNX/TensorRT export; direct drop-in. Low risk, real win.                                  |
| **Body ReID (durable)**         | **SOLIDER** + **TransReID-SSL**                       | DINO/ViT-based, ONNX-exportable, current strong supervised ReID. The category our broken CC-ReID was meant to fill.                       |
| **Body ReID (semantic)**        | **CLIP-ReID** / **SigLIP2**                           | Language-aligned; best _cross-domain generalization_ in the 2026 survey (our cameras ≠ training domain → generalization is what we need). |
| **General embedder (baseline)** | **DINOv2** (already have)                             | Already beats CC-ReID in our bench; keep as the floor every ReID model must clear.                                                        |
| **Open-vocab / VLM grounding**  | LocateAnything-3B — **NO for runtime**, maybe offline | See below: non-commercial license + 3B VLM = wrong tool for the 24/7 gate.                                                                |

## NVIDIA LocateAnything-3B — the "Find Anything" you heard about

Real, released **2026-05-26** (CVPR2026). But it is **not** a YOLO
replacement for us, for three hard reasons:

1. **License: NON-COMMERCIAL ONLY.** "Commercial use is NOT PERMITTED,
   except by NVIDIA." Kukii-Home is a product → disqualifying for shipping
   code, full stop. (Fine for our own offline experiments.)
2. **It's a 3B vision-language model**, not a detector. Input = image +
   text prompt ("locate all people in red shirts"); output = text with
   embedded box coordinates. Throughput numbers are quoted on an **H100**;
   it's Linux-only, no TensorRT/Triton yet. That is the opposite of a 24/7
   motion-gate detector on a Pi/edge box — it's heavyweight, prompt-driven,
   autoregressive-ish.
3. **Where it WOULD shine is the VLM reasoning layer, not detection.** Its
   real strength — open-vocabulary grounding, "find the person tampering
   with the door handle," GUI/document/scene-text — overlaps our **VLM
   grounding** stage (§09), not our YOLO stage. Worth watching as a
   _grounding_ model if the license ever loosens, but it does not belong in
   the detection or identity slots.

**Verdict:** impressive, wrong layer + wrong license for us. Don't chase it.
The genuinely useful detection news is **YOLO26**.

## YOLO26 (Ultralytics, Jan 2026) — the real detection upgrade

- **~43% faster CPU inference** than YOLO11 at equal size (YOLO26n: 38.9ms
  vs YOLO11n 56.1ms, ONNX Runtime). NMS-free end-to-end → simpler, lower
  latency, no NMS tuning.
- Removed Distribution Focal Loss → **cleaner ONNX/TensorRT/OpenVINO export**
  (DFL was a recurring export headache). Better INT8 quantization stability
  than transformer detectors (RT-DETR drops hard under INT8).
- Same task family (detect/seg/pose) → **drop-in for our YOLO11x**, same
  ultralytics API. Low-risk swap; pure latency win, which matters given the
  decode/inference wall we just measured.
- We use yolo11x; the move is YOLO26-x for accuracy-parity-but-faster, or
  test YOLO26-s/m for the edge box.

## Body-ReID — and what the 2026 survey says about our CC-ReID failure

The 2026 ReID survey ("Supervised, Self-Supervised, and Language-Aligned —
What Works?") **corroborates our finding** and reframes the problem:

- **"Hybrid approaches are best for ReID currently."** No single model
  wins; the field has moved to ensembles + language-alignment — which is
  exactly the fused-ensemble direction we're already building.
- **Cross-DOMAIN generalization is the real issue**, and the clothes-
  adversarial CAL/AIM family (our `ccreid_cal`) is **not** what the survey
  highlights. Language-aligned models (**CLIP-ReID, SigLIP2**) and self-
  supervised ViT (**SOLIDER, TransReID-SSL, DINOv2**) generalize better
  across unseen cameras — and our pool/driveway cams are very much an
  unseen domain vs. LTCC/Market training data.
- Concrete: CLIP-ReID hits 66% mAP in-domain on MSMT17 but **collapses to
  3% cross-camera** → overfitting is the trap. SigLIP2 is weaker peak but
  **stable across domains** (2.8–14.2% mAP spread). For a _home_ deployment
  on bespoke cameras, **stable-cross-domain beats peak-in-domain.**

Takeaway: our CC-ReID likely failed not because "clothes-invariance is
wrong" but because a **CAL/LTCC-trained model applied to a top-down home
cam is a domain mismatch** (one of our hypothesised root causes). The
better bets are **SOLIDER / TransReID-SSL** (self-supervised ViT, strong +
exportable) and **CLIP-ReID / SigLIP2** (language-aligned, generalize) —
all ONNX-exportable, all addable to the bench.

Wildcard worth noting: **GEFF** (Gallery Enrichment with Face Features) —
SOTA on PRCC/LTCC by _combining_ a ReID model with face features. That's
literally our **fusion** thesis (face ⊕ body), externally validated.
**Pose2ID** (CVPR2025) is a training-free feature-centralization wrapper
that boosts _any_ ReID model (even ImageNet-pretrained) — cheap to try on
top of whatever wins.

## Concrete next actions

1. **Detection:** export YOLO26-x → ONNX, add to bench / swap behind a flag.
   Lowest-risk, measurable latency win. (Don't pursue LocateAnything.)
2. **Body ReID:** export **SOLIDER** + **TransReID-SSL** + **CLIP-ReID** to
   ONNX, add each as one line in `ensemble_bench.py`'s registry. These are
   the real candidates to replace/augment OSNet as the durable anchor.
3. **Re-run the bench** once a 2nd subject exists → the accuracy×compute
   Pareto picks the winner(s) for the fused ensemble. The survey says the
   answer will likely be a _hybrid_, which is exactly what the bench scores.
4. **Re-validate CC-ReID** against a public set (PRCC/LTCC) to confirm
   domain-mismatch vs export-bug before discarding the approach entirely.

## Sources

- NVIDIA LocateAnything-3B (HF): https://huggingface.co/nvidia/LocateAnything-3B
- LocateAnything coverage: https://medium.com/data-science-in-your-pocket/nvidia-locateanything-3b-goodbye-yolo-object-detection-a264117f1318
- YOLO26 (arXiv): https://arxiv.org/abs/2509.25164
- YOLO26 vs YOLO11 (Ultralytics): https://docs.ultralytics.com/compare/yolo11-vs-yolo26
- ReID survey 2026: https://arxiv.org/html/2601.20598v1
- SOLIDER-REID: https://github.com/tinyvision/SOLIDER-REID
- TransReID-SSL: https://github.com/damo-cv/TransReID-SSL
- GEFF (face-enriched CC-ReID): https://arxiv.org/pdf/2211.13807
- Pose2ID (CVPR2025): https://github.com/yuanc3/Pose2ID
