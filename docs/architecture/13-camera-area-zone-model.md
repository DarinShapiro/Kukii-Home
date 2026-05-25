# 13 — Camera, Area & Zone Data Model

**Purpose:** The spatial data model rules and journeys are written against. Decoupled from physical cameras so cam swaps don't break the world.
**Status:** drafting

---

## Core principle

Rules, journeys, and alerts are written against _logical areas and zones_, not physical camera IDs. This decouples reasoning from hardware:

- Swap a camera → update the mapping, rules fire unchanged
- Add a camera → assign to existing area/zone, no rule rewrites
- Relocate a zone → update coordinates, detection logic auto-adjusts

The VLM never sees camera IDs or zone names — it sees `area_id` as context and enrichment includes zone membership per detection.

---

## Cameras

### Camera record

```json
{
  "camera_id": "doorbell_main",
  "label": "Front Door (Main)",
  "description": "High-res doorbell camera, wide FOV",

  "role": {
    "primary": "perimeter_security",
    "secondary": ["entrance_confirmation", "guest_alerting"],
    "sensitivity": "high"
  },

  "location": {
    "position_coords": { "x": 0.5, "y": 0.0, "z": 2.1 },
    "direction": 180,
    "mounting": "fixed | ptz"
  },

  "streams": {
    "main": {
      "profile": "2560×1920@15fps",
      "codec": "h264",
      "rtsp_url": "rtsp://...",
      "latency_ms": 500
    },
    "substream": {
      "profile": "640×480@8fps",
      "codec": "h264",
      "rtsp_url": "rtsp://...",
      "purpose": "motion_trigger | continuous_monitoring"
    }
  },

  "capabilities": {
    "ptz": false,
    "ir_night_vision": true,
    "wide_dynamic_range": true,
    "audio": true,
    "thermal": false,
    "estimated_sensor_size": "1/2.7",
    "fov_horizontal_degrees": 155
  },

  "detector_profile": {
    "face_detection_capable": true,
    "preferred_detector_model": "scrfd_500m",
    "night_detector_model": "yolov5_640_night",
    "max_concurrent_tracks": 8,
    "max_frame_latency_ms": 500
  },

  "attached_zones": ["entry_mat", "porch_stairs"],
  "attached_areas": ["front_door"],

  "status": "active | inactive | maintenance",
  "last_online": "2026-05-23T14:33:22Z",
  "health": {
    "connectivity": "excellent | good | spotty | offline",
    "cpu_load": 0.45,
    "uptime_days": 187
  }
}
```

---

## Areas

Semantic groupings that correspond to logical spaces in the home. An area can be covered by multiple cameras and may contain multiple zones.

### Area record

```json
{
  "area_id": "front_door",
  "label": "Front Door",
  "description": "Front porch, doorbell, entry vestibule",

  "containment": {
    "parent_area": null,
    "child_areas": ["entry_vestibule"],
    "zones": ["entry_mat", "porch_stairs", "porch_right"]
  },

  "cameras": [
    {
      "camera_id": "doorbell_main",
      "coverage": "primary",
      "blind_spots": ["porch_right_corner"]
    },
    {
      "camera_id": "exterior_corner",
      "coverage": "supplemental",
      "blind_spots": []
    }
  ],

  "security_profile": {
    "sensitivity": "high",
    "face_required_for_alert": true,
    "person_confidence_threshold": 0.75,
    "alert_targets": ["resident_1", "resident_2"]
  },

  "access_profile": {
    "allowed_persons": [
      {
        "actor_id": "sarah_resident",
        "allowed_times": "anytime"
      },
      {
        "actor_id": "mail_carrier",
        "allowed_times": {
          "days": ["Mon–Fri"],
          "hours": "08:00–18:00"
        }
      }
    ],
    "flagged_persons": [],
    "unattended_alert_threshold_minutes": 5
  },

  "devices": {
    "locks": ["lock.front_door"],
    "lights": [
      {
        "entity_id": "light.porch_lights",
        "scene_presets": ["on_bright", "on_dim", "night_dim"]
      }
    ],
    "speakers": ["speaker.entry"],
    "gates": []
  },

  "properties": {
    "interior": false,
    "ground_level": true,
    "high_traffic": true,
    "children_frequent": false,
    "pets_allowed": true,
    "known_hazards": []
  }
}
```

