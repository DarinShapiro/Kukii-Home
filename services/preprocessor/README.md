# services/preprocessor/

Motion-gated 24/7 preprocessing for service-mode NVR adapters. Consumes RTSP from any NVR (or directly from cameras), runs motion detection, generates frame markup + metadata, exposes results via MCP.

**Architecture:** [§03.5](../../docs/architecture/03.5-nvr-adapter-layer.md), [§08](../../docs/architecture/08-detection-pipeline.md)

## Responsibilities

- Consume RTSP streams (multiple cameras concurrently)
- 24/7 motion detection (MOG2 + optical flow + size filter + temporal consistency)
- When motion: invoke detector (YOLO, face, re-ID, pose) for enrichment
- Cache enriched frames + metadata (Redis or in-memory, 5-min rolling window)
- Expose `nvr.*` MCP contract for service-mode adapters

## Mode

This service is used by **service-mode** NVR adapters. It is bypassed for:

- Built-in mode (Frigate provides its own preprocessing)
- Native mode (preprocessing runs in-process inside the NVR; future)
- Direct mode (no NVR — this service is invoked directly by RTSP adapter)

## Status

Skeleton. Implementation tracked in [`planning/epics/04-preprocessing-detection.md`](../../planning/epics/).
