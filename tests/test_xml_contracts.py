#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_xml_contracts.py
# Description: The places where plugin.py and the XML files must agree, and where
#              a mismatch only shows up when a user clicks something:
#                - every <CallbackMethod> named in any XML exists on Plugin
#                - every device state plugin.py writes is declared in Devices.xml
#                  (onOffState is native to a sensor, so it is exempt)
#                - every value written to streamState is one of its <Option>s
#                - the detection classes in Devices.xml, CLASS_CODES and
#                  CLASS_LABELS, and the factory's want_<class> boxes all match
#              Reads the source with ast; imports plugin.py only through the
#              shared stub in conftest.py.
# Author:      CliveS & Claude Opus 5.5
# Date:        01-11-2026
# Version:     1.0

import ast
import glob
import os
import xml.etree.ElementTree as ET

import dahua_worker
import plugin as plugin_module
from conftest import SERVER_PLUGIN

PLUGIN_PY   = os.path.join(SERVER_PLUGIN, "plugin.py")
DEVICES_XML = os.path.join(SERVER_PLUGIN, "Devices.xml")
NATIVE_STATES = {"onOffState"}      # supplied by Indigo for type="sensor"


def _xml_files():
    return sorted(glob.glob(os.path.join(SERVER_PLUGIN, "*.xml")))


def _plugin_tree():
    with open(PLUGIN_PY, encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _state_writes():
    """(state key, value node) for every x.updateStateOnServer("key", value, ...)."""
    out = []
    for node in ast.walk(_plugin_tree()):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "updateStateOnServer" and node.args
                and isinstance(node.args[0], ast.Constant)):
            out.append((node.args[0].value, node.args[1] if len(node.args) > 1 else None))
    return out


def _declared_states():
    root = ET.parse(DEVICES_XML).getroot()
    return {s.get("id") for s in root.iter("State")}


def _stream_state_options():
    root = ET.parse(DEVICES_XML).getroot()
    for state in root.iter("State"):
        if state.get("id") == "streamState":
            return {o.get("value") for o in state.iter("Option")}
    return set()


# ------------------------------------------------------------ callbacks

def test_every_xml_callback_exists_on_the_plugin():
    named = []
    for path in _xml_files():
        for el in ET.parse(path).getroot().iter("CallbackMethod"):
            named.append((os.path.basename(path), (el.text or "").strip()))
    assert named, "expected at least one CallbackMethod (Probe a Camera, Check Camera...)"
    missing = [f"{f}: {m}" for f, m in named if not callable(getattr(plugin_module.Plugin, m, None))]
    assert not missing, f"XML names callbacks plugin.py does not define: {missing}"


def test_the_callback_check_can_fail():
    assert getattr(plugin_module.Plugin, "noSuchCallbackXYZ", None) is None


# ------------------------------------------------------------ states

def test_every_state_written_is_declared_in_devices_xml():
    written = {key for key, _ in _state_writes()}
    assert {"streamState", "lastDetection", "detectionsToday"} <= written, \
        "the AST scan found fewer state writes than expected — the check itself is broken"
    undeclared = written - _declared_states() - NATIVE_STATES
    assert not undeclared, f"plugin.py writes states Devices.xml does not declare: {undeclared}"


def test_every_literal_stream_state_is_a_declared_option():
    options = _stream_state_options()
    assert options, "streamState has no <Option> list"
    literals = {v.value for k, v in _state_writes()
                if k == "streamState" and isinstance(v, ast.Constant)}
    assert literals, "expected literal streamState writes"
    assert literals <= options, f"undeclared streamState values: {literals - options}"


def test_blocked_verdicts_write_declared_stream_states():
    options = _stream_state_options()
    values = {state for state, _ in plugin_module.BLOCKED_VERDICTS.values()}
    assert values <= options, f"undeclared: {values - options}"


def test_worker_statuses_that_reach_stream_state_are_declared():
    """_drain_statuses writes the worker's status straight into streamState for
    everything except STOPPED (1.19.1)."""
    options = _stream_state_options()
    for status in (dahua_worker.CONNECTED, dahua_worker.RECONNECTING, dahua_worker.UNSUPPORTED,
                   dahua_worker.LOGIN_REFUSED):
        assert status in options, f"worker status {status!r} is not a streamState option"


# ------------------------------------------------------------ detection classes

def test_detection_class_menu_matches_the_code_and_label_tables():
    root = ET.parse(DEVICES_XML).getroot()
    menu = next(f for f in root.iter("Field") if f.get("id") == "detectionClass")
    options = {o.get("value") for o in menu.iter("Option")}
    assert options == set(plugin_module.CLASS_CODES), "Devices.xml menu vs CLASS_CODES"
    assert options == set(plugin_module.CLASS_LABELS), "Devices.xml menu vs CLASS_LABELS"


def test_device_factory_has_a_checkbox_for_every_class():
    root = ET.parse(DEVICES_XML).getroot()
    boxes = {f.get("id")[len("want_"):] for f in root.iter("Field")
             if (f.get("id") or "").startswith("want_")}
    assert boxes == set(plugin_module.CLASS_LABELS)


def test_default_classes_are_known_classes():
    assert set(plugin_module.DEFAULT_CLASSES) <= set(plugin_module.CLASS_CODES)
