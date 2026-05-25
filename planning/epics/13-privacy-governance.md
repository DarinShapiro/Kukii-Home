# Epic 13: Privacy & Governance

**Architecture refs:** §16
**Components:** services/core, services/memory, services/vlm-router
**Priority:** P0
**Blocked by:** Epic 02, 05, 06

## Description

Privacy-by-architecture enforcement. Data classes (A–D), privacy tiers (local_only / cloud_eligible / cloud_any), tagging at ingress, enforcement at router, scrubbing pipeline, retention auto-cleanup, right-to-forget, multi-resident consent, audit log. GDPR/CCPA alignment.

## Definition of done

- Every event/file tagged with `privacy_tier` at ingress
- Router enforces tier constraints (hard reject local_only → cloud)
- Scrubbing pipeline for unknown faces, plates, interior backgrounds
- Retention auto-cleanup per data class (nightly job)
- Soft-delete + 7-day grace period
- Right-to-forget flow end-to-end
- Multi-resident consent with most-restrictive-wins
- Parental override for minors
- Audit log + UI for cloud egress

## Issues

1. **feat(shared): privacy tier enum + tagging schema** — `local_only`, `cloud_eligible`, `cloud_any` on every message. (labels: `epic:privacy`, `component:shared`, `priority:p0`)
2. **feat(core): tag at ingress** — preprocessor + NVR adapters tag events with privacy_tier based on camera area/role. (labels: `epic:privacy`, `component:core`, `priority:p0`)
3. **feat(vlm-router): enforce privacy tier at router** — hard reject local_only data routed to cloud. (labels: `epic:privacy`, `component:vlm-router`, `priority:p0`)
4. **feat(core): scrubbing pipeline** — blur unknown faces, hash plates, remove identifiable context before cloud egress. (labels: `epic:privacy`, `component:core`, `priority:p1`)
5. **feat(core): user-configurable scrubbing level** — off / minimal / moderate / aggressive. (labels: `epic:privacy`, `component:core`, `priority:p2`)
6. **feat(memory): retention auto-cleanup job** — nightly, per data class, soft-delete then secure erase after grace. (labels: `epic:privacy`, `component:memory`, `priority:p1`)
7. **feat(memory): right-to-forget flow** — search all locations for subject, soft-delete + grace + secure erase. (labels: `epic:privacy`, `component:memory`, `priority:p1`)
8. **feat(core): multi-resident consent model** — per-resident privacy levels, conflict resolution (most restrictive wins). (labels: `epic:privacy`, `component:core`, `priority:p2`)
9. **feat(core): parental override for minors** — `very_high` privacy level, blocks cloud for child-involved data. (labels: `epic:privacy`, `component:core`, `priority:p2`)
10. **feat(core): visitor consent tracking** — record visitor preferences across sessions. (labels: `epic:privacy`, `component:core`, `priority:p2`)
11. **feat(memory): cloud egress audit log** — every byte recorded with type, destination, user, retention. (labels: `epic:privacy`, `component:memory`, `priority:p1`)
12. **feat(ha-integration): "Data leaving home" UI panel** — cloud usage summary + audit log access. (labels: `epic:privacy`, `component:ha-integration`, `priority:p2`)
13. **docs: GDPR/CCPA compliance guide** — what data we collect, retention, deletion, export. (labels: `epic:privacy`, `component:docs`, `priority:p1`)
14. **test: privacy enforcement tests** — attempt local_only → cloud routing must fail; right-to-forget round-trip. (labels: `epic:privacy`, `component:core`, `priority:p0`)
