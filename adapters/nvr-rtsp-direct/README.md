# adapters/nvr-rtsp-direct/

Direct RTSP adapter. No NVR; consumes RTSP streams directly from ONVIF cameras. SentiHome's internal preprocessing runs effectively in native mode (single decode, in-process).

**Architecture:** [§03.5](../../docs/architecture/03.5-nvr-adapter-layer.md)

**Mode:** Direct (internal native)
**Priority:** v1 target — the long-term recommended path
**Status:** Skeleton. Implementation tracked in [`planning/epics/03-nvr-adapters.md`](../../planning/epics/).
