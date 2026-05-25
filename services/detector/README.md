# services/detector/

Fast-detector models: YOLO/RT-DETR (objects), SCRFD/RetinaFace (face detection), ArcFace/AdaFace (face recognition), OSNet (body re-ID), pose estimation, plate OCR, pet recognition, stillness/drowning classifiers.

**Architecture:** [§08](../../docs/architecture/08-detection-pipeline.md)

## Two deployment modes

1. **Embedded in preprocessor** — for service-mode NVR adapters, the preprocessor invokes detector models directly
2. **Standalone MCP service** — for cases where detection is invoked outside the standard frame flow (e.g., on-demand enrichment, attention mode bursts)

## Status

Skeleton. Implementation tracked in [`planning/epics/04-preprocessing-detection.md`](../../planning/epics/).
