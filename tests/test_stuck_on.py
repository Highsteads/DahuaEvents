#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_stuck_on.py
# Description: A detection must never stay on for ever because its Stop was lost.
#
#              HoldTimer.start clears any pending off, and only a Stop schedules
#              one, so a Start followed by a dropped stream (a camera reboot, a
#              network blip, a worker replaced for new codes) left the device ON
#              until the next detection of that class - which on a quiet camera
#              can be hours. 1.21 closes that two ways:
#
#                1. When a camera's stream is lost or replaced, every device of
#                   that camera that is on gets the normal trailing hold, exactly
#                   as if the Stop had arrived. A fresh Start still cancels it.
#                2. A plugin-wide maximum detection time (default 10 minutes, 0 =
#                   off). A device with no Stop that long after its last Start is
#                   cleared, with one INFO line saying no stop arrived.
#
#              Also covers MON-13: two devices for the same camera and class used
#              to share one route silently, and stopping either removed the route
#              for both.
#
#              Reuses test_event_log_quiet's stub install, as the other plugin-level
#              tests do, so there is one sys.modules["indigo"] stub and not two.
# Author:      CliveS & Claude Opus 5.5
# Date:        05-10-2026
# Version:     1.0

import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BUNDLE = os.path.join(REPO, "DahuaEvents.indigoPlugin", "Contents", "Server Plugin")

sys.path.insert(0, BUNDLE)
sys.path.insert(0, HERE)

from test_event_log_quiet import _install_stubs, make_plugin  # noqa: E402

_install_stubs()
import plugin as plugin_module           # noqa: E402
from dahua_stream import HoldTimer       # noqa: E402

ADDRESS = "192.168.1.201"


class FakeDevice:
    """Behaves like Indigo: a state write clears the error unless told not to."""

    def __init__(self, dev_id, name, address=ADDRESS, klass="person", override=""):
        self.id = dev_id
        self.name = name
        self.pluginId = plugin_module.PLUGIN_ID
        self.pluginProps = {"address": address, "detectionClass": klass,
                            "holdOverride": override}
        self.states = {"onOffState": False}
        self.errorState = ""

    def updateStateOnServer(self, key, value, clearErrorState=True):
        self.states[key] = value
        if clearErrorState:
            self.errorState = ""

    def setErrorStateOnServer(self, value):
        self.errorState = value


class FakeDevices(dict):
    """indigo.devices: a dict that also answers iter('self')."""

    def iter(self, which=None):
        return iter(list(self.values()))


def _register(p, *devices):
    """Register devices the way deviceStartComm does, without its Indigo calls."""
    plugin_module.indigo.devices = FakeDevices({d.id: d for d in devices})
    for d in devices:
        p._timers[d.id] = HoldTimer(p._hold_for(d), p.max_on_seconds)
        p._by_camera.setdefault(d.pluginProps["address"], {})[
            d.pluginProps["detectionClass"]] = d.id


# ============================================================
# HoldTimer: the pure logic
# ============================================================

class TestStreamLostArmsTheHold(unittest.TestCase):

    def test_a_device_left_on_clears_one_hold_after_the_stream_is_lost(self):
        t = HoldTimer(20)
        t.start(100.0)
        self.assertFalse(t.stream_lost(105.0), "must not switch off at once")
        self.assertTrue(t.is_on)
        self.assertFalse(t.tick(124.9))
        self.assertTrue(t.tick(125.0))
        self.assertFalse(t.is_on)

    def test_a_fresh_start_after_the_loss_keeps_it_on(self):
        t = HoldTimer(20)
        t.start(100.0)
        t.stream_lost(105.0)
        t.start(110.0)
        self.assertFalse(t.tick(200.0))
        self.assertTrue(t.is_on)

    def test_a_pending_off_is_not_pushed_later(self):
        t = HoldTimer(20)
        t.start(100.0)
        t.stop(101.0)                       # off due at 121
        t.stream_lost(115.0)
        self.assertEqual(t.expires_at, 121.0)

    def test_a_device_that_is_off_is_left_alone(self):
        t = HoldTimer(20)
        self.assertFalse(t.stream_lost(100.0))
        self.assertIsNone(t.expires_at)
        self.assertFalse(t.is_on)

    def test_with_no_hold_the_loss_clears_at_once_like_a_stop(self):
        t = HoldTimer(0)
        t.start(100.0)
        self.assertTrue(t.stream_lost(101.0))
        self.assertFalse(t.is_on)