### Hierarchical areas

Areas can nest. For example:

```
backyard
  ├─ pool_area
  ├─ patio
  └─ garden
```

When writing rules, specificity works:

- Rule on `backyard`: "person detected anywhere"
- Rule on `pool_area`: "person in water (attention mode)"

A detection in `pool_area` matches both rules; the more specific one takes precedence (see §10 conflict resolution).

---

## Zones

Zones are precise spatial regions tied to specific cameras. Two types:

### Type 1: Image-space zones (2D pixel polygons)

Used for real-time per-frame decisions (framing, field of regard).

```json
{
  "zone_id": "entry_mat",
  "label": "Entry mat (doorbell view)",
  "area_id": "front_door",
  "camera_id": "doorbell_main",
  "type": "image_space",

  "polygon": [
    { "x": 0.1, "y": 0.6 },    ← normalized coords (0–1)
    { "x": 0.4, "y": 0.6 },
    { "x": 0.4, "y": 1.0 },
    { "x": 0.1, "y": 1.0 }
  ],

  "properties": {
    "footfall_detector": true,
    "face_capture_zone": true,
    "high_confidence_region": true
  }
}
```

### Type 2: World-space zones (3D ground-plane or volumes)

Used for multi-camera reasoning and navigation logic. Requires camera calibration (see §14).

```json
{
  "zone_id": "porch_stairs",
  "label": "Porch stairs",
  "area_id": "front_door",
  "type": "world_space",

  "cameras": ["doorbell_main", "exterior_corner"],

  "geometry": {
    "reference_frame": "site_frame",
    "floor_polygon": [
      { "x": -0.5, "y": 0.2, "z": 0.0 },
      { "x": 0.0, "y": 0.2, "z": 0.0 },
      { "x": 0.0, "y": 0.8, "z": 0.0 },
      { "x": -0.5, "y": 0.8, "z": 0.0 }
    ],
    "height_range": { "min": 0.0, "max": 2.5 },
    "description": "Ground-plane zone covering porch stairs, ~0.5m wide × 0.6m deep, height up to head level"
  },

  "projection_per_camera": {
    "doorbell_main": {
      "projected_polygon": [ ... ],  ← computed from calibration
      "confidence": 0.92,
      "computed_at": "2026-04-15T10:30Z"
    }
  },

  "properties": {
    "stair_zone": true,
    "fall_detection_enabled": true,
    "child_monitoring": false
  }
}
```

**Why world-space zones matter:**

- A person standing on the stairs is detected by either camera — we need consistent reasoning
- `porch_stairs` in world coords is projected into each camera's image space
- Detection `person in porch_stairs` (world) is consistent across both cameras even if pixel locations differ
- Spatial reasoning: "person exited stairs toward door" (world direction) is meaningful

### Height-aware zones

For life-safety scenarios (pools, stairs), height is critical:

```json
{
  "zone_id": "pool_deep_end",
  "type": "world_volume",
  "geometry": {
    "floor_polygon": [ ... ],
    "height_range": {
      "water_surface": 0.9,
      "bottom": 0.0,
      "alert_if_above_water": 1.8
    }
  },

  "anomaly_detection": {
    "person_fully_submerged": {
      "trigger": "head_below_water_surface",
      "alert_class": "urgent"
    },
    "person_motionless_in_water": {
      "trigger": "no_motion > 45s",
      "alert_class": "urgent"
    }
  }
}
```

---

## Adjacency graph

Encodes which areas/zones are reachable from which, and travel time estimates. Used for:

- Cross-session spatial plausibility (can subject reach camera 2 in elapsed time?)
- Blind-spot transit (subject exited area A, next detection in area C; could they have passed through area B in the dark?)

### Adjacency record

