#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_event_log_quiet.py
# Description: The detection narration must stay OUT of Indigo's shared event log
#              unless the user asks for it, and every WARNING and ERROR must stay
#              IN it whichever way that switch is set.
# Author:      CliveS & Claude Opus 5
# Date:        06-09-2026
# Version:     1.1
#
# WHY THIS FILE EXISTS
# The event log is the estate's dashboard and every plugin writes to it. Two
# cameras alone put 52, 54 and 52 lines a day into it on 03, 04 and 05 Sep 2026,
# and on each of those days they were the ONLY DahuaEvents lines there: a
# DETECTED/clear pair per person walking past, saying what the device's own
# onOffState already said, and growing with every camera added.
#
# The cure is one word, info -> debug, which is exactly the kind of change that
# gets reverted by accident. So these tests do not check which method was called.
# They stand a real logging.Logger up with the SAME two handlers Indigo gives a
# plugin (plugin_base.py:274 sets the event-log handler to INFO, :300 sets the
# plugin's own file handler to THREADDEBUG) and ask what actually landed where.
#
# The half that matters most is the second half. Log_Error_Watch.py reads the
# EVENT log and nothing else, so a fault that only reaches the plugin's own file
# is a fault nobody is watching.

import ast
import glob
import io
import logging
import os
import re
import sys
import types
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BUNDLE = os.path.join(REPO, "DahuaEvents.indigoPlugin", "Contents", "Server Plugin")
PLUGIN_PY = os.path.join(BUNDLE, "plugin.py")
CONFIG_XML = os.path.join(BUNDLE, "PluginConfig.xml")

sys.path.insert(0, BUNDLE)


# ============================================================
# Stubs: just enough Indigo to import plugin.py and drive it
# ============================================================

# Every CliveS plugin installs a filter that prefixes each line with the time it
# was logged, and DahuaEvents is no exception. These tests are about WHERE a line
# goes, not what time it was, so the prefix comes off before comparing. It is
# stripped rather than switched off so the tests run against the real init path,
# and TestTheHarnessCanFail checks the prefix is genuinely there - otherwise this
# strip would quietly mask its disappearance.
_STAMP = re.compile(r"^\[\d{2}:\d{2}:\d{2}\.\d{3}\] ")


class Recorder(logging.Handler):
    """One of Indigo's two handlers, remembering what got through its level."""

    def __init__(self, level):
        super().__init__(level)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    @property
    def raw(self):
        return [r.getMessage() for r in self.records]

    @property
    def messages(self):
        return [_STAMP.sub("", m) for m in self.raw]

    def at_least(self, level):
        return [_STAMP.sub("", r.getMessage())
                for r in self.records if r.levelno >= level]


_logger_serial = [0]


class StubPluginBase:
    """Stands in for indigo.PluginBase.

    The logger is REAL, and carries the same two handlers a plugin gets from
    Indigo: one at INFO standing for the shared event log, one at DEBUG standing
    for Logs/<bundle id>/plugin.log. That is the whole mechanism this change rests
    on, so faking it would be testing the wrong thing.
    """

    class StopThread(Exception):
        pass

    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        self.pluginId = pluginId
        self.pluginDisplayName = pluginDisplayName
        self.pluginVersion = pluginVersion
        self.pluginPrefs = pluginPrefs
        self.debug = False

        _logger_serial[0] += 1
        self.logger = logging.getLogger(f"DahuaEventsTest-{_logger_serial[0]}")
        # plugin_base.py uses THREADDEBUG (5) here; DEBUG is below INFO, which is
        # the only relationship any of this turns on.
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False       # keep it out of pytest's own capture
        self.logger.handlers = []
        self.event_log = Recorder(logging.INFO)      # plugin_base.py:274
        self.plugin_file = Recorder(logging.DEBUG)   # plugin_base.py:300
        self.logger.addHandler(self.event_log)
        self.logger.addHandler(self.plugin_file)

    def sleep(self, _seconds):
        pass

    def savePluginPrefs(self):
        pass