class TestMaximumDetectionTime(unittest.TestCase):

    def test_a_start_with_no_stop_is_cleared_after_the_maximum(self):
        t = HoldTimer(20, max_on_seconds=600)
        t.start(100.0)
        self.assertFalse(t.tick(699.9))
        self.assertTrue(t.tick(700.0))
        self.assertFalse(t.is_on)
        self.assertTrue(t.cleared_by_cap)

    def test_the_maximum_counts_from_the_latest_start(self):
        t = HoldTimer(20, max_on_seconds=600)
        t.start(100.0)
        t.start(500.0)
        self.assertFalse(t.tick(700.0))
        self.assertTrue(t.tick(1100.0))

    def test_zero_means_no_maximum(self):
        t = HoldTimer(20, max_on_seconds=0)
        t.start(100.0)
        self.assertFalse(t.tick(10_000_000.0))
        self.assertTrue(t.is_on)

    def test_a_normal_hold_expiry_is_not_reported_as_the_cap(self):
        t = HoldTimer(20, max_on_seconds=600)
        t.start(100.0)
        t.stop(110.0)
        self.assertTrue(t.tick(130.0))
        self.assertFalse(t.cleared_by_cap)

    def test_next_deadline_includes_the_cap(self):
        t = HoldTimer(20, max_on_seconds=600)
        t.start(100.0)
        self.assertEqual(t.next_deadline(), 700.0)
        t.stop(110.0)
        self.assertEqual(t.next_deadline(), 130.0)


# ============================================================
# The plugin: statuses arm the hold, the cap logs once
# ============================================================

class TestStreamStatusesArmTheHold(unittest.TestCase):

    def setUp(self):
        self.p = make_plugin(holdSeconds="20")
        self.person = FakeDevice(1, "Drive Person")
        self.vehicle = FakeDevice(2, "Drive Vehicle", klass="vehicle")
        self.other = FakeDevice(3, "Garden Person", address="192.168.1.202")
        _register(self.p, self.person, self.vehicle, self.other)
        for d in (self.person, self.other):
            self.p._timers[d.id].start(100.0)
            d.states["onOffState"] = True

    def _drain(self, status, now=105.0):
        self.p._statuses.put((ADDRESS, status, "detail"))
        with mock.patch.object(plugin_module.time, "monotonic", return_value=now):
            self.p._drain_statuses()

    def _expire(self, now):
        with mock.patch.object(plugin_module.time, "monotonic", return_value=now):
            self.p._expire_holds()

    def _assert_cleared_after_hold(self):
        self.assertTrue(self.person.states["onOffState"], "cleared at once, not after the hold")
        self._expire(124.0)
        self.assertTrue(self.person.states["onOffState"])
        self._expire(125.0)
        self.assertFalse(self.person.states["onOffState"],
                         "a device left on by a lost stream stayed on")
        self.assertTrue(self.other.states["onOffState"],
                        "a different camera's device was cleared")

    def test_reconnecting_arms_the_hold(self):
        self._drain("reconnecting")
        self._assert_cleared_after_hold()

    def test_a_replaced_worker_arms_the_hold(self):
        self._drain(plugin_module.WORKER_STOPPED)
        self._assert_cleared_after_hold()

    def test_unsupported_arms_the_hold(self):
        self._drain("unsupported")
        self._assert_cleared_after_hold()

    def test_a_connected_reaffirm_does_not_arm_the_hold(self):
        self._drain("connected")
        self.assertIsNone(self.p._timers[1].expires_at)
        self._expire(10_000.0 if self.p.max_on_seconds == 0 else 125.0)
        self.assertTrue(self.person.states["onOffState"])

    def test_a_device_that_is_off_gets_no_write(self):
        self._drain("reconnecting")
        self.assertIsNone(self.p._timers[2].expires_at)


class TestCapLogsOnce(unittest.TestCase):

    def test_a_capped_device_is_cleared_with_one_info_line(self):
        p = make_plugin(holdSeconds="20", maxDetectionMinutes="10")
        dev = FakeDevice(1, "Drive Person")
        _register(p, dev)
        p._timers[1].start(100.0)
        dev.states["onOffState"] = True
        with mock.patch.object(plugin_module.time, "monotonic", return_value=699.0):
            p._expire_holds()
        self.assertTrue(dev.states["onOffState"])
        with mock.patch.object(plugin_module.time, "monotonic", return_value=700.0):
            p._expire_holds()
            p._expire_holds()
        self.assertFalse(dev.states["onOffState"])
        lines = [m for m in p.event_log.at_least(20) if "no stop" in m.lower()]
        self.assertEqual(len(lines), 1, p.event_log.messages)
        self.assertIn("Drive Person", lines[0])

    def test_the_default_is_ten_minutes(self):
        p = make_plugin()
        self.assertEqual(p.max_on_seconds, 600)

    def test_a_saved_maximum_reaches_running_timers(self):
        p = make_plugin(holdSeconds="20", maxDetectionMinutes="10")
        dev = FakeDevice(1, "Drive Person")
        _register(p, dev)
        p.closedPrefsConfigUi({"holdSeconds": "20", "maxDetectionMinutes": "3",
                               "dahuaUser": "", "dahuaPass": ""}, False)
        self.assertEqual(p._timers[1].max_on_seconds, 180)

    def test_junk_in_the_pref_falls_back_to_the_default(self):
        p = make_plugin(maxDetectionMinutes="soon")
        self.assertEqual(p.max_on_seconds, 600)
        p = make_plugin(maxDetectionMinutes="0")
        self.assertEqual(p.max_on_seconds, 0)


