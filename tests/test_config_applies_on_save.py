#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_config_applies_on_save.py
# Description: A change saved in the plugin's Configure dialog must take effect at
#              once. Until 1.19 closedPrefsConfigUi only updated the plugin's own
#              attributes: every device's HoldTimer kept the hold it was built with,
#              and every camera's worker kept the username and password it started
#              with, so nothing changed until the plugin was reloaded.
#
#              Reuses test_event_log_quiet's stub install, as the other plugin-level
#              tests do, so there is one sys.modules["indigo"] stub and not two.
# Author:      CliveS & Claude Opus 5.5
# Date:        27-09-2026
# Version:     1.0

import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BUNDLE = os.path.join(REPO, "DahuaEvents.indigoPlugin", "Contents", "Server Plugin")

sys.path.insert(0, BUNDLE)
sys.path.insert(0, HERE)

from test_event_log_quiet import _install_stubs, make_plugin  # noqa: E402

_install_stubs()
import plugin as plugin_module           # noqa: E402
from dahua_stream import HoldTimer       # noqa: E402


class FakeDevice:
    def __init__(self, dev_id, name, address="192.168.1.201", klass="person", override=""):
        self.id = dev_id
        self.name = name
        self.pluginProps = {"address": address, "detectionClass": klass,
                            "holdOverride": override}
        self.states = {}

    def updateStateOnServer(self, key, value):
        self.states[key] = value


class FakeWorker:
    """Records the credentials it was built with. Never opens a socket."""

    made = []

    def __init__(self, address, user, password, out_queue, stop_event,
                 status_cb=None, name=None, codes=None):
        self.address = address
        self.user = user
        self.password = password
        self.codes = set(codes or ())
        self.name = name or f"FakeWorker-{address}"
        self.stopped = False
        self._alive = False
        FakeWorker.made.append(self)

    def start(self):
        self._alive = True

    def stop(self):
        self.stopped = True
        self._alive = False

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return self._alive


def _saved(**values):
    """What Indigo hands closedPrefsConfigUi: every field, as strings."""
    base = {"dahuaUser": "viewer", "dahuaPass": "old-pass", "holdSeconds": "20",
            "logActivityToEventLog": False}
    base.update(values)
    return base


class _Harness(unittest.TestCase):

    def setUp(self):
        self._real_worker = plugin_module.CameraWorker
        plugin_module.CameraWorker = FakeWorker
        FakeWorker.made = []
        self.p = make_plugin(dahuaUser="viewer", dahuaPass="old-pass", holdSeconds="20")
        self.settled = []
        self.p._settle_device = lambda dev_id, address, klass: self.settled.append(dev_id)

    def tearDown(self):
        plugin_module.CameraWorker = self._real_worker

    def _start(self, *devices):
        """Register devices the way deviceStartComm does, without its Indigo calls."""
        plugin_module.indigo.devices = {d.id: d for d in devices}
        for d in devices:
            address = d.pluginProps["address"]
            self.p._timers[d.id] = HoldTimer(self.p._hold_for(d))
            self.p._by_camera.setdefault(address, {})[d.pluginProps["detectionClass"]] = d.id
        for address in list(self.p._by_camera):
            self.p._ensure_worker(address)

    def _wait_for_settles(self):
        for t in threading.enumerate():
            if t.name.startswith("DahuaVerdict-"):
                t.join(timeout=2)


class TestHoldAppliesOnSave(_Harness):

    def test_a_new_hold_reaches_every_running_device(self):
        a = FakeDevice(1, "Drive Person")
        b = FakeDevice(2, "Drive Vehicle", klass="vehicle")
        self._start(a, b)
        self.p.closedPrefsConfigUi(_saved(holdSeconds="45"), False)
        self.assertEqual(self.p._timers[1].hold_seconds, 45)
        self.assertEqual(self.p._timers[2].hold_seconds, 45)

    def test_a_device_with_its_own_override_keeps_it(self):
        own = FakeDevice(1, "Drive Person", override="5")
        plain = FakeDevice(2, "Garden Person", address="192.168.1.202")
        self._start(own, plain)
        self.p.closedPrefsConfigUi(_saved(holdSeconds="60"), False)
        self.assertEqual(self.p._timers[1].hold_seconds, 5)
        self.assertEqual(self.p._timers[2].hold_seconds, 60)

    def test_the_new_hold_decides_when_the_device_switches_off(self):
        """The behaviour a user sees, not just the attribute."""
        dev = FakeDevice(1, "Drive Person")
        self._start(dev)
        self.p.closedPrefsConfigUi(_saved(holdSeconds="5"), False)
        timer = self.p._timers[1]
        timer.start(100.0)
        timer.stop(101.0)
        self.assertTrue(timer.tick(106.0), "still on the old 20s hold after saving 5s")

    def test_cancel_changes_nothing(self):
        dev = FakeDevice(1, "Drive Person")
        self._start(dev)
        self.p.closedPrefsConfigUi(_saved(holdSeconds="45"), True)
        self.assertEqual(self.p._timers[1].hold_seconds, 20)