def _install_stubs():
    """Seed sys.modules before plugin.py is imported.

    IndigoSecrets is stubbed rather than left to the real file so the test reads
    the same on CI as on the Indigo Mac, and so a suite run never opens the
    estate's credentials file for no reason.
    """
    if "indigo" not in sys.modules:
        indigo = types.ModuleType("indigo")
        indigo.PluginBase = StubPluginBase
        indigo.Dict = dict
        indigo.devices = {}
        indigo.kSensorAction = types.SimpleNamespace(
            RequestStatus="RequestStatus", TurnOn="TurnOn",
            TurnOff="TurnOff", Toggle="Toggle")
        indigo.kProtocol = types.SimpleNamespace(Plugin="Plugin")
        indigo.server = types.SimpleNamespace(log=lambda *a, **k: None)
        sys.modules["indigo"] = indigo
    if "IndigoSecrets" not in sys.modules:
        secrets = types.ModuleType("IndigoSecrets")
        secrets.DAHUA_USER = ""
        secrets.DAHUA_PASS = ""
        sys.modules["IndigoSecrets"] = secrets


# Imported here, not at the top: plugin.py subclasses indigo.PluginBase at import
# time, so the stubs above have to be in sys.modules first.
_install_stubs()
import plugin as plugin_module


class FakeDevice:
    def __init__(self, name="Drive Person"):
        self.name = name
        self.states = {}

    def updateStateOnServer(self, key, value):
        self.states[key] = value


def make_plugin(**prefs):
    return plugin_module.Plugin("com.clives.indigoplugin.dahuaevents",
                                "DahuaEvents", "1.10", dict(prefs))


# ============================================================
# The harness itself
# ============================================================

class TestTheHarnessCanFail(unittest.TestCase):
    """Every 'nothing reached the event log' test below is an assertion about an
    ABSENCE, and an absence passes just as happily when the recorder is broken.
    Prove it can see a line before trusting it not to."""

    def test_an_info_line_does_reach_the_event_log_recorder(self):
        p = make_plugin()
        p.logger.info("a line")
        self.assertEqual(p.event_log.messages, ["a line"])

    def test_a_debug_line_does_not_reach_the_event_log_recorder(self):
        p = make_plugin()
        p.logger.debug("a line")
        self.assertEqual(p.event_log.messages, [])
        self.assertEqual(p.plugin_file.messages, ["a line"])

    def test_the_house_timestamp_prefix_is_really_there(self):
        """Guards the strip above. If the filter ever stops being installed, the
        strip would go on passing and nobody would notice it had gone."""
        p = make_plugin()
        p.logger.info("a line")
        self.assertRegex(p.event_log.raw[0], r"^\[\d{2}:\d{2}:\d{2}\.\d{3}\] a line$")


# ============================================================
# The narration
# ============================================================

class TestDetectionNarration(unittest.TestCase):

    def test_detections_do_not_reach_the_event_log_by_default(self):
        p = make_plugin()
        dev = FakeDevice()
        p._write_on_off(dev, True)
        p._write_on_off(dev, False)
        self.assertEqual(p.event_log.messages, [],
                         "the detection narration is back in the shared event log")

    def test_detections_still_reach_the_plugins_own_log_by_default(self):
        p = make_plugin()
        dev = FakeDevice()
        p._write_on_off(dev, True)
        p._write_on_off(dev, False)
        self.assertEqual(p.plugin_file.messages,
                         ["Drive Person: DETECTED", "Drive Person: clear"],
                         "quiet must mean 'not in the shared log', never 'not recorded'")

    def test_detections_reach_the_event_log_when_the_user_opts_in(self):
        p = make_plugin(logActivityToEventLog=True)
        dev = FakeDevice()
        p._write_on_off(dev, True)
        p._write_on_off(dev, False)
        self.assertEqual(p.event_log.messages,
                         ["Drive Person: DETECTED", "Drive Person: clear"])

    def test_the_wording_is_the_same_whichever_way_the_switch_is_set(self):
        """Otherwise the plugin's own log reads differently depending on a setting
        that is supposed to be about WHERE the line goes, not what it says."""
        quiet = make_plugin()
        loud = make_plugin(logActivityToEventLog=True)
        for p in (quiet, loud):
            dev = FakeDevice()
            p._write_on_off(dev, True)
            p._write_on_off(dev, False)
        self.assertEqual(quiet.plugin_file.messages, loud.plugin_file.messages)

    def test_the_device_state_is_written_whichever_way_the_switch_is_set(self):
        """The switch governs logging and nothing else. Folding the state write
        into either branch would make the setting silently break the plugin."""
        for prefs in ({}, {"logActivityToEventLog": True}):
            with self.subTest(prefs=prefs):
                p = make_plugin(**prefs)
                dev = FakeDevice()
                p._write_on_off(dev, True)
                self.assertEqual(dev.states["onOffState"], True)
                p._write_on_off(dev, False)
                self.assertEqual(dev.states["onOffState"], False)


