#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_pulse_action.py
# Description: 1.24 - an event can arrive as action=Pulse, a single moment with no
#              Stop to follow. _apply used to treat every action that was not
#              "Start" as a Stop, so a Pulse never switched its device on, and an
#              unknown action could end a real detection. Pinned here:
#                - a Pulse switches on, holds, then switches off, and is counted
#                - a Pulse does not end a Start still running on that channel
#                - a hold of 0 still shows the device on for one tick
#                - an unknown action changes nothing and warns once per kind
#                - Start and Stop behave exactly as before
# Author:      CliveS & Claude Opus 5.5
# Date:        01-11-2026
# Version:     1.0

from dahua_stream import Event, HoldTimer, parse_event_line
from test_nvr_channels import NVR, _device, _plugin_with


def _send(p, action, now, index=0, code="CrossLineDetection"):
    p._apply((NVR, Event(code, action, index)), now)


def _tripwire(p_hold="20"):
    dev = _device(1, "Patio Tripwire", klass="crossline")
    p = _plugin_with(dev)
    p._timers[1].hold_seconds = int(p_hold)
    return p, dev


# ------------------------------------------------------------ parsing

def test_a_pulse_line_is_parsed():
    assert parse_event_line("Code=CrossLineDetection;action=Pulse;index=0;data={") == \
        Event("CrossLineDetection", "Pulse", 0)


# ------------------------------------------------------------ the plugin

def test_a_pulse_switches_the_device_on_then_off_after_the_hold():
    p, dev = _tripwire()
    _send(p, "Pulse", 1000.0)
    assert dev.states["onOffState"] is True
    assert p._timers[1].expires_at == 1020.0
    p._timers[1].tick(1019.9)
    assert p._timers[1].is_on
    assert p._timers[1].tick(1020.0) is True


def test_a_pulse_is_counted_and_stamped():
    p, dev = _tripwire()
    _send(p, "Pulse", 1000.0)
    assert dev.states.get("detectionsToday") == 1
    assert dev.states.get("lastDetection")


def test_repeated_pulses_keep_it_on_and_push_the_off_later():
    p, dev = _tripwire()
    _send(p, "Pulse", 1000.0)
    _send(p, "Pulse", 1010.0)
    assert p._timers[1].expires_at == 1030.0
    assert dev.states["onOffState"] is True


def test_an_unknown_action_changes_nothing_and_warns_once():
    p, dev = _tripwire()
    _send(p, "Start", 1000.0)

    def warnings():
        return [r.getMessage() for r in p.event_log.records if r.levelname == "WARNING"]

    before = len(warnings())
    _send(p, "Bogus", 1001.0)
    _send(p, "Bogus", 1002.0)
    timer = p._timers[1]
    assert timer.is_on and timer.expires_at is None, "an unknown action ended the detection"
    assert dev.states.get("detectionsToday") == 1, "an unknown action was counted"
    new_lines = warnings()[before:]
    assert len(new_lines) == 1, f"expected one warning, got {new_lines}"
    assert "Bogus" in new_lines[0] and "Patio Tripwire" in new_lines[0]


def test_start_and_stop_are_unchanged():
    p, dev = _tripwire()
    _send(p, "Start", 1000.0)
    assert dev.states["onOffState"] is True
    _send(p, "Stop", 1005.0)
    assert p._timers[1].expires_at == 1025.0


# ------------------------------------------------------------ HoldTimer.pulse

def test_a_pulse_leaves_a_running_start_on_the_same_channel_alone():
    t = HoldTimer(20)
    t.start(100.0, 0)
    t.pulse(101.0, 0)
    assert t.expires_at is None, "a Pulse ended a Start that had no Stop yet"
    t.stop(102.0, 0)
    assert t.expires_at == 122.0


def test_a_pulse_on_another_channel_does_not_end_a_running_start():
    t = HoldTimer(20)
    t.start(100.0, 0)
    t.pulse(101.0, 1)
    assert t.expires_at is None
    assert t.active_sources == frozenset({0})


def test_with_a_hold_of_zero_a_pulse_is_still_on_for_one_tick():
    t = HoldTimer(0)
    assert t.pulse(100.0) is True
    assert t.is_on, "a hold of 0 hid the pulse completely"
    assert t.tick(100.5) is True and not t.is_on


def test_a_pulse_counts_toward_the_cap_like_a_start():
    t = HoldTimer(20, max_on_seconds=600)
    t.pulse(100.0)
    assert t.next_deadline() == 120.0
