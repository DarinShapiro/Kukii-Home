# services/notify/

Notification dispatcher. Push notifications, voice/TTS via HA speakers, in-app notifications, conversational `ask` flows. Handles confidence tier routing, quiet hours, occupancy-aware delivery, last-responder bias mitigation.

**Architecture:** [§15](../../docs/architecture/15-alerting-and-actions.md)

## Responsibilities

- `notify.push(targets, message, evidence_ref, priority)`
- `notify.speak(message, zone)` — TTS via HA speakers
- `notify.ask(question, evidence_ref, response_callback_id)` — conversational confirmation, suspends pipeline

## Status

Skeleton. Implementation tracked in [`planning/epics/08-action-dispatch.md`](../../planning/epics/).
