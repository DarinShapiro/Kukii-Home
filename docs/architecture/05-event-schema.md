# 05 — Event & Message Schemas

**Purpose:** The canonical shapes that flow on the bus. Stable contracts so producers and consumers evolve independently.
**Status:** drafting

---

## Identifiers & versioning

Every event and message carries:

```json
{
  "schema_version": "1.0",
  "event_id": "camera-{source}-{ts}-{track_id_primary}",
  "timestamp": "2026-05-23T14:33:22.456Z",
  "correlation_id": "session-abc123"
}
```

Breaking schema changes increment major version. Clients declare accepted versions; servers reject mismatched versions with structured error.

---

## Trigger event (ingress)

### Camera event (ONVIF webhook / DVR push)

```json
{
  "schema_version": "1.0",
  "event_type": "camera.trigger",
  "event_id": "camera-doorbell-20260523T143322-track42",
  "source": "dvr_webhook | camera_native_ai",
  "camera_id": "doorbell_main",
  "area_id": "front_door",
  "ts": "2026-05-23T14:33:22.456Z",
  
  "trigger_kind": "motion | face | package | vehicle | person | custom_class",
  "confidence": 0.87,
  "track_id_primary": 42,
  "spatial": {
    "bbox": [100, 200, 450, 550],
    "zone_id": "entry_mat" | null,
    "region": "top-left | center | bottom"
  },
  
  "clip": {
    "clip_uri": "s3://clips/doorbell/20260523T143322_track42.mp4",
    "duration_ms": 5000,
    "frame_count": 8,
    "fps": 8
  },
  
  "triage_context": {
    "dedup_key": "SHA256(source+camera_id+round(ts,5s)+track_id)",
    "session_ref": "session-abc123" | null,
    "coalesced": false
  },
  
  "privacy_tier": "local_only | can_cache | cloud_eligible",
  "world_state_snapshot_ref": "snapshot-20260523T143300"
}
```

### HA synthetic event (polling)

```json
{
  "schema_version": "1.0",
  "event_type": "ha.state_change",
  "event_id": "ha-poll-{hash}",
  "source": "ha_poller",
  "source_kind": "sensor | lock | presence | ecosystem_alert | automation | device",
  
  "entity_id": "lock.back_door",
  "area_id": "back_door",
  "zone_id": null,
  "ts": "2026-05-23T14:33:22.456Z",
  
  "state_change": {
    "previous_state": "locked",
    "current_state": "unlocked",
    "changed_attribute": null
  },
  
  "metadata": {
    "entity_name": "Back Door Lock",
    "device_class": "lock",
    "changed_by": "automation.security_night" | "manual" | null,
    "attributes": {
      "battery_level": 87,
      "last_activity": "door_opened"
    }
  },
  
  "significance": "high | normal | low",
  "world_state_snapshot_ref": "snapshot-20260523T143300",
  "privacy_tier": "local_only"
}
```

---

## Enriched event (post-detector)

Output of `detector.enrich(frame_uris)`. This is appended to the trigger event and passed to context assembly.

