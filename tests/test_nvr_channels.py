#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_nvr_channels.py
# Description: 1.23 - an NVR sends every channel's detections on one stream, each
#              line carrying index=<channel, from 0>. Before 1.23 the index was
#              thrown away, so:
#                - every channel's events reached the same device
#                - a Stop on one channel started the off countdown while another
#                  channel was still detecting
#                - the checks only ever read channel 1's ([0]) settings, and an
#                  IVS rule drawn on any channel counted for every channel
#              These tests pin the fix, and that a device with no channel (every
#              ordinary camera) behaves exactly as before.
# Author:      CliveS & Claude Opus 5.5
# Date:        01-11-2026
# Version:     1.0

import pytest

import dahua_probe
import plugin as plugin_module
from dahua_stream import Event, HoldTimer
from test_stuck_on import FakeDevice, FakeDevices

NVR = "192.168.1.50"


def _device(dev_id, name, klass="person", channel=""):
    dev = FakeDevice(dev_id, name, address=NVR, klass=klass)
    dev.pluginProps["channel"] = channel
    dev.pluginProps["SupportsOnState"] = True
    return dev


def _plugin_with(*devices):
    """Start the devices through the real deviceStartComm, with no threads and
    no network: the settle and the worker are stubbed out."""
    p = plugin_module.Plugin(plugin_module.PLUGIN_ID, "DahuaEvents", "1.23",
                             {"holdSeconds": "20", "maxDetectionMinutes": "10"})
    p._start_settle = lambda *a, **k: None
    p._ensure_worker = lambda address: None
    plugin_module.indigo.devices = FakeDevices({d.id: d for d in devices})
    for d in devices:
        p.deviceStartComm(d)
    return p


def _event(p, action, index, code="SmartMotionHuman", now=1000.0):
    p._apply((NVR, Event(code, action, index)), now)


# ------------------------------------------------------------ the setting

@pytest.mark.parametrize("raw, expected", [
    ("", (True, None)), ("  ", (True, None)), (None, (True, None)),
    ("1", (True, 1)), (" 16 ", (True, 16)), ("256", (True, 256)),
    ("0", (False, None)), ("257", (False, None)), ("-1", (False, None)),
    ("two", (False, None)), ("1.5", (False, None)),
])
def test_parse_channel(raw, expected):
    assert plugin_module.parse_channel(raw) == expected


def test_channels_overlap():
    overlap = plugin_module.channels_overlap
    assert overlap(None, None) and overlap(None, 3) and overlap(3, None) and overlap(3, 3)
    assert not overlap(2, 3)


def test_a_device_with_no_channel_keeps_its_old_route_key():
    """Existing devices, and the existing tests, file a device under its plain
    class name. That must not change."""
    assert plugin_module.route_key("person", None) == "person"
    assert plugin_module.route_key("person", 3) == ("person", 3)


# ------------------------------------------------------------ routing

def test_each_channel_reaches_only_its_own_device():
    ch1, ch3 = _device(1, "Drive Person", channel="1"), _device(2, "Garden Person", channel="3")
    p = _plugin_with(ch1, ch3)
    _event(p, "Start", 2)                           # index 2 = channel 3
    assert ch3.states["onOffState"] is True
    assert ch1.states["onOffState"] is False


def test_a_channel_with_no_device_is_ignored():
    ch1 = _device(1, "Drive Person", channel="1")
    p = _plugin_with(ch1)
    _event(p, "Start", 1)                           # channel 2: nobody listening
    assert ch1.states["onOffState"] is False


def test_a_stop_on_another_channel_does_not_touch_this_device():
    ch1, ch2 = _device(1, "Drive Person", channel="1"), _device(2, "Garden Person", channel="2")
    p = _plugin_with(ch1, ch2)
    _event(p, "Start", 0)
    _event(p, "Stop", 1, now=1001.0)
    assert p._timers[1].expires_at is None, "channel 2's Stop started channel 1's off"


