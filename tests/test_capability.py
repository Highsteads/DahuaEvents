#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# Filename:    test_capability.py
# Description: Contract tests for the capability probe. No hardware, no network.
#              The case that matters most is test_config_flag_cannot_override_firmware:
#              a camera that SAYS smart detection is enabled while being incapable of it
#              is the exact live failure that prompted this plugin, and it must never
#              be reported as healthy.
# Author:      CliveS & Claude Opus 5
# Date:        01-09-2026
# Version:     1.0

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "DahuaEvents.indigoPlugin", "Contents", "Server Plugin"))

import dahua_probe as dp


CAPABLE_EVENTS = "\n".join(
    f"events[{i}]={c}" for i, c in enumerate(
        ["AudioAnomaly", "CrossLineDetection", "MoveDetection",
         "SmartMotionHuman", "SmartMotionVehicle", "VideoMotion"]))

# The real Garage camera's list, 01-09-2026: a rich IVS suite, no SMD.
OLD_EVENTS = "\n".join(
    f"events[{i}]={c}" for i, c in enumerate(
        ["AudioAnomaly", "CrossLineDetection", "CrossRegionDetection", "FaceDetection",
         "MoveDetection", "ParkingDetection", "StayDetection", "VideoMotion",
         "WanderDetection"]))

ON  = "table.{0}[0].Enable=true"
OFF = "table.{0}[0].Enable=false"
SMD_ON   = ON.format("SmartMotionDetect")
SMD_OFF  = OFF.format("SmartMotionDetect")
MD_ON    = ON.format("MotionDetect")
MD_OFF   = OFF.format("MotionDetect")


class TestParseEventList(unittest.TestCase):
    def test_extracts_codes(self):
        self.assertIn("SmartMotionHuman", dp.parse_event_list(CAPABLE_EVENTS))

    def test_empty_and_junk_give_empty_set_not_an_exception(self):
        for bad in ("", None, "not a config at all", "<html>404</html>"):
            self.assertEqual(dp.parse_event_list(bad), set())


class TestParseEnableFlag(unittest.TestCase):
    def test_true_and_false(self):
        self.assertIs(dp.parse_enable_flag(SMD_ON, "SmartMotionDetect"), True)
        self.assertIs(dp.parse_enable_flag(SMD_OFF, "SmartMotionDetect"), False)

    def test_absent_is_none_not_false(self):
        """The whole absent-state family in one assertion: 'did not say' must never
        collapse into 'said no'."""
        self.assertIsNone(dp.parse_enable_flag("", "SmartMotionDetect"))
        self.assertIsNone(dp.parse_enable_flag(None, "SmartMotionDetect"))
        self.assertIsNone(dp.parse_enable_flag(MD_ON, "SmartMotionDetect"))

    def test_does_not_confuse_the_two_params(self):
        both = MD_ON + "\n" + SMD_OFF
        self.assertIs(dp.parse_enable_flag(both, "MotionDetect"), True)
        self.assertIs(dp.parse_enable_flag(both, "SmartMotionDetect"), False)


class TestAssess(unittest.TestCase):
    def test_capable_when_advertised_and_both_enabled(self):
        verdict, _ = dp.assess(CAPABLE_EVENTS, SMD_ON, MD_ON)
        self.assertEqual(verdict, dp.CAPABLE)

    def test_config_flag_cannot_override_firmware(self):
        """THE case this module exists for. The Garage camera accepted
        SmartMotionDetect[0].Enable=true, returned OK and read it back as true, while
        advertising no smart events whatsoever. Trusting the flag reports a dead
        camera as healthy."""
        verdict, reason = dp.assess(OLD_EVENTS, SMD_ON, MD_ON)
        self.assertEqual(verdict, dp.UNSUPPORTED)
        self.assertIn("SmartMotionHuman", reason)

    def test_motion_detect_off_is_disabled_not_capable(self):
        """SMD filters motion rather than replacing it, so MotionDetect off makes it a
        no-op however SMD reads."""
        verdict, reason = dp.assess(CAPABLE_EVENTS, SMD_ON, MD_OFF)
        self.assertEqual(verdict, dp.DISABLED)
        self.assertIn("MotionDetect", reason)

    def test_both_off_are_both_named(self):
        verdict, reason = dp.assess(CAPABLE_EVENTS, SMD_OFF, MD_OFF)
        self.assertEqual(verdict, dp.DISABLED)
        self.assertIn("MotionDetect", reason)
        self.assertIn("SmartMotionDetect", reason)

    def test_no_event_list_is_unreachable_not_unsupported(self):
        """Learning nothing is not the same as learning the camera cannot do it —
        one is a network problem, the other is a hardware fact."""
        for empty in ("", None):
            verdict, _ = dp.assess(empty, SMD_ON, MD_ON)
            self.assertEqual(verdict, dp.UNREACHABLE)

    def test_unreadable_settings_are_unreachable_not_capable(self):
        verdict, _ = dp.assess(CAPABLE_EVENTS, None, MD_ON)
        self.assertEqual(verdict, dp.UNREACHABLE)
        verdict, _ = dp.assess(CAPABLE_EVENTS, SMD_ON, None)
        self.assertEqual(verdict, dp.UNREACHABLE)

    def test_partial_smart_support_is_unsupported(self):
        half = CAPABLE_EVENTS.replace("events[4]=SmartMotionVehicle", "events[4]=Whatever")
        verdict, reason = dp.assess(half, SMD_ON, MD_ON)
        self.assertEqual(verdict, dp.UNSUPPORTED)
        self.assertIn("SmartMotionVehicle", reason)


class TestDescribe(unittest.TestCase):
    def test_every_verdict_has_a_distinct_prefix_and_names_the_camera(self):
        seen = set()
        for verdict in (dp.CAPABLE, dp.DISABLED, dp.UNSUPPORTED, dp.UNREACHABLE):
            line = dp.describe(verdict, "some reason", "192.168.1.64")
            self.assertIn("192.168.1.64", line)
            self.assertIn("some reason", line)
            seen.add(line.split("]")[0])
        self.assertEqual(len(seen), 4, "verdicts must be distinguishable in the log")


class TestProbeGuards(unittest.TestCase):
    def test_missing_address_or_credentials_never_touches_the_network(self):
        def explode(*a, **k):
            raise AssertionError("probe() must not reach the network without inputs")
        original, dp.fetch = dp.fetch, explode
        try:
            self.assertEqual(dp.probe("", "u", "p")[0], dp.UNREACHABLE)
            self.assertEqual(dp.probe("1.2.3.4", "", "p")[0], dp.UNREACHABLE)
            self.assertEqual(dp.probe("1.2.3.4", "u", "")[0], dp.UNREACHABLE)
        finally:
            dp.fetch = original


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestSweepIsolation(unittest.TestCase):
    """Architecture question 2, asserted rather than asserted-in-prose.

    A sweep of seven cameras must lose only the camera that fails. The plugin
    itself needs an indigo import, so this exercises the contract at the level
    the plugin relies on: probe() must never raise, whatever fetch does.
    """

    def test_probe_never_raises_however_the_network_misbehaves(self):
        def hostile(*a, **k):
            raise RuntimeError("kernel said no")
        original, dp.fetch = dp.fetch, hostile
        try:
            verdict, reason = dp.probe("192.168.1.64", "u", "p")
        except Exception as exc:                       # noqa: BLE001 - that IS the assertion
            self.fail(f"probe() raised {exc!r}; one bad camera would kill the whole sweep")
        finally:
            dp.fetch = original
        self.assertEqual(verdict, dp.UNREACHABLE)
        self.assertTrue(reason)