```json
{
  "edges": [
    {
      "from_area": "front_door",
      "to_area": "entryway",
      "travel_time_seconds": { "min": 2, "max": 5, "typical": 3 },
      "direct": true,
      "transit_via": null
    },
    {
      "from_area": "entryway",
      "to_area": "living_room",
      "travel_time_seconds": { "min": 1, "max": 3, "typical": 2 },
      "direct": true,
      "transit_via": null
    },
    {
      "from_area": "living_room",
      "to_area": "backyard",
      "travel_time_seconds": { "min": 5, "max": 15, "typical": 8 },
      "direct": true,
      "transit_via": null,
      "description": "Through sliding door"
    },
    {
      "from_area": "backyard",
      "to_area": "street",
      "travel_time_seconds": { "min": 10, "max": 30, "typical": 15 },
      "direct": false,
      "transit_via": ["side_yard"],
      "description": "Exit via side gate"
    }
  ],

  "blind_spots": [
    {
      "from_area": "backyard",
      "to_area": "street",
      "blind_zone": "side_yard_dark_corner",
      "duration_seconds": 5,
      "confidence": 0.7,
      "notes": "Camera gap at side fence corner, subject could disappear 5s"
    }
  ]
}
```

**Plausibility check example:**

```
Session opened: person detected at front_door, 14:00:00
Segment 2: person detected at living_room, 14:00:15

Plausibility check:
  Path: front_door → entryway (3s) → living_room (2s) = ~5s expected
  Actual elapsed: 15s
  Result: geometrically plausible ✓ (15s > 5s, subject could have lingered)

Session opened: person detected at backyard, 14:00:00
Segment 2: person detected at street, 14:00:08

Plausibility check:
  Path: backyard → side_yard → street = ~15s typical
  Actual elapsed: 8s
  Blind spot check: side_yard gap is 5s
  Result: geometrically implausible ✗ (can't exit through blind zone in 8s total)
  → new session (likely different person)
```

---

## Site coordinate frame

A shared reference frame for all world-space zones and calibration.

```json
{
  "site_frame": {
    "name": "123 Oak Street - Site Frame",
    "origin": {
      "description": "Front left corner of house (ground level)",
      "gps": { "lat": 37.7749, "lon": -122.4194, "altitude": 42 }
    },
    "axes": {
      "x": "east (positive = rightward from front of house)",
      "y": "north (positive = away from street, into house/backyard)",
      "z": "up (vertical, positive = higher altitude)"
    },
    "units": "meters",
    "floor_level_z": 0.0,
    "reference_images": [
      {
        "source": "plot_plan",
        "url": "s3://calibration/123_oak_floorplan.png",
        "registered_corners": [ ... ]
      }
    ]
  }
}
```

All cameras are registered relative to this frame. When computing world-space zones or projections, this is the reference.

---

## Complete example: front porch layout

```
Physical setup:
  ┌─────────────────────────┐
  │    Porch (roofed)       │
  ├─────────────────────────┤
  │  [Entry mat] ← Doorbell │  ← Camera "doorbell_main"
  │    (2x3ft)              │
  └─────────────────────────┘
  │       Porch stairs      │
  │      (4 concrete)       │
  └─────────────────────────┘
          │ Street │

Areas:
  - front_door
      ├─ zones: entry_mat, porch_stairs
      ├─ cameras: doorbell_main, exterior_corner

Zones in image-space (doorbell_main):
  - entry_mat: polygon covering mat region (0.1–0.4, 0.6–1.0 normalized)
  - porch_stairs: below entry mat

Zones in world-space:
  - porch_stairs: 3D volume on ground plane, reachable by doorbell_main + exterior_corner

Adjacency:
  front_door → entryway (door open, ~3s)
  entryway → living_room (hallway, ~2s)

Rules written against this:
  "Alert if unknown person detected in entry_mat" (area: front_door)
  "Don't alert if person is Carlos (access profile: Thu 10am–2pm, areas: [porch, backyard])"
  "If person detected in porch_stairs at night, illuminate porch lights" (area: front_door, zone: porch_stairs)
```

---

## Maintenance & calibration updates

When a camera is replaced, repositioned, or a zone moves:

1. Update camera record (location, capabilities, streams)
2. Recompute zone projections for affected world-space zones (§14 calibration)
3. Re-validate adjacency (blind spots may change)
4. Rules written against area/zone remain unchanged

This decoupling means a hardware swap doesn't cascade into rule edits.