def test_an_ordinary_camera_is_unchanged():
    cam = _device(1, "Drive Person")                # no channel
    p = _plugin_with(cam)
    _event(p, "Start", 0)
    assert cam.states["onOffState"] is True
    _event(p, "Stop", 0, now=1005.0)
    assert p._timers[1].expires_at == 1025.0


def test_a_device_for_every_channel_stays_on_until_all_have_stopped():
    every = _device(1, "NVR Person")                # no channel = every channel
    p = _plugin_with(every)
    _event(p, "Start", 0)
    _event(p, "Start", 1, now=1001.0)
    _event(p, "Stop", 1, now=1002.0)
    assert p._timers[1].expires_at is None, "off scheduled while channel 1 still detecting"
    _event(p, "Stop", 0, now=1003.0)
    assert p._timers[1].expires_at == 1023.0


def test_the_events_reach_the_right_class_on_a_channel():
    person = _device(1, "Drive Person", channel="2")
    vehicle = _device(2, "Drive Vehicle", klass="vehicle", channel="2")
    p = _plugin_with(person, vehicle)
    _event(p, "Start", 1, code="SmartMotionVehicle")
    assert vehicle.states["onOffState"] is True
    assert person.states["onOffState"] is False


# ------------------------------------------------------------ duplicates

def test_a_second_device_for_the_same_channel_is_refused():
    a, b = _device(1, "Drive Person", channel="2"), _device(2, "Drive copy", channel="2")
    p = _plugin_with(a, b)
    assert 2 not in p._timers
    assert "Drive Person" in b.errorState


def test_a_channel_device_beside_an_every_channel_device_is_refused():
    every, ch2 = _device(1, "NVR Person"), _device(2, "Garden Person", channel="2")
    p = _plugin_with(every, ch2)
    assert 2 not in p._timers


def test_two_different_channels_may_share_a_class():
    a, b = _device(1, "Drive Person", channel="1"), _device(2, "Garden Person", channel="2")
    p = _plugin_with(a, b)
    assert {1, 2} <= set(p._timers)


def test_an_invalid_channel_puts_the_device_in_error():
    bad = _device(1, "Drive Person", channel="nine")
    p = _plugin_with(bad)
    assert 1 not in p._timers
    assert "channel" in bad.errorState


def test_stopping_a_channel_device_removes_only_its_own_route():
    a, b = _device(1, "Drive Person", channel="1"), _device(2, "Garden Person", channel="2")
    p = _plugin_with(a, b)
    p._stop_worker = lambda address: pytest.fail("stopped the stream the other device needs")
    p.deviceStopComm(a)
    assert p._by_camera[NVR] == {("person", 2): 2}


def test_the_dialog_refuses_a_bad_or_overlapping_channel():
    every = _device(1, "NVR Person")
    p = _plugin_with(every)
    ok, _v, errors = p.validateDeviceConfigUi(
        {"address": NVR, "detectionClass": "person", "channel": "0"}, "dahuaDetection", 99)
    assert not ok and "channel" in errors
    ok, _v, errors = p.validateDeviceConfigUi(
        {"address": NVR, "detectionClass": "person", "channel": "2"}, "dahuaDetection", 99)
    assert not ok and "NVR Person" in errors["detectionClass"]


def test_changing_the_channel_restarts_the_device():
    old, new = _device(1, "Drive Person", channel="1"), _device(1, "Drive Person", channel="2")
    assert plugin_module.Plugin.didDeviceCommPropertyChange(old, new)


def test_the_worker_asks_for_each_class_once_whatever_the_channels():
    a = _device(1, "Drive Person", channel="1")
    b = _device(2, "Garden Vehicle", klass="vehicle", channel="2")
    p = _plugin_with(a, b)
    assert p._codes_for_camera(NVR) == {"SmartMotionHuman", "SmartMotionVehicle"}


# ------------------------------------------------------------ HoldTimer

def test_a_stop_from_a_channel_that_never_started_is_ignored():
    t = HoldTimer(20)
    t.start(100.0, 0)
    assert t.stop(101.0, 5) is False
    assert t.expires_at is None


