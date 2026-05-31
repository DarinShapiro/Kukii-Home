# Epic 04: Preprocessing & Detection

**Architecture refs:** §08
**Components:** services/preprocessor, services/detector
**Priority:** P0
**Blocked by:** Epic 02, 03

## Description

The preprocessing layer that gates the pipeline (motion detection) and enriches frames before they reach the VLM. Used by all service-mode NVR adapters and by the direct RTSP adapter as Kukii-Home's internal preprocessing.

## Definition of done

- Robust motion detection (MOG2 + optical flow + size filter + temporal consistency) running 24/7
- Per-camera motion sensitivity tunable + automatically refined via §10.5
- YOLO/RT-DETR object detection wired up with GPU acceleration
- Face detection + ArcFace embedding pipeline
- Body re-ID embedding pipeline
- Pose estimation, plate OCR, pet recognition models loaded
- Stillness + drowning classifiers
- Detector exposes both embedded mode (called by preprocessor) and standalone MCP

## Issues

1. **feat(preprocessor): RTSP multi-stream consumer** — handles N cameras concurrently with shared decoder pool. (labels: `epic:preprocessing`, `component:preprocessor`, `priority:p0`)
2. **feat(preprocessor): MOG2 background subtraction motion detector** — per-camera adaptive background model. (labels: `epic:preprocessing`, `component:preprocessor`, `priority:p0`)
3. **feat(preprocessor): optical flow motion validation** — distinguishes real motion from lighting changes. (labels: `epic:preprocessing`, `component:preprocessor`, `priority:p0`)
4. **feat(preprocessor): object-size filtering + temporal consistency** — minimum size threshold, sustained-duration requirement. (labels: `epic:preprocessing`, `component:preprocessor`, `priority:p0`)
5. **feat(preprocessor): on-camera AI corroboration logic** — fuse on-camera AI events with our motion signal. (labels: `epic:preprocessing`, `component:preprocessor`, `priority:p1`)
6. **feat(preprocessor): per-camera motion config** — exclusion zones, environmental modes (rain, night). (labels: `epic:preprocessing`, `component:preprocessor`, `priority:p1`)
7. **feat(preprocessor): frame markup pipeline** — draws bboxes/annotations on frames for VLM consumption. (labels: `epic:preprocessing`, `component:preprocessor`, `priority:p1`)
8. **feat(preprocessor): metadata cache (Redis/in-memory)** — 5-min rolling window keyed by camera + ts. (labels: `epic:preprocessing`, `component:preprocessor`, `priority:p0`)
9. **feat(preprocessor): expose `nvr.*` contract** — service-mode adapters call into preprocessor. (labels: `epic:preprocessing`, `component:preprocessor`, `priority:p0`)
10. **feat(detector): YOLO/RT-DETR integration** — ONNX runtime + GPU acceleration, returns class+bbox+track_id. (labels: `epic:preprocessing`, `component:detector`, `priority:p0`)
11. **feat(detector): face detection (SCRFD/RetinaFace)** — high-recall face detector with crop output. (labels: `epic:preprocessing`, `component:detector`, `priority:p0`)
12. **feat(detector): face recognition (ArcFace/AdaFace)** — produces embeddings for gallery matching. (labels: `epic:preprocessing`, `component:detector`, `priority:p0`)
13. **feat(detector): body re-ID embeddings (OSNet)** — in-session person continuity. (labels: `epic:preprocessing`, `component:detector`, `priority:p0`)
14. **feat(detector): pose estimation** — keypoints for intent + gait + height. (labels: `epic:preprocessing`, `component:detector`, `priority:p1`)
15. **feat(detector): plate OCR for vehicles** — license plate text extraction. (labels: `epic:preprocessing`, `component:detector`, `priority:p2`)
16. **feat(detector): pet recognition** — separate gallery for pets, face + coat pattern. (labels: `epic:preprocessing`, `component:detector`, `priority:p2`)
17. **feat(detector): stillness + drowning classifiers** — pool safety, motionless person detection. (labels: `epic:preprocessing`, `component:detector`, `priority:p2`)
18. **feat(detector): standalone MCP server mode** — for on-demand enrichment outside the standard frame flow. (labels: `epic:preprocessing`, `component:detector`, `priority:p1`)
19. **feat(detector): detector-guided frame selection** — choose frames where subject is most visible/centered. (labels: `epic:preprocessing`, `component:detector`, `priority:p1`)
20. **feat(preprocessor): track ID persistence across clip frames** — stable subject references for cross-frame reasoning. (labels: `epic:preprocessing`, `component:preprocessor`, `priority:p1`)
21. **test: motion detection robustness suite** — recorded clips with lighting/wind/rain to verify false-positive rejection. (labels: `epic:preprocessing`, `component:preprocessor`, `priority:p1`)