class TestThePreference(unittest.TestCase):

    QUIET = [{}, {"logActivityToEventLog": False}, {"logActivityToEventLog": ""},
             {"logActivityToEventLog": "false"}, {"logActivityToEventLog": "0"},
             {"logActivityToEventLog": "no"}, {"logActivityToEventLog": "off"},
             {"logActivityToEventLog": 0}, {"logActivityToEventLog": None},
             {"logActivityToEventLog": "banana"}]

    LOUD = [{"logActivityToEventLog": True}, {"logActivityToEventLog": "true"},
            {"logActivityToEventLog": "True"}, {"logActivityToEventLog": "1"},
            {"logActivityToEventLog": "yes"}, {"logActivityToEventLog": "on"}]

    def test_an_absent_blank_or_unreadable_setting_leaves_the_plugin_quiet(self):
        """bool("false") is True, so a pref holding text is exactly how a quiet
        default turns loud without anyone touching the checkbox."""
        for prefs in self.QUIET:
            with self.subTest(prefs=prefs):
                self.assertFalse(make_plugin(**prefs).log_activity)

    def test_a_ticked_box_is_honoured_in_every_form_indigo_can_hand_it_back(self):
        for prefs in self.LOUD:
            with self.subTest(prefs=prefs):
                self.assertTrue(make_plugin(**prefs).log_activity)

    def test_saving_the_dialog_applies_the_new_setting(self):
        """Without this mirror a re-save leaves the plugin on the value it booted
        with, so the checkbox appears to do nothing until the next restart."""
        p = make_plugin()
        p.closedPrefsConfigUi({"logActivityToEventLog": True}, False)
        self.assertTrue(p.log_activity)
        p.closedPrefsConfigUi({"logActivityToEventLog": False}, False)
        self.assertFalse(p.log_activity)

    def test_cancelling_the_dialog_changes_nothing(self):
        p = make_plugin(logActivityToEventLog=True)
        p.closedPrefsConfigUi({"logActivityToEventLog": False}, True)
        self.assertTrue(p.log_activity)


# ============================================================
# Faults. The half that matters most.
# ============================================================

class TestFaultsStillReachTheEventLog(unittest.TestCase):
    """Log_Error_Watch.py reads the EVENT log and nothing else."""

    def test_a_bad_hold_value_warns_in_the_event_log_when_quiet(self):
        p = make_plugin(holdSeconds="banana")
        self.assertTrue(p.event_log.at_least(logging.WARNING),
                        "a config fault did not reach the shared event log")

    def test_a_negative_hold_value_warns_in_the_event_log_when_quiet(self):
        p = make_plugin(holdSeconds="-5")
        self.assertTrue(p.event_log.at_least(logging.WARNING))

    def test_a_bad_per_device_override_warns_in_the_event_log_when_quiet(self):
        p = make_plugin()
        dev = FakeDevice()
        dev.pluginProps = {"holdOverride": "soon"}
        p._hold_for(dev)
        self.assertTrue(p.event_log.at_least(logging.WARNING))

    def test_the_quiet_switch_does_not_touch_warnings_or_errors(self):
        """The switch is about narration. Anything red must be unaffected by it."""
        for prefs in ({}, {"logActivityToEventLog": True}):
            with self.subTest(prefs=prefs):
                p = make_plugin(**prefs)
                p.logger.warning("something is wrong")
                p.logger.error("something is very wrong")
                self.assertEqual(p.event_log.at_least(logging.WARNING),
                                 ["something is wrong", "something is very wrong"])


