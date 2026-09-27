#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_error_state_survives.py
# Description: A device's error state must survive the plugin's routine state
#              writes. Indigo's updateStateOnServer() CLEARS the error state unless
#              it is passed clearErrorState=False, so before 1.20 the midnight
#              detectionsToday reset wiped "no rule drawn on the camera" every night
#              (a blocked device never detects, so its day stamp is always stale),
#              and a stream "reconnecting" broadcast wiped it too. Anything reading
#              errorState, such as Device Health Monitor, then saw a healthy device.
#              The FakeDevice clears on write exactly the way Indigo does.
# Author:      CliveS & Claude Opus 5.5
# Date:        27-09-2026
# Version:     1.0

import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BUNDLE = os.path.join(REPO, "DahuaEvents.indigoPlugin", "Contents", "Server Plugin")

sys.path.insert(0, BUNDLE)
sys.path.insert(0, HERE)

from test_event_log_quiet import _install_stubs, make_plugin  # noqa: E402

_install_stubs()
import plugin as plugin_module           # noqa: E402
import dahua_probe                       # noqa: E402
from dahua_stream import HoldTimer       # noqa: E402

ADDRESS = "192.168.1.200"


class ClearingDevice:
    """Behaves like an Indigo device: any state write clears the error state
    unless the caller passes clearErrorState=False."""

    def __init__(self, dev_id, name="Patio Tripwire"):
        self.id = dev_id
        self.name = name
        self.states = {}
        self.errorState = ""

    def updateStateOnServer(self, key, value, clearErrorState=True):
        self.states[key] = value
        if clearErrorState:
            self.errorState = ""

    def setErrorStateOnServer(self, value):
        self.errorState = value


def _running(verdict, klass="crossline"):
    """A plugin with one running device settled to `verdict`."""
    p = make_plugin()
    dev = ClearingDevice(1)
    plugin_module.indigo.devices = {dev.id: dev}
    p._by_camera.setdefault(ADDRESS, {})[klass] = dev.id
    p._timers[dev.id] = HoldTimer(20)
    p._verdict_for = lambda addr, k: (verdict, "because")
    p._settle_device(dev.id, ADDRESS, klass)
    return p, dev


class TestTheFakeClearsLikeIndigo(unittest.TestCase):
    """The tests below are only worth anything if the fake really wipes the error."""

    def test_a_default_write_clears_the_error(self):
        dev = ClearingDevice(1)
        dev.setErrorStateOnServer("broken")
        dev.updateStateOnServer("x", 1)
        self.assertEqual(dev.errorState, "")


class TestABlockedDeviceKeepsItsError(unittest.TestCase):

    def test_no_rule_survives_the_midnight_counter_reset(self):
        p, dev = _running(dahua_probe.NO_RULE)
        self.assertEqual(dev.errorState, "no rule drawn on the camera")
        p._counter_day = "2000-01-01"          # the date has changed
        p._roll_day_if_needed()
        self.assertEqual(dev.states["detectionsToday"], 0, "the reset did not run")
        self.assertEqual(dev.errorState, "no rule drawn on the camera",
                         "the midnight reset wiped the device's error")

    def test_disabled_survives_a_reconnecting_broadcast(self):
        p, dev = _running(dahua_probe.DISABLED)
        p._statuses.put((ADDRESS, "reconnecting", "no heartbeat"))
        p._drain_statuses()
        self.assertEqual(dev.states["streamState"], "reconnecting")
        self.assertEqual(dev.errorState, "switched off at the camera",
                         "a stream status wiped the device's error")

    def test_unsupported_survives_a_reconnecting_then_connected_stream(self):
        p, dev = _running(dahua_probe.UNSUPPORTED)
        for status in ("reconnecting", "connected"):
            p._statuses.put((ADDRESS, status, ""))
            p._drain_statuses()
        self.assertEqual(dev.errorState, "camera cannot emit this detection")


class TestAWorkerErrorKeepsUntilTheStreamRecovers(unittest.TestCase):

    def test_worker_unsupported_survives_the_midnight_reset(self):
        p, dev = _running(dahua_probe.CAPABLE, klass="person")
        p._statuses.put((ADDRESS, "unsupported", "firmware advertises none of X"))
        p._drain_statuses()
        p._counter_day = "2000-01-01"
        p._roll_day_if_needed()
        self.assertEqual(dev.errorState, "firmware advertises none of X")

    def test_connected_still_clears_it(self):
        """The plugin's own decision that the fault is over still works."""
        p, dev = _running(dahua_probe.CAPABLE, klass="person")
        p._statuses.put((ADDRESS, "unsupported", "firmware advertises none of X"))
        p._statuses.put((ADDRESS, "connected", ""))
        p._drain_statuses()
        self.assertEqual(dev.errorState, "")

    def test_a_capable_verdict_still_clears_it(self):
        p, dev = _running(dahua_probe.NO_RULE)
        p._verdict_for = lambda addr, k: (dahua_probe.CAPABLE, "")
        p._settle_device(dev.id, ADDRESS, "crossline")
        self.assertEqual(dev.errorState, "")


class TestEveryStateWriteKeepsTheError(unittest.TestCase):
    """So a new state write cannot quietly start wiping errors again."""

    def test_every_updateStateOnServer_passes_clearErrorState_false(self):
        with open(os.path.join(BUNDLE, "plugin.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr in ("updateStateOnServer", "updateStatesOnServer")]
        self.assertGreater(len(calls), 5, "found too few state writes to trust this")
        for call in calls:
            flag = next((k.value for k in call.keywords if k.arg == "clearErrorState"), None)
            self.assertTrue(
                isinstance(flag, ast.Constant) and flag.value is False,
                f"plugin.py line {call.lineno} writes a state without "
                f"clearErrorState=False")


if __name__ == "__main__":
    unittest.main(verbosity=2)