def test_stream_lost_clears_every_channel():
    t = HoldTimer(20)
    t.start(100.0, 0)
    t.start(100.0, 1)
    t.stream_lost(105.0)
    assert t.expires_at == 125.0
    assert t.active_sources == frozenset()


def test_a_lost_stop_on_one_channel_cannot_outlast_the_cap():
    t = HoldTimer(20, max_on_seconds=600)
    t.start(100.0, 1)                     # channel 2's Stop never arrives
    t.start(110.0, 0)
    t.stop(111.0, 0)
    assert t.expires_at is None           # channel 2 still counts as detecting
    assert t.tick(710.0) is True and t.cleared_by_cap
    assert t.active_sources == frozenset()
    t.start(800.0, 0)
    t.stop(801.0, 0)
    assert t.expires_at == 821.0, "the stale channel outlived the cap"


# ------------------------------------------------------------ the checks

SMD = ("table.SmartMotionDetect[0].Enable=true\r\n"
       "table.SmartMotionDetect[1].Enable=false\r\n"
       "table.SmartMotionDetect[2].Enable=true\r\n")
MOTION = ("table.MotionDetect[0].Enable=true\r\n"
          "table.MotionDetect[1].Enable=true\r\n"
          "table.MotionDetect[2].Enable=true\r\n")
EVENTS = "events[0]=SmartMotionHuman\r\nevents[1]=SmartMotionVehicle\r\nevents[2]=CrossLineDetection\r\n"
RULES = ("table.VideoAnalyseRule[2][0].Type=CrossLineDetection\r\n"
         "table.VideoAnalyseRule[2][0].Enable=true\r\n"
         "table.VideoAnalyseRule[2][0].Name=Gate\r\n")


def test_the_enable_flag_is_read_for_the_channel_asked_about():
    assert dahua_probe.parse_enable_flag(SMD, "SmartMotionDetect", 1) is False
    assert dahua_probe.parse_enable_flag(SMD, "SmartMotionDetect", 2) is True
    assert dahua_probe.parse_enable_flag(SMD, "SmartMotionDetect", 7) is None
    assert dahua_probe.parse_enable_flag(SMD, "SmartMotionDetect") is True   # as before


def test_smart_motion_is_judged_on_the_devices_own_channel():
    assert dahua_probe.assess(EVENTS, SMD, MOTION, 1)[0] == dahua_probe.DISABLED
    verdict, reason = dahua_probe.assess(EVENTS, SMD, MOTION, 2)
    assert verdict == dahua_probe.CAPABLE and "channel 3" in reason
    assert dahua_probe.assess(EVENTS, SMD, MOTION)[0] == dahua_probe.CAPABLE


def test_a_rule_on_another_channel_does_not_count():
    assert dahua_probe.assess_ivs(EVENTS, RULES, "crossline", 0)[0] == dahua_probe.NO_RULE
    assert dahua_probe.assess_ivs(EVENTS, RULES, "crossline", 2)[0] == dahua_probe.CAPABLE
    # No channel: any channel's rule counts, as before 1.23.
    assert dahua_probe.assess_ivs(EVENTS, RULES, "crossline")[0] == dahua_probe.CAPABLE


def test_the_device_channel_reaches_the_check_counted_from_zero(monkeypatch):
    seen = {}
    monkeypatch.setattr(dahua_probe, "probe",
                        lambda a, u, pw, timeout=8, channel_index=None:
                        seen.setdefault("probe", channel_index) or (dahua_probe.CAPABLE, "ok"))
    monkeypatch.setattr(dahua_probe, "probe_ivs",
                        lambda a, u, pw, k, timeout=8, channel_index=None:
                        seen.setdefault("ivs", channel_index) or (dahua_probe.CAPABLE, "ok"))
    p = plugin_module.Plugin(plugin_module.PLUGIN_ID, "DahuaEvents", "1.23", {})
    p._verdict_for(NVR, "person", 3)
    p._verdict_for(NVR, "crossline", 1)
    assert seen == {"probe": 2, "ivs": 0}
    seen.clear()
    p._verdict_for(NVR, "person")
    assert seen == {"probe": None}