# ============================================================
# Structural guards on plugin.py itself
# ============================================================

def _plugin_tree():
    return ast.parse(io.open(PLUGIN_PY, encoding="utf-8").read())


def _logger_calls(node):
    """Every self.logger.<level>(...) call at or below this node.

    It matches the CALL, so it is blind to a level reached through a name:
    `level = self.logger.warning if ... else self.logger.info` followed by
    `level(line)` has no self.logger.<attr> in callee position and is counted as
    nothing at all. plugin.py:501 was written exactly that way until 06-09-2026,
    so the WARNING it raised when a camera stopped being connected was invisible
    to every guard below and could have been demoted with the suite still green.

    Teaching this walker to trace an alias cannot be done in general - the name
    could be reassigned, passed, or built in another function. So the alias is
    forbidden instead, by TestStructure.test_no_logger_method_is_reached_through
    _an_alias, which is what keeps this simple matcher honest.
    """
    out = []
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Attribute)
                and n.func.value.attr == "logger"):
            out.append(n.func.attr)
    return out


def _aliased_logger_methods(tree):
    """(line, attr) for every self.logger.<attr> that is NOT being called there.

    Anything this returns is a logging level bound to a name, stored, or handed
    to something else, and therefore a level the guards above cannot see. Note it
    matches self.logger.<attr>, not self.logger itself, so passing the logger
    whole - install_timestamp_filter(self.logger) - is not caught, and should not
    be: that is the logger, not a level.
    """
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return [(n.lineno, n.attr) for n in ast.walk(tree)
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Attribute)
            and n.value.attr == "logger"
            and id(n) not in called]