class TestPrefsValidation(unittest.TestCase):

    def _check(self, value):
        p = make_plugin()
        ok, _values, errors = p.validatePrefsConfigUi(
            {"holdSeconds": "20", "maxDetectionMinutes": value})
        return ok, errors

    def test_good_values_pass(self):
        for value in ("0", "10", "1440", " 5 "):
            ok, errors = self._check(value)
            self.assertTrue(ok, f"{value!r}: {dict(errors)}")

    def test_bad_values_are_refused_on_the_field(self):
        for value in ("-1", "abc", "1441", "", "2.5"):
            ok, errors = self._check(value)
            self.assertFalse(ok, value)
            self.assertIn("maxDetectionMinutes", errors)

    def test_a_bad_hold_is_refused_too(self):
        p = make_plugin()
        ok, _v, errors = p.validatePrefsConfigUi(
            {"holdSeconds": "-3", "maxDetectionMinutes": "10"})
        self.assertFalse(ok)
        self.assertIn("holdSeconds", errors)


# ============================================================
# MON-13: one device per camera and class
# ============================================================

class TestDuplicateRoutes(unittest.TestCase):

    def test_a_second_device_for_the_same_camera_and_class_is_refused(self):
        p = make_plugin()
        existing = FakeDevice(1, "Drive Person", address="Drive-Cam.local")
        plugin_module.indigo.devices = FakeDevices({1: existing})
        ok, _v, errors = p.validateDeviceConfigUi(
            {"address": " drive-cam.local ", "detectionClass": "person",
             "holdOverride": ""}, "dahuaDetection", 99)
        self.assertFalse(ok)
        self.assertIn("Drive Person", errors.get("detectionClass", "")
                      + errors.get("address", ""))

    def test_editing_the_device_itself_is_allowed(self):
        p = make_plugin()
        existing = FakeDevice(1, "Drive Person")
        plugin_module.indigo.devices = FakeDevices({1: existing})
        ok, _v, errors = p.validateDeviceConfigUi(
            {"address": ADDRESS, "detectionClass": "person", "holdOverride": ""},
            "dahuaDetection", 1)
        self.assertTrue(ok, dict(errors))

    def test_another_class_on_the_same_camera_is_allowed(self):
        p = make_plugin()
        plugin_module.indigo.devices = FakeDevices({1: FakeDevice(1, "Drive Person")})
        ok, _v, errors = p.validateDeviceConfigUi(
            {"address": ADDRESS, "detectionClass": "vehicle", "holdOverride": ""},
            "dahuaDetection", 99)
        self.assertTrue(ok, dict(errors))

    def test_a_blank_address_is_refused(self):
        p = make_plugin()
        plugin_module.indigo.devices = FakeDevices({})
        ok, _v, errors = p.validateDeviceConfigUi(
            {"address": "  ", "detectionClass": "person", "holdOverride": ""},
            "dahuaDetection", 99)
        self.assertFalse(ok)
        self.assertIn("address", errors)

    def test_the_factory_refuses_a_camera_class_another_group_already_has(self):
        p = make_plugin()
        plugin_module.indigo.devices = FakeDevices({1: FakeDevice(1, "Drive Person")})
        ok, _v, errors = p.validateDeviceFactoryUi(
            {"address": ADDRESS, "cameraName": "Drive Again",
             "want_person": True, "want_vehicle": False}, [])
        self.assertFalse(ok)
        self.assertIn("Drive Person", errors.get("address", ""))

    def test_the_factory_allows_its_own_group(self):
        p = make_plugin()
        plugin_module.indigo.devices = FakeDevices({1: FakeDevice(1, "Drive Person")})
        ok, _v, errors = p.validateDeviceFactoryUi(
            {"address": ADDRESS, "cameraName": "Drive",
             "want_person": True, "want_vehicle": True}, [1])
        self.assertTrue(ok, dict(errors))

    def test_stopping_a_device_that_does_not_hold_the_route_leaves_it(self):
        p = make_plugin()
        owner = FakeDevice(1, "Drive Person")
        dup = FakeDevice(2, "Drive Person copy")
        _register(p, dup, owner)             # owner registered last, so it holds the route
        self.assertEqual(p._by_camera[ADDRESS]["person"], 1)
        p._stop_worker = lambda address: self.fail("stopped the camera's stream")
        p.deviceStopComm(dup)
        self.assertEqual(p._by_camera[ADDRESS]["person"], 1,
                         "stopping a duplicate removed the other device's route")
        self.assertIn(1, p._timers)

    def test_a_duplicate_starting_later_is_put_in_error_not_given_the_route(self):
        p = make_plugin()
        owner = FakeDevice(1, "Drive Person")
        dup = FakeDevice(2, "Drive Person copy")
        dup.pluginProps["SupportsOnState"] = True
        _register(p, owner)
        plugin_module.indigo.devices[2] = dup
        p._ensure_worker = lambda address: self.fail("started a stream for a duplicate")
        p.deviceStartComm(dup)
        self.assertEqual(p._by_camera[ADDRESS]["person"], 1)
        self.assertNotIn(2, p._timers)
        self.assertIn("Drive Person", dup.errorState)


if __name__ == "__main__":
    unittest.main(verbosity=2)
