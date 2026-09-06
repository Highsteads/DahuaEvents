#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_camera_status_blocking.py
# Description: A device's SETTLED verdict (no rule drawn / switched off at the
#              camera / firmware unsupported) is a fact about the camera's
#              CONFIGURATION and must survive the shared per-camera worker
#              reconnecting, which only says the event STREAM is reachable —
#              a different axis entirely. Found live 06-09-2026 onboarding
#              Patio: "Patio Tripwire" correctly settled to streamState
#              "noRule" / errorState "no rule drawn on the camera", then was
#              overwritten to "connected" with a CLEARED errorState within a
#              second, because _drain_statuses broadcasts the worker's
#              CONNECTED status to every device sharing that camera address —
#              a device that will NEVER fire until a rule is drawn read as
#              perfectly healthy the moment the stream opened.
#
#              Reuses test_event_log_quiet's stub install rather than a second
#              copy of it: two competing sys.modules["indigo"] stubs racing on
#              import order is exactly the kind of thing that looks fine in
#              isolation and breaks the other file silently.
# Author:      CliveS & Claude Sonnet 5
# Date:        06-09-2026
# Version:     1.0

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


class FakeDevice:
    """Just enough of an Indigo device for _settle_device / _drain_statuses.
    test_event_log_quiet's FakeDevice has no id/errorState — this one is a
    superset, not a competing definition."""

    def __init__(self, dev_id, name="Patio Tripwire"):
        self.id = dev_id
        self.name = name
        self.states = {}
        self.errorState = ""

    def updateStateOnServer(self, key, value):
        self.states[key] = value

    def setErrorStateOnServer(self, value):
        self.errorState = value


def _settle(p, dev, address, klass, verdict, reason="because"):
    """Drive the real _settle_device, with _verdict_for stubbed so no network
    call is made — the point here is what _settle_device and _drain_statuses do
    with a verdict, not dahua_probe's own HTTP layer (covered elsewhere)."""
    plugin_module.indigo.devices = {dev.id: dev}
    p._by_camera.setdefault(address, {})[klass] = dev.id
    p._verdict_for = lambda addr, k: (verdict, reason)
    p._settle_device(dev.id, address, klass)


class TestBlockedVerdictSurvivesConnected(unittest.TestCase):
    """The exact live fault: a settled BLOCKING verdict must not be overwritten
    by the shared camera worker reporting the stream is CONNECTED."""

    def _assert_survives_connected(self, verdict, expected_state, expected_error):
        p = make_plugin()
        dev = FakeDevice(1)
        _settle(p, dev, "192.168.100.67", "crossline", verdict)
        self.assertEqual(dev.states["streamState"], expected_state)
        self.assertEqual(dev.errorState, expected_error)

        p._statuses.put(("192.168.100.67", "connected", ""))
        p._drain_statuses()

        self.assertEqual(
            dev.states["streamState"], expected_state,
            "a worker-level CONNECTED broadcast overwrote a settled blocking verdict")
        self.assertEqual(
            dev.errorState, expected_error,
            "the errorState was cleared even though the camera still blocks this class")

    def test_no_rule_survives_a_connected_broadcast(self):
        self._assert_survives_connected(
            dahua_probe.NO_RULE, "noRule", "no rule drawn on the camera")

    def test_disabled_survives_a_connected_broadcast(self):
        self._assert_survives_connected(
            dahua_probe.DISABLED, "disabled", "switched off at the camera")

    def test_unsupported_survives_a_connected_broadcast(self):
        self._assert_survives_connected(
            dahua_probe.UNSUPPORTED, "unsupported", "camera cannot emit this detection")


class TestUnblockedBehaviourIsUnchanged(unittest.TestCase):
    """The fix must not touch the common case: a CAPABLE device still tracks
    the worker's live stream status exactly as before."""

    def test_a_capable_device_still_shows_connected(self):
        p = make_plugin()
        dev = FakeDevice(1)
        _settle(p, dev, "192.168.100.68", "crossline", dahua_probe.CAPABLE)
        self.assertEqual(dev.states["streamState"], "connected")
        self.assertEqual(dev.errorState, "")

        p._statuses.put(("192.168.100.68", "connected", ""))
        p._drain_statuses()
        self.assertEqual(dev.states["streamState"], "connected")
        self.assertEqual(dev.errorState, "")

    def test_a_capable_device_still_shows_reconnecting(self):
        p = make_plugin()
        dev = FakeDevice(1)
        _settle(p, dev, "192.168.100.68", "crossline", dahua_probe.CAPABLE)

        p._statuses.put(("192.168.100.68", "reconnecting", "no heartbeat"))
        p._drain_statuses()
        self.assertEqual(dev.states["streamState"], "reconnecting",
                         "a genuine connection fault must still reach a capable device")


class TestOnlyConnectedIsSuppressed(unittest.TestCase):
    """The fix targets exactly one transition — a blocked device must still
    hear that its camera has gone properly unreachable, because that IS new
    information; only the misleadingly reassuring "connected" is withheld."""

    def test_a_blocked_device_still_receives_reconnecting(self):
        p = make_plugin()
        dev = FakeDevice(1)
        _settle(p, dev, "192.168.100.67", "crossline", dahua_probe.NO_RULE)

        p._statuses.put(("192.168.100.67", "reconnecting", "no heartbeat"))
        p._drain_statuses()
        self.assertEqual(
            dev.states["streamState"], "reconnecting",
            "a blocked device should still learn its camera went unreachable")

    def test_a_blocked_device_still_receives_worker_unsupported(self):
        p = make_plugin()
        dev = FakeDevice(1)
        _settle(p, dev, "192.168.100.67", "crossline", dahua_probe.NO_RULE)

        p._statuses.put(("192.168.100.67", "unsupported", "camera stopped advertising events"))
        p._drain_statuses()
        self.assertEqual(dev.states["streamState"], "unsupported")
        self.assertEqual(dev.errorState, "camera stopped advertising events")


class TestReSettleUnblocks(unittest.TestCase):
    """A fresh settle (Send Status Request, or a device restart after the user
    draws a rule) is the ONLY thing that should clear a block — never a stream
    reconnect — and once it does, normal worker-driven behaviour resumes."""

    def test_settling_capable_after_no_rule_unblocks_the_device(self):
        p = make_plugin()
        dev = FakeDevice(1)
        _settle(p, dev, "192.168.100.67", "crossline", dahua_probe.NO_RULE)
        self.assertTrue(p._blocked[dev.id])

        _settle(p, dev, "192.168.100.67", "crossline", dahua_probe.CAPABLE)
        self.assertFalse(p._blocked[dev.id])
        self.assertEqual(dev.states["streamState"], "connected")

        p._statuses.put(("192.168.100.67", "connected", ""))
        p._drain_statuses()
        self.assertEqual(dev.states["streamState"], "connected")


class TestBlockedFlagIsCleanedUp(unittest.TestCase):

    def test_devicestopcomm_pops_the_blocked_flag(self):
        p = make_plugin()
        dev = FakeDevice(1)
        _settle(p, dev, "192.168.100.67", "crossline", dahua_probe.NO_RULE)
        self.assertIn(dev.id, p._blocked)

        dev.pluginProps = {"address": "192.168.100.67", "detectionClass": "crossline"}
        p.deviceStopComm(dev)
        self.assertNotIn(dev.id, p._blocked,
                         "a stopped device left a stale blocked flag behind")


if __name__ == "__main__":
    unittest.main(verbosity=2)