def _function(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def _mentions_the_flag(node):
    return any(isinstance(n, ast.Attribute) and n.attr == "log_activity"
               for n in ast.walk(node))


FAULT_LEVELS = ("warning", "error", "exception", "critical")

# Measured on plugin.py 06-09-2026, after the info -> debug change AND after the
# ternary at plugin.py:501 was written out as two direct calls: 13 warning, 3
# error, 7 exception, 0 critical. It was 22 while that ternary was still an alias,
# because the warning inside it was counted by nothing - which is the whole reason
# the alias guard below exists. The check is >= rather than == so a future fault
# line can be added without editing this file, while a fault line that is deleted
# or quietly demoted still turns the suite red.
FAULT_CALL_SITES_ON_06_09_2026 = 23


class TestStructure(unittest.TestCase):

    def test_the_narration_line_is_only_ever_info_behind_the_switch(self):
        """The mutation this whole file exists to catch: putting the event log
        back on the unconditional path."""
        fn = _function(_plugin_tree(), "_write_on_off")
        self.assertIsNotNone(fn, "_write_on_off has been renamed - update this test")
        gated = [n for n in ast.walk(fn)
                 if isinstance(n, ast.If) and _mentions_the_flag(n.test)]
        self.assertTrue(gated, "_write_on_off no longer consults self.log_activity")

        in_whole_method = _logger_calls(fn).count("info")
        behind_the_flag = sum(_logger_calls(branch).count("info") for branch in gated)
        self.assertGreaterEqual(behind_the_flag, 1,
                                "nothing is logged at info even when the user opts in")
        self.assertEqual(in_whole_method, behind_the_flag,
                         "_write_on_off has an info call outside the "
                         "self.log_activity branch, so it reaches the shared "
                         "event log whatever the user set")

    def test_no_fault_line_anywhere_is_gated_by_the_switch(self):
        """A warning or error hidden behind a quiet setting is a fault nobody
        would ever be told about."""
        tree = _plugin_tree()
        for n in ast.walk(tree):
            if isinstance(n, ast.If) and _mentions_the_flag(n.test):
                levels = set(_logger_calls(n))
                with self.subTest(line=n.lineno):
                    self.assertEqual(
                        levels & set(FAULT_LEVELS), set(),
                        f"a {sorted(levels & set(FAULT_LEVELS))} call sits behind "
                        f"self.log_activity at line {n.lineno}")

    def test_no_fault_line_was_lost_when_the_narration_was_quietened(self):
        counts = {}
        for level in _logger_calls(_plugin_tree()):
            counts[level] = counts.get(level, 0) + 1
        total = sum(counts.get(level, 0) for level in FAULT_LEVELS)
        self.assertGreaterEqual(
            total, FAULT_CALL_SITES_ON_06_09_2026,
            f"plugin.py had {FAULT_CALL_SITES_ON_06_09_2026} warning/error/exception "
            f"call sites and now has {total} - one has been removed or demoted")

    def test_no_logger_method_is_reached_through_an_alias(self):
        """Every logging level must be called where it is written.

        The guards above match a literal self.logger.<level>(...) call, so a level
        bound to a name first is invisible to all of them. plugin.py:501 read
        `level = self.logger.warning if status != "connected" else
        self.logger.info` with `level(...)` on the next line: a genuine fault site
        that the "no fault line was lost" count never included, and that could
        therefore have been demoted to info without a single test noticing.

        Forbidding the alias is the check that can actually be made. Following one
        cannot be, in general, so the choice is between this and a guard that
        quietly stops covering whatever gets written next.
        """
        aliased = _aliased_logger_methods(_plugin_tree())
        self.assertEqual(
            aliased, [],
            "plugin.py binds a logger level to a name at "
            + ", ".join(f"line {line} (self.logger.{attr})" for line, attr in aliased)
            + " - call self.logger.<level>(...) directly, or the structural guards "
              "in this file cannot see that fault site at all")

    def test_the_alias_detector_can_actually_see_an_alias(self):
        """The test above asserts an ABSENCE, and an absence passes just as well
        when the detector is broken. This is the exact shape plugin.py:501 had."""
        tree = ast.parse(
            "class C:\n"
            "    def m(self, bad):\n"
            "        level = self.logger.warning if bad else self.logger.info\n"
            "        level('something')\n"
            "        self.logger.error('direct')\n"
            "        install_timestamp_filter(self.logger)\n")
        found = _aliased_logger_methods(tree)
        self.assertEqual(sorted(attr for _, attr in found), ["info", "warning"],
                         "the detector missed the ternary, or flagged the direct "
                         "call or the whole-logger argument, which are both fine")

    def test_there_is_something_to_scan(self):
        # A tree with no logger calls would make every assertion above pass.
        self.assertTrue(_logger_calls(_plugin_tree()))


class TestTheSwitchIsReachable(unittest.TestCase):
    """Quiet by default is only acceptable because the narration can be asked for."""

    FIELD = "logActivityToEventLog"

    def _field(self):
        root = ET.parse(CONFIG_XML).getroot()
        for f in root.findall("Field"):
            if f.get("id") == self.FIELD:
                return f
        return None

    def test_the_config_dialog_offers_the_switch(self):
        self.assertIsNotNone(
            self._field(),
            f"{self.FIELD} is not in PluginConfig.xml, so a user who wants the "
            f"narration back has no way to ask for it")

    def test_it_is_a_checkbox_that_starts_off(self):
        f = self._field()
        self.assertEqual(f.get("type"), "checkbox")
        self.assertIn((f.get("defaultValue") or "").strip().lower(),
                      ("false", "0", "no", ""),
                      "the shared event log must be quiet until asked otherwise")

    def test_the_plugin_reads_the_field_the_dialog_writes(self):
        """A dialog field nothing reads is a switch that does nothing."""
        source = io.open(PLUGIN_PY, encoding="utf-8").read()
        self.assertIn(self.FIELD, source)

    def test_the_bundle_has_a_config_dialog_at_all(self):
        self.assertTrue(glob.glob(CONFIG_XML))


if __name__ == "__main__":
    unittest.main(verbosity=2)
