#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_config_xml.py
# Description: Structural checks on the plugin's dialog XML. Exists because
#              SigenEnergyManager v5.75.0 added a second section that reused three
#              field IDs from the first, and Indigo refuses to open a dialog
#              with a duplicate ID —
#                runConfigDialog() caught exception: PAXDialogControllerError
#                -- Field ID separator_dashboard was already used.
#              Nothing in the plugin exercises the XML, so the whole Configure
#              dialog was dead for five days before anyone opened it. These
#              tests are cheap and would have caught it the same afternoon.
# Author:      CliveS & Claude Opus 5
# Date:        29-08-2026
# Version:     1.0

import collections
import glob
import os
import unittest
import xml.etree.ElementTree as ET

# This copy lives in tests/, so it looks INTO the bundle rather than beside itself.
# The empty-glob guard below is what makes that safe to change: if this path is ever
# wrong the suite says so, rather than passing vacuously over nothing.
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
XML_FILES = sorted(glob.glob(os.path.join(
    REPO, "DahuaEvents.indigoPlugin", "Contents", "Server Plugin", "*.xml")))


def _dialog_scopes(root):
    """Every element whose direct <Field> children share one dialog namespace.

    PluginConfig.xml is itself one dialog; Devices/Actions/Events wrap each
    dialog in its own <ConfigUI>, so IDs only need to be unique within one.
    """
    scopes = [("<root>", root)]
    for parent in root.iter():
        for cfg in parent.findall("ConfigUI"):
            scopes.append((parent.get("id") or parent.tag, cfg))
    return scopes


class TestDialogXml(unittest.TestCase):

    def test_there_are_xml_files_to_check(self):
        # A glob that silently matches nothing would make every test below pass.
        self.assertTrue(XML_FILES, "no XML files found — the check is not checking")

    def test_every_xml_file_parses(self):
        for path in XML_FILES:
            with self.subTest(xml=os.path.basename(path)):
                ET.parse(path)

    def test_field_ids_unique_within_each_dialog(self):
        for path in XML_FILES:
            root = ET.parse(path).getroot()
            for label, scope in _dialog_scopes(root):
                ids = [f.get("id") for f in scope.findall("Field")]
                dupes = sorted(i for i, c in collections.Counter(ids).items() if c > 1)
                with self.subTest(xml=os.path.basename(path), dialog=label):
                    self.assertEqual(
                        dupes, [],
                        f"duplicate Field id(s) {dupes} — Indigo raises "
                        f"PAXDialogControllerError and the dialog will not open")

    def test_every_field_has_an_id(self):
        for path in XML_FILES:
            root = ET.parse(path).getroot()
            for label, scope in _dialog_scopes(root):
                for f in scope.findall("Field"):
                    with self.subTest(xml=os.path.basename(path), dialog=label):
                        self.assertTrue(f.get("id"), "a <Field> has no id attribute")

    def test_visible_bindings_point_at_a_field_in_the_same_dialog(self):
        """A visibleBindingId naming a field that isn't there hides the row for good."""
        for path in XML_FILES:
            root = ET.parse(path).getroot()
            for label, scope in _dialog_scopes(root):
                ids = {f.get("id") for f in scope.findall("Field")}
                for f in scope.findall("Field"):
                    binding = f.get("visibleBindingId")
                    if binding:
                        with self.subTest(xml=os.path.basename(path),
                                          dialog=label, field=f.get("id")):
                            self.assertIn(
                                binding, ids,
                                f"visibleBindingId {binding!r} names no field in this dialog")


class TestEnumStateOptionValues(unittest.TestCase):
    """A Devices.xml State with a ValueType List gets an auto-generated boolean
    sub-state per Option, named `<state_id>.<option_value>` (global CLAUDE.md
    "enum auto-generates bool sub-states"). A value containing a space cannot
    form a legal sub-state id, so Indigo refuses the WHOLE write with
    `cannot update device state for key <id> to value <value>` even though the
    Option is declared correctly — exactly what happened to `streamState`
    "no rule" (found 02-09-2026, TRIAGE_QUEUE, fixed to "noRule").
    """

    def _enum_states(self):
        for path in XML_FILES:
            root = ET.parse(path).getroot()
            for state in root.iter("State"):
                options = state.findall("./ValueType/List/Option")
                if options:
                    yield os.path.basename(path), state.get("id"), options

    def test_there_are_enum_states_to_check(self):
        # This repo has exactly one today (streamState) — if that ever becomes
        # zero the glob or the tag name changed under this test and it would
        # otherwise pass vacuously.
        self.assertTrue(list(self._enum_states()), "no List-enum States found")

    def test_option_values_contain_no_whitespace(self):
        for xml_name, state_id, options in self._enum_states():
            for opt in options:
                value = opt.get("value")
                with self.subTest(xml=xml_name, state=state_id, value=value):
                    self.assertNotRegex(
                        value or "", r"\s",
                        f"{state_id}.{value} would be the auto-generated sub-state "
                        f"id — a space in it makes Indigo refuse the whole write")


if __name__ == "__main__":
    unittest.main(verbosity=2)
