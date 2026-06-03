"""Tests for camera-native motion (Dahua eventManager) parsing + URL derivation."""

from __future__ import annotations

from kukiihome_preprocessor.camera_motion import event_url_from_rtsp, parse_motion_line


def test_parse_start_stop_heartbeat():
    assert parse_motion_line("Code=VideoMotion;action=Start;index=0;data={") is True
    assert parse_motion_line("Code=VideoMotion;action=Stop;index=0") is False
    assert parse_motion_line("Heartbeat") is None
    assert parse_motion_line("--myboundary") is None
    assert parse_motion_line('  "Id" : [ 0 ]') is None
    # a different code (not motion) is ignored
    assert parse_motion_line("Code=CrossLineDetection;action=Start") is None


def test_event_url_decodes_credentials_from_rtsp():
    # RTSP carries the password percent-encoded (%25 == %).
    rtsp = "rtsp://admin:J9v%258emo@192.168.68.89:554/cam/realmonitor?channel=1&subtype=0"
    url, user, pw = event_url_from_rtsp(rtsp)
    assert user == "admin"
    assert pw == "J9v%8emo"  # decoded
    assert url.startswith("http://192.168.68.89/cgi-bin/eventManager.cgi")
    assert "VideoMotion" in url


def test_event_url_handles_no_port_no_creds():
    url, user, pw = event_url_from_rtsp("rtsp://10.0.0.5/stream")
    assert (user, pw) == ("", "")
    assert url.startswith("http://10.0.0.5/cgi-bin/eventManager.cgi")
