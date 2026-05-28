#!/usr/bin/env python
"""Manually publish CameraConfigEvent to NATS for preprocessor testing.

The canonical flow is ha-agent → NATS → preprocessor. This script is
the manual escape hatch — useful for:

* Testing the preprocessor's pull path against a single real camera
  before ha-agent's publisher wiring lands (Phase 10.1.6.2).
* Reproducing config events in a dev shell to debug subscriber
  behavior.
* Loading a YAML or JSON file of camera configs into a fresh
  preprocessor instance.

Usage:
    # Configure one camera:
    python scripts/dev/publish_camera_config.py configure \\
        --camera-id front_porch \\
        --stream-url 'rtsp://user:pass@192.168.1.20:554/h264Preview_01_sub' \\
        --vendor reolink

    # Remove a camera:
    python scripts/dev/publish_camera_config.py remove --camera-id front_porch

    # Load a YAML file of camera configs:
    python scripts/dev/publish_camera_config.py from-file --path cameras.yaml

YAML schema (for ``from-file``)::

    cameras:
      - camera_id: front_porch
        stream_url: rtsp://user:pass@192.168.1.20:554/h264Preview_01_sub
        stream_protocol: rtsp
        vendor: reolink
        sub_stream: true
      - camera_id: driveway_cam
        stream_url: rtsp://user:pass@192.168.1.21:554/cam/realmonitor?channel=1&subtype=1
        stream_protocol: rtsp
        vendor: dahua

Connects to NATS at ``$NATS_URL`` or ``nats://localhost:4222``. The
preprocessor in the dev compose stack subscribes to the canonical
subjects; this script publishes onto the same subjects so they meet
on the broker.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import nats
import yaml
from sentihome_shared.preprocessor import (
    SUBJECT_CAMERA_CONFIGURED,
    SUBJECT_CAMERA_REMOVED,
    CameraConfigEvent,
)


async def _publish(events: list[tuple[str, CameraConfigEvent]], nats_url: str) -> int:
    nc = await nats.connect(servers=[nats_url])
    try:
        for subject, event in events:
            await nc.publish(subject, event.model_dump_json().encode("utf-8"))
            # ASCII arrow — Windows cp1252 console barfs on '→'.
            print(
                f"published -> {subject}  {event.action} "
                f"camera_id={event.camera_id!r} "
                f"url={event.stream_url!r}"
            )
        await nc.flush()
    finally:
        await nc.drain()
    return 0


def _cmd_configure(args: argparse.Namespace) -> int:
    event = CameraConfigEvent(
        action="configured",
        camera_id=args.camera_id,
        stream_url=args.stream_url,
        stream_protocol=args.stream_protocol,
        vendor=args.vendor,
        sub_stream=args.sub_stream,
    )
    return asyncio.run(
        _publish([(SUBJECT_CAMERA_CONFIGURED, event)], args.nats_url)
    )


def _cmd_remove(args: argparse.Namespace) -> int:
    event = CameraConfigEvent(action="removed", camera_id=args.camera_id)
    return asyncio.run(
        _publish([(SUBJECT_CAMERA_REMOVED, event)], args.nats_url)
    )


def _cmd_from_file(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"ERROR: file not found: {path}")
        return 2
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "cameras" not in raw:
        print("ERROR: file must be a YAML mapping with a 'cameras' list")
        return 2

    events: list[tuple[str, CameraConfigEvent]] = []
    for entry in raw["cameras"]:
        events.append(
            (
                SUBJECT_CAMERA_CONFIGURED,
                CameraConfigEvent(
                    action="configured",
                    camera_id=entry["camera_id"],
                    stream_url=entry["stream_url"],
                    stream_protocol=entry.get("stream_protocol"),
                    vendor=entry.get("vendor"),
                    sub_stream=entry.get("sub_stream", True),
                    refresh_after_seconds=entry.get("refresh_after_seconds"),
                ),
            )
        )
    return asyncio.run(_publish(events, args.nats_url))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--nats-url",
        default=os.environ.get("NATS_URL", "nats://localhost:4222"),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cfg = sub.add_parser("configure", help="publish a 'configured' event")
    p_cfg.add_argument("--camera-id", required=True)
    p_cfg.add_argument("--stream-url", required=True)
    p_cfg.add_argument(
        "--stream-protocol", choices=["rtsp", "hls"], default="rtsp"
    )
    p_cfg.add_argument("--vendor", default=None)
    p_cfg.add_argument(
        "--sub-stream", action=argparse.BooleanOptionalAction, default=True
    )
    p_cfg.set_defaults(func=_cmd_configure)

    p_rm = sub.add_parser("remove", help="publish a 'removed' event")
    p_rm.add_argument("--camera-id", required=True)
    p_rm.set_defaults(func=_cmd_remove)

    p_file = sub.add_parser("from-file", help="publish from a YAML file")
    p_file.add_argument("--path", required=True)
    p_file.set_defaults(func=_cmd_from_file)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
