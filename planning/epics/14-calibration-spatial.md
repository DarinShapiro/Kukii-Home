# Epic 14: Calibration & Spatial Model

**Architecture refs:** §13, §14
**Components:** services/memory, services/core, frontend/operator-dashboard
**Priority:** P2
**Blocked by:** Epic 06

## Description

Camera/area/zone spatial model and the calibration workflows that enable world-space reasoning. Includes intrinsics, extrinsics, ground plane estimation, stereo calibration, PTZ presets. Three calibration UX paths (phone AR, landmark PnP, resident walk).

## Definition of done

- Camera/area/zone records modeled per §13
- Calibration data structures per §14
- At least one calibration UX path works end-to-end
- World-space zones (ground plane) computable
- Stereo calibration supported where 2+ overlapping cameras exist
- PTZ preset management

## Issues

1. **feat(memory): camera record schema** — role, location, streams, capabilities, detector profile, attached zones/areas, health. (labels: `epic:calibration`, `component:memory`, `priority:p2`)
2. **feat(memory): area record schema** — semantic grouping, hierarchy, containment, cameras coverage, security/access profile. (labels: `epic:calibration`, `component:memory`, `priority:p2`)
3. **feat(memory): zone schemas** — image-space (2D), world-space (3D ground plane), height-aware (pools, stairs). (labels: `epic:calibration`, `component:memory`, `priority:p2`)
4. **feat(memory): adjacency graph** — reachable areas, travel times, blind spots. (labels: `epic:calibration`, `component:memory`, `priority:p2`)
5. **feat(core): site coordinate frame** — origin, axes, units, floor plan registration. (labels: `epic:calibration`, `component:core`, `priority:p2`)
6. **feat(core): intrinsics calibration support** — focal length, principal point, distortion model storage. (labels: `epic:calibration`, `component:core`, `priority:p2`)
7. **feat(core): extrinsics calibration storage** — rotation, translation from site frame to camera. (labels: `epic:calibration`, `component:core`, `priority:p2`)
8. **feat(operator-dashboard): phone AR calibration flow** — easiest UX, leverages ARKit/ARCore. (labels: `epic:calibration`, `component:frontend`, `priority:p2`)
9. **feat(operator-dashboard): landmark PnP calibration flow** — highest precision, manual landmark tagging. (labels: `epic:calibration`, `component:frontend`, `priority:p2`)
10. **feat(operator-dashboard): resident walk calibration flow** — lowest friction; person walks known path. (labels: `epic:calibration`, `component:frontend`, `priority:p2`)
11. **feat(core): ground plane estimation** — derive from extrinsics + landmark heights. (labels: `epic:calibration`, `component:core`, `priority:p2`)
12. **feat(core): stereo calibration** — baseline measurement, frame synchronization, 3D anomaly detection. (labels: `epic:calibration`, `component:core`, `priority:p2`)
13. **feat(core): PTZ preset management** — presets as virtual cameras, fixed + PTZ pairing. (labels: `epic:calibration`, `component:core`, `priority:p2`)
14. **feat(operator-dashboard): area / zone editor** — visual editor for spatial model. (labels: `epic:calibration`, `component:frontend`, `priority:p2`)
