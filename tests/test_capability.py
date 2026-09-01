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


# A real VideoAnalyseRule body, shortened, from Left Garage Door on 01-09-2026.
RULES_FACE_ONLY = """
table.VideoAnalyseRule[0][0].Class=FaceDetection
table.VideoAnalyseRule[0][0].Enable=false
table.VideoAnalyseRule[0][0].Name=FaceDetection
table.VideoAnalyseRule[0][0].ObjectTypes[0]=Human
"""

RULES_WITH_TRIPWIRE = RULES_FACE_ONLY + """
table.VideoAnalyseRule[0][1].Class=CrossLineDetection
table.VideoAnalyseRule[0][1].Enable=true
table.VideoAnalyseRule[0][1].Name=DriveLine
table.VideoAnalyseRule[0][1].ObjectTypes[0]=Human
"""

RULES_TRIPWIRE_OFF = RULES_FACE_ONLY + """
table.VideoAnalyseRule[0][1].Class=CrossLineDetection
table.VideoAnalyseRule[0][1].Enable=false
table.VideoAnalyseRule[0][1].Name=DriveLine
"""

IVS_EVENTS = "\n".join(f"events[{i}]={c}" for i, c in enumerate(
    ["CrossLineDetection", "CrossRegionDetection", "VideoMotion"]))

# Deliberately WITHOUT any IVS code. Note CAPABLE_EVENTS at the top of this file
# does contain CrossLineDetection, so it is the wrong fixture for this question —
# using it asserted the opposite of what it looked like it was asserting.
NO_IVS_EVENTS = "\n".join(f"events[{i}]={c}" for i, c in enumerate(
    ["AudioAnomaly", "MoveDetection", "SmartMotionHuman", "SmartMotionVehicle", "VideoMotion"]))

# Everything: SMD and BOTH IVS codes. Use this when a test needs each class to
# reach a DIFFERENT verdict. CAPABLE_EVENTS is not that fixture — it carries
# CrossLineDetection but not CrossRegionDetection, which has now caught me twice
# writing an assertion that looked right and tested the opposite.
ALL_EVENTS = "\n".join(f"events[{i}]={c}" for i, c in enumerate(
    ["AudioAnomaly", "CrossLineDetection", "CrossRegionDetection", "MoveDetection",
     "SmartMotionHuman", "SmartMotionVehicle", "VideoMotion"]))


class TestParseRules(unittest.TestCase):

    def test_reads_class_enabled_and_name(self):
        self.assertEqual(dp.parse_rules(RULES_WITH_TRIPWIRE),
                         [("FaceDetection", False, "FaceDetection"),
                          ("CrossLineDetection", True, "DriveLine")])

    def test_empty_or_junk_gives_no_rules(self):
        for bad in ("", None, "not a config"):
            self.assertEqual(dp.parse_rules(bad), [])


class TestAssessIvs(unittest.TestCase):

    def test_advertised_but_no_rule_is_not_capable(self):
        """THE IVS trap, and it is the SMD trap one generation down. The firmware
        advertises the event, so any check based on the event list alone says yes —
        and the camera then never fires, looking healthy for ever."""
        verdict, reason = dp.assess_ivs(IVS_EVENTS, RULES_FACE_ONLY, "crossline")
        self.assertEqual(verdict, dp.NO_RULE)
        self.assertIn("no such rule", reason)

    def test_a_rule_that_exists_but_is_switched_off_is_not_capable(self):
        verdict, reason = dp.assess_ivs(IVS_EVENTS, RULES_TRIPWIRE_OFF, "crossline")
        self.assertEqual(verdict, dp.NO_RULE)
        self.assertIn("switched off", reason)
        self.assertIn("DriveLine", reason, "name the rule so it can be found")

    def test_an_enabled_rule_is_capable(self):
        verdict, _ = dp.assess_ivs(IVS_EVENTS, RULES_WITH_TRIPWIRE, "crossline")
        self.assertEqual(verdict, dp.CAPABLE)

    def test_a_rule_of_the_wrong_class_does_not_count(self):
        verdict, _ = dp.assess_ivs(IVS_EVENTS, RULES_WITH_TRIPWIRE, "crossregion")
        self.assertEqual(verdict, dp.NO_RULE)

    def test_firmware_without_the_event_is_unsupported_not_no_rule(self):
        """Different problems, different fixes: one is 'draw a rule', the other is
        'this camera never will'."""
        verdict, _ = dp.assess_ivs(NO_IVS_EVENTS, RULES_WITH_TRIPWIRE, "crossline")
        self.assertEqual(verdict, dp.UNSUPPORTED)

    def test_unreadable_rules_are_unreachable_not_no_rule(self):
        verdict, _ = dp.assess_ivs(IVS_EVENTS, None, "crossline")
        self.assertEqual(verdict, dp.UNREACHABLE)

    def test_an_unknown_class_is_refused(self):
        verdict, _ = dp.assess_ivs(IVS_EVENTS, RULES_WITH_TRIPWIRE, "telepathy")
        self.assertEqual(verdict, dp.UNSUPPORTED)

    def test_probe_ivs_never_raises(self):
        def hostile(*a, **k):
            raise RuntimeError("kernel said no")
        original, dp.fetch = dp.fetch, hostile
        try:
            verdict, _ = dp.probe_ivs("192.168.1.64", "u", "p", "crossline")
        except Exception as exc:                   # noqa: BLE001 - that IS the assertion
            self.fail(f"probe_ivs raised {exc!r}")
        finally:
            dp.fetch = original
        self.assertEqual(verdict, dp.UNREACHABLE)