```json
{
  "schema_version": "1.0",
  "event_id": "camera-doorbell-20260523T143322-track42",
  "enrichment_ts": "2026-05-23T14:33:25.100Z",
  "enrichment_latency_ms": 2644,
  
  "objects": [
    {
      "track_id": 42,
      "class": "person",
      "confidence": 0.94,
      "bbox": [100, 200, 450, 550],
      "center": [275, 375],
      "area_coverage": 0.18,
      "visibility": "frontal | profile | partial_occlusion",
      
      "face": {
        "detected": true,
        "bbox": [120, 210, 320, 380],
        "size_pixels": 36,
        "quality": "high | medium | low | unresolved",
        "pose": { "yaw": 5, "pitch": -8, "roll": 0 },
        "blur": 0.15,
        "landmarks": 68,
        
        "recognition": {
          "embedding": "base64:...",
          "embedding_model": "arcface_r100",
          "gallery_match": {
            "candidate": "sarah_resident",
            "similarity": 0.92,
            "confidence_tier": "high" | "tentative" | "unknown"
          },
          "identity_claim": "sarah_resident | unknown"
        }
      },
      
      "body": {
        "reid_embedding": "base64:...",
        "reid_model": "osnet_x1_0",
        "session_match_confidence": 0.87,
        "pose_keypoints": [
          {"joint": "head", "x": 210, "y": 220, "confidence": 0.98},
          {"joint": "left_shoulder", "x": 155, "y": 290, "confidence": 0.95},
          ...
        ],
        "pose_summary": "standing | walking | running | sitting | lying_down | climbing | falling",
        "gait": "normal | hurried | unusual",
        "height_estimate_cm": 175,
        "posture": "upright | bending | crouching | stretching"
      },
      
      "attributes": {
        "clothing_color": ["dark_blue", "white"],
        "clothing_type": ["shirt", "jeans"],
        "held_objects": ["phone"],
        "visible_features": {
          "hood": true,
          "glasses": false,
          "mask": false,
          "hat": true,
          "facial_hair": null
        }
      }
    }
  ],
  
  "vehicles": [
    {
      "track_id": 43,
      "class": "car",
      "confidence": 0.91,
      "bbox": [500, 100, 800, 350],
      "color": "silver",
      "make_model": "Toyota Camry",
      
      "plate": {
        "detected": true,
        "ocr_text": "ABC1234" | "ABC12?4",
        "ocr_confidence": 0.97,
        "partial": false,
        "identity_claim": "vehicle_registered_resident | unknown_vehicle"
      }
    }
  ],
  
  "animals": [
    {
      "track_id": 44,
      "class": "dog",
      "confidence": 0.89,
      "bbox": [50, 300, 180, 450],
      
      "face_recognition": {
        "detected": true,
        "embedding": "base64:...",
        "gallery_match": {
          "candidate": "max_golden_retriever",
          "similarity": 0.88,
          "confidence_tier": "high"
        },
        "identity_claim": "max_golden_retriever | unknown_dog"
      },
      
      "behavior": "standing | sitting | running | playing | eating | resting | distress"
    }
  ],
  
  "annotations": {
    "rendered_image_uri": "s3://annotated/doorbell/20260523T143322_track42_ann.jpg",
    "clean_crop_uri": "s3://crops/doorbell/20260523T143322_track42_clean.jpg",
    "tight_face_crop_uri": "s3://crops/doorbell/20260523T143322_face.jpg"
  },
  
  "scene_context": {
    "lighting": "good | dim | low_light | very_low_light",
    "weather": "clear | overcast | rain | snow",
    "motion_blur": 0.12,
    "camera_motion": false,
    "obstruction": "none | partial | significant"
  },
  
  "quality_flags": {
    "face_resolution_low": false,
    "face_oblique": false,
    "face_occluded": false,
    "plate_partial": false,
    "high_confidence_enrichment": true,
    "enrichment_failed": false,
    "gpu_saturation": false
  }
}
```

---

## VLM request

