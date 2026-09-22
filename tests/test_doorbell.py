#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# Filename:    test_doorbell.py
# Description: Contract tests for the doorbell class (1.17). The case that matters
#              most is test_unlisted_code_on_a_real_doorbell_is_capable: the Amcrest
#              AD110 sends CallNoAnswered on a press WITHOUT listing it in
#              getExposureEvents (measured 22-09-2026), so trusting the list alone
#              would refuse the one device this class exists for.
# Author:      CliveS & Claude Opus 5
# Date:        22-09-2026
# Version:     1.0

import ast
import os
import queue
import sys
import threading
import unittest
import xml.etree.ElementTree as ET

HERE   = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.join(os.path.dirname(HERE), "DahuaEvents.indigoPlugin", "Contents",
                      "Server Plugin")
sys.path.insert(0, BUNDLE)

import dahua_probe as dp          # noqa: E402
import dahua_worker               # noqa: E402

# The real AD110's list, 22-09-2026 — note there is no CallNoAnswered in it.
AD110_EVENTS = "\n".join(f"events[{i}]={c}" for i, c in enumerate(
    ["LoginFailure", "VideoMotion", "VideoBlind", "AlarmLocal",
     "StorageNotExist", "StorageFailure", "StorageLowSpace"]))
CAMERA_EVENTS = "\n".join(f"events[{i}]={c}" for i, c in enumerate(
    ["VideoMotion", "SmartMotionHuman", "SmartMotionVehicle"]))


class TestAssessDoorbell(unittest.TestCase):

    def test_unlisted_code_on_a_real_doorbell_is_capable(self):
        verdict, reason = dp.assess_doorbell(AD110_EVENTS, "type=AD110\r\n")
        self.assertEqual(verdict, dp.CAPABLE)
        self.assertIn("AD110", reason)

    def test_advertising_the_code_is_proof_whatever_the_type(self):
        listed = AD110_EVENTS + "\nevents[7]=CallNoAnswered"
        self.assertEqual(dp.assess_doorbell(listed, "type=IP Camera")[0], dp.CAPABLE)

    def test_an_ordinary_camera_is_refused(self):
        """A Doorbell Pressed device on a camera would look healthy and never fire."""
        verdict, reason = dp.assess_doorbell(CAMERA_EVENTS, "type=IP Camera")
        self.assertEqual(verdict, dp.UNSUPPORTED)
        self.assertIn("not a doorbell", reason)

    def test_no_event_list_is_unreachable_not_unsupported(self):
        self.assertEqual(dp.assess_doorbell("", "type=AD110")[0], dp.UNREACHABLE)

    def test_missing_device_type_does_not_raise(self):
        self.assertEqual(dp.assess_doorbell(CAMERA_EVENTS, None)[0], dp.UNSUPPORTED)

    def test_parse_device_type(self):
        self.assertEqual(dp.parse_device_type("type=AD110\r\n"), "AD110")
        self.assertEqual(dp.parse_device_type("type=IP Camera"), "IP Camera")
        self.assertEqual(dp.parse_device_type(None), "")


class TestSummaryShowsTheDoorbell(unittest.TestCase):

    def test_doorbell_appears_in_the_dialog_summary(self):
        caps = {"person": (dp.UNSUPPORTED, ""), "doorbell": (dp.CAPABLE, "")}
        line = dp.summarise(caps)
        self.assertIn("Doorbell: yes", line)
        self.assertEqual(line, line.encode("ascii").decode("ascii"))


class TestWorkerDoesNotHaltADoorbell(unittest.TestCase):
    """The worker halts a camera that advertises none of the wanted codes. A
    doorbell advertises none of them — CallNoAnswered is unlisted — so without the
    exemption a doorbell-only camera would be halted for ever at startup."""

    def test_doorbell_only_camera_opens_a_stream(self):
        orig = dp.fetch
        dp.fetch = lambda *a, **k: AD110_EVENTS
        opened = threading.Event()
        statuses = []
        stop = threading.Event()
        w = dahua_worker.CameraWorker("10.0.0.9", "u", "p", queue.Queue(), stop,
                                      status_cb=lambda a, s, d: statuses.append(s),
                                      codes={dp.DOORBELL_CODE})

        def fake_open():
            opened.set()
            raise OSError("test stops here")
        w._open = fake_open
        try:
            w.start()
            self.assertTrue(opened.wait(3), "a doorbell must not be halted as unsupported")
            self.assertNotIn(dahua_worker.UNSUPPORTED, statuses)
        finally:
            dp.fetch = orig
            stop.set()
            w.stop()
            w.join(timeout=3)

    def test_an_ordinary_unsupported_camera_still_halts(self):
        """The exemption must not swallow the original guard."""
        orig = dp.fetch
        dp.fetch = lambda *a, **k: AD110_EVENTS
        statuses = []
        stop = threading.Event()
        w = dahua_worker.CameraWorker("10.0.0.9", "u", "p", queue.Queue(), stop,
                                      status_cb=lambda a, s, d: statuses.append(s),
                                      codes=set(dp.SMART_CODES))
        w._open = lambda: (_ for _ in ()).throw(AssertionError("must not open"))
        try:
            w.start()
            w.join(timeout=3)
            self.assertFalse(w.is_alive())
            self.assertEqual(statuses[0], dahua_worker.UNSUPPORTED)
        finally:
            dp.fetch = orig
            stop.set()


def _class_dict(name):
    tree = ast.parse(open(os.path.join(BUNDLE, "plugin.py"), encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in plugin.py")


class TestEveryClassIsOfferedInTheDialogs(unittest.TestCase):
    """A class in CLASS_CODES with no factory checkbox or menu Option can never be
    created — the code would be there and unreachable."""

    def setUp(self):
        self.codes  = _class_dict("CLASS_CODES")
        self.labels = _class_dict("CLASS_LABELS")
        self.xml    = ET.parse(os.path.join(BUNDLE, "Devices.xml")).getroot()

    def test_codes_and_labels_cover_the_same_classes(self):
        self.assertEqual(set(self.codes), set(self.labels))

    def test_doorbell_maps_to_the_measured_code(self):
        self.assertEqual(self.codes["doorbell"], dp.DOORBELL_CODE)
        self.assertEqual(dp.DOORBELL_CODE, "CallNoAnswered")

    def test_factory_has_a_checkbox_per_class(self):
        ids = {f.get("id") for f in self.xml.iter("Field")}
        for klass in self.codes:
            self.assertIn(f"want_{klass}", ids)

    def test_device_menu_has_an_option_per_class(self):
        field = next(f for f in self.xml.iter("Field") if f.get("id") == "detectionClass")
        values = {o.get("value") for o in field.iter("Option")}
        self.assertEqual(values, set(self.codes))


if __name__ == "__main__":
    unittest.main()