class TestCapabilities(unittest.TestCase):
    """One round of fetches answering every class, for the setup dialog.

    Indigo gives a UI callback about thirty seconds. Probing four classes
    separately would fetch the same three documents four times; this must not.
    """

    def _stub(self, events=None, smd=None, motion=None, rules=None):
        bodies = {
            "getExposureEvents": events,
            "SmartMotionDetect": smd,
            "name=MotionDetect": motion,
            "VideoAnalyseRule": rules,
        }
        self.fetches = []

        def fake(address, path, user, password, timeout=None):
            self.fetches.append(path)
            for key, body in bodies.items():
                if key in path:
                    return body
            return None
        return fake

    def test_it_fetches_each_document_exactly_once(self):
        original, dp.fetch = dp.fetch, self._stub(
            ALL_EVENTS, SMD_ON, MD_ON, RULES_WITH_TRIPWIRE)
        try:
            dp.capabilities("192.168.1.64", "u", "p")
        finally:
            dp.fetch = original
        self.assertEqual(len(self.fetches), 4,
                         f"expected 4 requests, made {len(self.fetches)}: {self.fetches}")
        self.assertEqual(len(set(self.fetches)), 4, "no document should be fetched twice")

    def test_it_answers_every_class(self):
        # ALL_EVENTS on purpose: this asserts three DIFFERENT verdicts, which needs
        # a camera advertising both IVS codes while only one has a rule.
        original, dp.fetch = dp.fetch, self._stub(
            ALL_EVENTS, SMD_ON, MD_ON, RULES_WITH_TRIPWIRE)
        try:
            caps = dp.capabilities("192.168.1.64", "u", "p")
        finally:
            dp.fetch = original
        self.assertEqual(set(caps), {"person", "vehicle", "crossline", "crossregion"})
        self.assertEqual(caps["person"][0], dp.CAPABLE)
        self.assertEqual(caps["crossline"][0], dp.CAPABLE)
        self.assertEqual(caps["crossregion"][0], dp.NO_RULE)

    def test_an_unreachable_camera_answers_every_class_rather_than_some(self):
        """A partial answer would leave classes silently unmentioned, and an
        unmentioned class reads as a working one."""
        original, dp.fetch = dp.fetch, (lambda *a, **k: None)
        try:
            caps = dp.capabilities("192.168.1.64", "u", "p")
        finally:
            dp.fetch = original
        self.assertEqual(set(caps), {"person", "vehicle", "crossline", "crossregion"})
        self.assertTrue(all(v == dp.UNREACHABLE for v, _ in caps.values()))

    def test_it_never_raises(self):
        def hostile(*a, **k):
            raise RuntimeError("kernel said no")
        original, dp.fetch = dp.fetch, hostile
        try:
            caps = dp.capabilities("192.168.1.64", "u", "p")
        except Exception as exc:                  # noqa: BLE001 - that IS the assertion
            self.fail(f"capabilities() raised {exc!r} — a dialog must not throw")
        finally:
            dp.fetch = original
        self.assertTrue(all(v == dp.UNREACHABLE for v, _ in caps.values()))

    def test_summarise_names_all_four_in_a_stable_order(self):
        original, dp.fetch = dp.fetch, self._stub(
            ALL_EVENTS, SMD_ON, MD_ON, RULES_WITH_TRIPWIRE)
        try:
            line = dp.summarise(dp.capabilities("192.168.1.64", "u", "p"))
        finally:
            dp.fetch = original
        self.assertEqual(line.index("People") < line.index("Vehicles")
                         < line.index("Tripwire") < line.index("Intrusion"), True)
        self.assertIn("no rule drawn", line)


class TestAsciiOnly(unittest.TestCase):
    """Indigo serialises names, props and states through XML, and a non-ASCII
    character in a value written at RUNTIME can be refused with
    `LowLevelBadParameterError -- illegal character in XML tag name or value`,
    naming neither the field nor the character. Device creation failed exactly
    that way on 01-09-2026 because a summary line joined with a middle dot.
    """

    def test_it_strips_the_characters_that_caused_it(self):
        self.assertEqual(dp.ascii_only("a · b — c"), "a ? b ? c")

    def test_plain_text_is_untouched(self):
        self.assertEqual(dp.ascii_only("People: yes | Vehicles: no"),
                         "People: yes | Vehicles: no")

    def test_none_and_non_strings_are_safe(self):
        self.assertEqual(dp.ascii_only(None), "")
        self.assertEqual(dp.ascii_only(42), "42")

    def test_control_characters_go_too(self):
        self.assertEqual(dp.ascii_only("a\x00b\tc\nd"), "a?b?c?d")

    def test_summarise_output_is_always_ascii(self):
        """The actual regression. summarise() feeds a dialog field, so whatever it
        returns must survive Indigo's XML layer."""
        original, dp.fetch = dp.fetch, (lambda *a, **k: None)
        try:
            line = dp.summarise(dp.capabilities("192.168.1.64", "u", "p"))
        finally:
            dp.fetch = original
        self.assertTrue(line)
        offenders = [c for c in line if ord(c) > 126]
        self.assertEqual(offenders, [], f"non-ASCII in a dialog value: {offenders}")

    def test_every_summarise_verdict_stays_ascii(self):
        for verdict in (dp.CAPABLE, dp.DISABLED, dp.NO_RULE, dp.UNSUPPORTED, dp.UNREACHABLE):
            caps = {k: (verdict, "why") for k in
                    ("person", "vehicle", "crossline", "crossregion")}
            line = dp.summarise(caps)
            self.assertEqual([c for c in line if ord(c) > 126], [], line)