```json
{
  "schema_version": "1.0",
  "request_id": "vlm-req-uuid",
  "event_id": "camera-doorbell-20260523T143322-track42",
  
  "frames": {
    "frame_uris": [
      "s3://annotated/doorbell/frame_1_ann.jpg",
      "s3://annotated/doorbell/frame_3_ann.jpg",
      "s3://annotated/doorbell/frame_5_ann.jpg",
      "s3://clean/doorbell/frame_1_clean.jpg",
      "s3://clean/doorbell/frame_3_clean.jpg",
      "s3://clean/doorbell/frame_5_clean.jpg"
    ],
    "frame_count": 6,
    "frame_selection": "detector_guided",
    "annotation_style": "thin_boxes_transparent_fills",
    "includes_clean": true
  },
  
  "context": {
    "enrichment": { /* enriched event payload above */ },
    "world_state": {
      "current_time": "2026-05-23T14:33:22Z",
      "time_of_day": "afternoon",
      "occupancy": ["resident_1"],
      "alarm_state": "disarmed",
      "weather": "sunny, 72F",
      "active_situational_contexts": [
        "Birthday party today 3–6pm, ~10 guests expected"
      ],
      "active_transient_intents": []
    },
    
    "retrieved_rules": [
      {
        "rule_id": "rule-doorbell-guest-alert",
        "text": "Alert on person detected at front door after 3pm on weekends",
        "scope": "camera (doorbell_main)",
        "conditions_matched": true
      },
      {
        "rule_id": "rule-deliveries",
        "text": "Alert if delivery driver detected",
        "scope": "area (front_door)",
        "conditions_matched": false
      }
    ],
    
    "identity_candidates": [
      {
        "track_id": 42,
        "identity_ref": "guest_participant_1",
        "confidence": 0.87,
        "identity_source": "face_recognition",
        "access_profile": "temporary_guest"
      }
    ],
    
    "episodic_summaries": [
      "Yesterday 4pm: Similar person visited, greeted by resident_1"
    ]
  },
  
  "prompt": {
    "persona": "Home AI security assistant. Reason over visual observations and context.",
    "cached_prefix": "You are a home AI...[system instructions, persona, rule context]",
    "variable_tail": "Event: Person at front door, afternoon. Birthday party in progress...",
    "temperature": 0,
    "model_hint": "supports_visual_reasoning: true"
  },
  
  "privacy_tier": "local_only",
  "backend_preferred": "local | cloud | any",
  "timeout_ms": 8000
}
```

## VLM response

```json
{
  "schema_version": "1.0",
  "request_id": "vlm-req-uuid",
  "response_id": "vlm-res-uuid",
  "event_id": "camera-doorbell-20260523T143322-track42",
  "response_ts": "2026-05-23T14:33:29.400Z",
  "latency_ms": 4278,
  
  "observation": "Person in casual clothing at front door. Matches identity of guest expected for party. Greeting visitor.",
  
  "decision": {
    "alert_required": true,
    "criticality": "alert | warning | info | no_action",
    "confidence": 0.91,
    "confidence_limiting_factors": [
      "face_partially_oblique"
    ],
    
    "rules_fired": ["rule-doorbell-guest-alert"],
    "rules_suppressed": [],
    
    "action": {
      "primary": "notify | escalate | ask | open_session | speak | none",
      "targets": ["resident_1"],
      "message": "Guest arrival detected at front door",
      "evidence_clip": true,
      "evidence_uri": "s3://clips/doorbell/20260523T143322_track42.mp4"
    },
    
    "deeper_assessment": false,
    "deeper_assessment_reason": null,
    
    "journey": {
      "journey_open": false,
      "journey_reason": null,
      "priority_boost": null
    },
    
    "attention_mode": null,
    "sequence_watch": null
  },
  
  "backend": "ollama_local_5b",
  "model": "llava_1.5",
  "cached": false
}
```

---

## Reasoner request / response

### Request

Reasoner receives VLM response + triggered rules + world state and decides final action. This is deterministic (no second LLM call).

```json
{
  "schema_version": "1.0",
  "event_id": "camera-doorbell-20260523T143322-track42",
  "vlm_response_id": "vlm-res-uuid",
  
  "vlm_output": { /* VLM response above */ },
  "rules_fired": [
    {
      "rule_id": "rule-doorbell-guest-alert",
      "severity": "alert",
      "actions": [
        {"type": "notify", "targets": ["resident_1"]}
      ],
      "scope": "camera"
    }
  ],
  
  "policy_check": {
    "unlocks_required": false,
    "alarms_required": false,
    "sirens_required": false
  }
}
```

### Response