class TestDeviceOverrideAppliesOnSave(_Harness):
    """A device's own Hold override, saved in its own settings dialog."""

    def _save_device(self, dev_id, override, cancelled=False):
        values = {"address": "192.168.1.201", "detectionClass": "person",
                  "holdOverride": override}
        self.p.closedDeviceConfigUi(values, cancelled, "dahuaDetection", dev_id)

    def test_a_new_override_reaches_the_running_device(self):
        dev = FakeDevice(1, "Drive Person")
        self._start(dev)
        self._save_device(1, "7")
        self.assertEqual(self.p._timers[1].hold_seconds, 7)

    def test_clearing_the_override_returns_to_the_plugin_hold(self):
        dev = FakeDevice(1, "Drive Person", override="7")
        self._start(dev)
        self._save_device(1, "")
        self.assertEqual(self.p._timers[1].hold_seconds, 20)

    def test_a_non_number_falls_back_to_the_plugin_hold(self):
        dev = FakeDevice(1, "Drive Person", override="7")
        self._start(dev)
        self._save_device(1, "soon")
        self.assertEqual(self.p._timers[1].hold_seconds, 20)

    def test_cancel_changes_nothing(self):
        dev = FakeDevice(1, "Drive Person")
        self._start(dev)
        self._save_device(1, "7", cancelled=True)
        self.assertEqual(self.p._timers[1].hold_seconds, 20)

    def test_a_device_that_is_not_running_is_left_alone(self):
        self._save_device(99, "7")          # must not raise
        self.assertNotIn(99, self.p._timers)


class TestCredentialsApplyOnSave(_Harness):

    def test_a_new_password_restarts_every_camera_with_it(self):
        a = FakeDevice(1, "Drive Person", address="192.168.1.201")
        b = FakeDevice(2, "Garden Person", address="192.168.1.202")
        self._start(a, b)
        first = list(FakeWorker.made)
        self.p.closedPrefsConfigUi(_saved(dahuaPass="new-pass"), False)
        self._wait_for_settles()

        self.assertTrue(all(w.stopped for w in first), "an old worker was left running")
        running = self.p._workers
        self.assertEqual(set(running), {"192.168.1.201", "192.168.1.202"})
        for worker in running.values():
            self.assertEqual((worker.user, worker.password), ("viewer", "new-pass"))
            self.assertTrue(worker.is_alive())

    def test_each_device_is_checked_again_with_the_new_login(self):
        a = FakeDevice(1, "Drive Person")
        b = FakeDevice(2, "Drive Vehicle", klass="vehicle")
        self._start(a, b)
        self.p.closedPrefsConfigUi(_saved(dahuaUser="admin"), False)
        self._wait_for_settles()
        self.assertEqual(sorted(self.settled), [1, 2])

    def test_the_restarted_worker_still_asks_for_every_device_code(self):
        a = FakeDevice(1, "Drive Person")
        b = FakeDevice(2, "Drive Vehicle", klass="vehicle")
        self._start(a, b)
        self.p.closedPrefsConfigUi(_saved(dahuaPass="new-pass"), False)
        self._wait_for_settles()
        self.assertEqual(self.p._workers["192.168.1.201"].codes,
                         {"SmartMotionHuman", "SmartMotionVehicle"})

    def test_saving_without_a_credential_change_leaves_the_streams_alone(self):
        dev = FakeDevice(1, "Drive Person")
        self._start(dev)
        before = self.p._workers["192.168.1.201"]
        self.p.closedPrefsConfigUi(_saved(holdSeconds="30"), False)
        self.assertIs(self.p._workers["192.168.1.201"], before)
        self.assertFalse(before.stopped)
        self.assertEqual(self.settled, [])


if __name__ == "__main__":
    unittest.main()
