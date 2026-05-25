# 14 — Calibration (Fixed, PTZ, Stereo)

**Purpose:** How cameras are calibrated, how drift is detected, and what world-coordinate capabilities calibration unlocks.
**Status:** drafting

---

## What calibration unlocks

Calibration transforms pixel coordinates into world coordinates (meters, world-frame), enabling:

1. **Real-world distances & speed** — "subject traveling at 1.2 m/s implies human pace" vs "2.8 m/s implies vehicle"
2. **Height as identity feature** — skeleton keypoints → estimated cm height, used for cross-day person matching (§12)
3. **World-coordinate rule scopes** — zones defined in meters on floor plan, projected per camera, enforced consistently
4. **Multi-camera fusion** — person detected in cam A at (100, 500) px and cam B at (350, 200) px → same world location (1.2m, 3.4m)?
5. **Stereo 3D primitives** — overlapping cameras enable depth, critical for drowning detection (pose + depth = person fully submerged?)
6. **PTZ calibration** — PTZ presets stored as pan/tilt angles → world direction; slew and re-acquire targets

**Calibration is optional** — the system operates without it (everything in image space only) but loses these capabilities. For life-safety scenarios (pool, stairs), calibration is highly recommended.

---

## Intrinsics, extrinsics, ground plane

### Camera intrinsics

Describes how a camera projects 3D world points onto its 2D image plane. Stable per camera (unless lens changes).

```
Intrinsics:
  focal_length_px: [1920, 1920]    ← fx, fy (pixels)
  principal_point: [1280, 960]     ← cx, cy (image center)
  distortion: {
    model: "fisheye | rational | radial_tangential",
    coefficients: [k1, k2, p1, p2, ...]
  }

  Reprojection error: ±2–3 pixels (good quality)
```

**Calibration source:** Checkerboard / ArUco marker detection in known geometry, or pre-calibrated from camera manufacturer spec.

### Camera extrinsics

Describes where the camera is located and oriented in world coordinates. Changes when camera is moved/rotated.

```
Extrinsics:
  rotation: 3×3 matrix (world-to-camera rotation)
  translation: (x, y, z) vector from site-frame origin to camera center

  Example: doorbell camera
    position: (0.5m east, 0.0m north, 2.1m up) from site origin
    direction: 180° (facing south, toward street)
    rotation: computed from direction + tilt
```

### Ground plane estimation

For world-space zones on a floor, we need to project 3D floor points onto each camera's image. Ground plane is defined by:

- Floor height `z = z_floor` (constant, e.g., 0.0m)
- Normal vector pointing up (0, 0, 1)

**Projection example:**

```
World floor point: (1.0m east, 2.0m north, 0.0m altitude)

Step 1: Apply camera extrinsics (world → camera frame)
  point_cam = Rotation @ (world_point - translation)

Step 2: Apply intrinsics (3D camera → 2D image)
  u = fx * (point_cam.x / point_cam.z) + cx
  v = fy * (point_cam.y / point_cam.z) + cy

Result: pixel location (u, v) where that floor point appears
```

---

## Calibration UX options

Three approaches, ranked by ease vs. accuracy:

### Option 1: Phone AR walk (easiest, ~3–5 min)

User walks around holding phone, AR app shows camera views overlaid on real space.

```
Flow:
  1. Start AR session, select target camera
  2. User walks to camera's position, phones taps: "here"
  3. User walks to known corner/landmark, taps: "southeast corner"
  4. User stands at known distances: "1 meter away", "3 meters away"
  5. App computes extrinsics from phone IMU + vision + taps
  6. Quick sanity check: "does projected corner match real corner?"

Accuracy: ±0.5m position, ±5° rotation (good for medium-stakes scenarios)
Drift: Slow (weeks)
```

**Pros:** Very intuitive, fast, captures human experience of space
**Cons:** Phone IMU drifts; marker-less; lower precision

### Option 2: Landmark tagging + PnP (moderate effort, high accuracy)

User tags visible landmarks in each camera view, provides their real-world coordinates.