```json
{
  "schema_version": "1.0",
  "event_id": "camera-doorbell-20260523T143322-track42",
  "decision_ts": "2026-05-23T14:33:30.100Z",
  
  "final_action": {
    "type": "notify",
    "targets": ["resident_1"],
    "message": "Guest arrival detected at front door",
    "priority": "normal",
    "evidence": {
      "clip_uri": "s3://clips/doorbell/20260523T143322_track42.mp4",
      "snapshot_uri": "s3://snapshots/doorbell/20260523T143322_track42.jpg",
      "annotation_uri": "s3://annotated/doorbell/20260523T143322_track42_ann.jpg"
    }
  },
  
  "session": {
    "action": "open" | "update" | "close" | "none",
    "session_id": "session-abc123" | null,
    "subject_descriptor": "Guest at front door"
  },
  
  "memory_writes": [
    {
      "store": "episodic",
      "action": "append_segment",
      "session_id": "session-abc123"
    },
    {
      "store": "visit_ledger",
      "action": "update",
      "subject_ref": "guest_participant_1",
      "area": "front_door"
    }
  ],
  
  "device_actions": [],
  
  "audit": {
    "rules_evaluated": 2,
    "rules_fired": 1,
    "policy_gates_passed": true,
    "vlm_calls": 1
  }
}
```

---

## Session / journey update

Sent when a session is opened, segments appended, or closed.

```json
{
  "schema_version": "1.0",
  "session_id": "session-abc123",
  "event_id": "camera-doorbell-20260523T143322-track42",
  "ts": "2026-05-23T14:33:30.100Z",
  
  "action": "open | append_segment | escalate | close",
  
  "segment": {
    "segment_id": "seg-1",
    "camera_id": "doorbell_main",
    "subject_track": "track42",
    "frame_uri": "s3://snapshots/doorbell/20260523T143322_track42.jpg",
    "clip_uri": "s3://clips/doorbell/20260523T143322_track42.mp4",
    "identity": "guest_participant_1",
    "confidence": 0.91,
    "ts": "2026-05-23T14:33:22.456Z"
  },
  
  "journey_state": {
    "journey_score": 2.1,
    "segment_count": 1,
    "area_span": ["front_door"],
    "elapsed_time_s": 8,
    "status": "active"
  }
}
```

---

## Action / notification message

Final message sent to notification delivery.

```json
{
  "schema_version": "1.0",
  "action_id": "action-uuid",
  "event_id": "camera-doorbell-20260523T143322-track42",
  
  "action_type": "push_notification | speak | sms | ask",
  "targets": ["resident_1"],
  
  "content": {
    "title": "Guest arrival",
    "message": "Guest arrival detected at front door",
    "priority": "normal | urgent",
    "evidence": {
      "clip_uri": "s3://clips/doorbell/20260523T143322_track42.mp4",
      "thumbnail_uri": "s3://snapshots/doorbell/20260523T143322_track42.jpg"
    }
  },
  
  "explanation": {
    "why": "Rule 'doorbell_guest_alert' matched: person at front door during party",
    "confidence": 0.91,
    "can_dismiss": true,
    "can_edit_rule": true
  }
}
```

---

## Privacy & routing hints

Carried on every event message:

```json
{
  "privacy_tier": "local_only | can_cache | cloud_eligible",
  "cloud_eligible_reason": "no resident faces | scene-only | enrichment only",
  
  "preferred_backend": "local | cloud | any",
  "backend_capability_required": "supports_visual_reasoning | fast_inference | none",
  
  "priority": "urgent | normal | background",
  "retention": {
    "delete_after_days": 30,
    "archive_after_days": 7,
    "store_in": "local | cloud | both"
  }
}
```

---

## Schema evolution rules

1. **Backward compatibility:** New fields are optional; old clients ignore unknown fields
2. **Breaking changes:** New major version; servers reject requests with mismatched major versions
3. **Versioning:** `schema_version` in every message (major.minor)
4. **Migration window:** Support N-1 versions for 30 days; clients warned of deprecation
5. **Enum extensions:** New enum values are acceptable; old clients will see unknown strings gracefully
6. **Field deprecation:** Mark as `@deprecated` 30 days before removal; old clients still work