```
Flow:
  1. Show camera 1's live view
  2. User clicks on known corners/objects: "click the southeast corner"
  3. Repeat for 4–6 landmarks
  4. System solves Perspective-n-Point (PnP) problem: find camera pose
       that best explains observed pixel-to-world correspondences
  5. Repeat for camera 2, 3, ...
  6. Cross-validate: "floor point (1.0, 2.0) should appear at pixel (X, Y) in cam1"

Accuracy: ±2–5cm position, ±2° rotation (excellent)
Drift: Very slow (months)
```

**Pros:** High precision; user has explicit control; landmarks are durable
**Cons:** Requires 4–6 landmarks visible in each camera; manual tagging tedious

### Option 3: Resident-walk auto-calibration (lowest friction)

System learns camera geometry by passively observing residents' known trajectories over days.

```
Mechanism:
  1. System knows typical resident heights (from face gallery)
  2. System observes resident walking at known pace in frame
  3. Skeleton height estimate + walking speed → ground plane constraint
  4. Over multiple walks, triangulation → camera pose

Flow:
  1. System asks: "Walk past each camera at normal pace so I can learn your height?"
  2. User walks 2–3 times per camera
  3. System computes intrinsics/extrinsics incrementally
  4. Weekly re-calibration runs passively

Accuracy: ±0.3–0.5m position, ±3° rotation (good after 3–5 walks)
Drift: Adaptive (detects and corrects drift)
```

**Pros:** Zero extra work beyond normal living; auto-improves
**Cons:** Slower (days to converge); requires good skeleton detection

### Recommended workflow

1. **Initial:** Use Option 2 (landmark tagging) or Option 1 (AR walk) for bootstrap
2. **Ongoing:** Enable Option 3 (resident walk) to auto-refine and detect drift

---

## Extrinsic stability & re-calibration

Once calibrated, extrinsics can drift due to:

- Camera vibration (wind, door slam)
- Physical relocation (even 1 cm)
- Thermal expansion
- Mount fatigue

**Drift detection strategies:**

```
Canary 1: Resident-height validation
  Regular resident walks past camera
  Skeleton-estimated height: typically 175cm ±2cm
  If estimate suddenly shifts to 172cm or 180cm → drift detected

Canary 2: Landmark reprojection
  Known floor landmark (e.g., corner of mat)
  Periodically check: does it still project to expected pixel?
  If pixel error > 3–5px → drift detected

Canary 3: Cross-camera consistency
  Two cameras observing same person
  World position estimated from cam1 vs cam2 disagree by > 0.3m?
  → one camera may have drifted

Action on drift detection:
  - Log warning
  - Suggest re-calibration in UI
  - Auto-enable Option 3 walk re-calibration if not already running
  - Temporarily flag `geometry_unreliable: true` in output (VLM & reasoner know estimate is low-confidence)
```

---

## Stereo on overlapping pairs

When two cameras have overlapping fields of view, stereo enables **depth** (distance to camera in 3D).

### When stereo is worth it

| Scenario                 | Benefit                                    | ROI                                 |
| ------------------------ | ------------------------------------------ | ----------------------------------- |
| Pool drowning detection  | Depth determines if person fully submerged | **Very high** — literal life-safety |
| Fall detection on stairs | Height below expected floor → fall         | High — medical emergency            |
| Two-camera front door    | Verify person is at threshold vs. 2m back  | Medium — improves face quality      |
| Driveway traffic         | Depth distinguishes near vehicle from far  | Low — less critical for driveway    |

Pool + dual-camera stereo is **strongly recommended**; others are optional.

### Stereo calibration

```
Relative pose between camera 1 and camera 2:
  Rotation (relative): R
  Translation (baseline vector): t

Typical baseline for front-door stereo: 0.5–1.0m apart
Typical baseline for pool stereo: 1–2m apart (to see depth underwater)

Calibration flow:
  1. Calibrate intrinsics (checkerboard) for each cam
  2. Calibrate extrinsics (world frame) for each cam (Option 2)
  3. Compute relative pose: R, t = extrinsics_cam2^-1 @ extrinsics_cam1
  4. Validate: project known 3D point to both images, verify stereo constraint holds
```

### Synchronized frame capture & sync offset

Stereo triangulation requires **synchronized frames**:

```
Ideal: capture frame from cam1 and cam2 at exact same instant
Real: network latency, exposure times differ, rolling shutter

Solution:
  1. Configure RTSP sync: request keyframes at same wall time
  2. Measure actual offset: compare timestamp metadata cam1.ts vs cam2.ts
  3. Compensate: when triangulating, use frame from cam2 at time = cam1.ts + offset
  4. Acceptable offset: ±33ms (one frame at 30fps)

If sync offset > 100ms → stereo unusable; flag `stereo_unreliable`
```

### 3D anomaly detection with stereo

**Drowning detection example:**

```
Raw detections:
  Camera 1: person detected, head at pixel (640, 480), confidence 0.89
  Camera 2: person detected, head at pixel (320, 200), confidence 0.85

Stereo triangulation:
  3D position of head: (1.2m, 3.4m, 0.8m) in world coords

Water surface: z = 0.9m
Head is above water: z=0.8m < 0.9m? NO → head is fully submerged ✓

Distress pose check:
  Arms above water: elbow y > water_surface?
  Yes → arms raised above water
  Head submerged + arms up → high drowning risk

Alert: URGENT
```

---

## PTZ (pan-tilt-zoom) strategy

PTZ cameras can move and zoom. Calibration enables:

- Presets as virtual static cameras
- Target re-acquisition after pan/tilt
- Reliable zoom for long-distance identification

### Preset-based approach

```
PTZ preset = (pan°, tilt°, zoom)
Example: Preset "garage_entrance" = (45°, -10°, 1.0x)

Calibration:
  1. Move PTZ to each preset, capture frame
  2. Landmark-tag or auto-calibrate (Option 2/3) for that preset position
  3. Store extrinsics keyed by preset_id
  4. At runtime, when PTZ moves, know exact new pose

Effect:
  Each PTZ preset acts like a separate static camera
  Zones projected per preset independently
  Reasonable to write rules against "garage_entrance" preset
```

### Fixed + PTZ pairing

Typical setup: one fixed wide-view camera + one PTZ narrow-view camera on same area.

```
Scenario: Backyard
  Fixed camera: wide 155° FOV, low zoom
  PTZ camera: narrow 30° FOV, can zoom 3x

Normal operation:
  PTZ parks in "home" preset (pointing driveway)
  Fixed camera monitors backyard continuously
  Fast detector triggers → attention needed in unexpected area?
    → PTZ slews to new preset ("back_left_corner")
    → high-res PTZ frame sampled
    → VLM gets both fixed wide + PTZ zoom
    → PTZ returns to home after 30s idle

Architecture:
  Both cameras in site calibration (fixed + PTZ home preset positions)
  Additional presets stored as virtual cameras
  No special handling in downstream logic (rules written against areas, not specific cameras)
```

### Geometry reliability during slewing

While a PTZ is moving:

```
geometry_unreliable: true

Reason: intermediate frames don't have known pose (moving between presets)
Effect: skip stereo processing, flag low-confidence estimates
Duration: PTZ start → PTZ.settled_at + 100ms
```

---

## Expected precision by approach

| Approach                         | Position accuracy | Rotation accuracy | Practical use                                  | Re-calibration |
| -------------------------------- | ----------------- | ----------------- | ---------------------------------------------- | -------------- |
| Phone AR                         | ±0.5m             | ±5°               | Distance classification, rough zone projection | Weekly         |
| Landmark PnP                     | ±5cm              | ±2°               | Height extraction, stereo, precise zones       | Monthly        |
| Resident walk                    | ±0.3m             | ±3°               | Height extraction, zones                       | Auto (daily)   |
| Manufacturer spec (no site cal.) | Unknown           | Unknown           | Only intrinsics; extrinsics assumed            | Never          |

---

## Calibration maintenance checklist

- [ ] Intrinsics measured or obtained from manufacturer (per camera)
- [ ] Initial extrinsics calibrated (Option 2 or 1 recommended)
- [ ] Ground plane & site frame defined (§13)
- [ ] Zone projections computed for all cameras
- [ ] Stereo baseline measured (if stereo cameras)
- [ ] PTZ presets calibrated (if PTZ cameras)
- [ ] Drift detection enabled (height canary, landmark reprojection)
- [ ] Re-calibration workflow tested (Option 3 or manual landmark re-tagging)
- [ ] Geometry precision documented for each camera (expected accuracy for rules)
