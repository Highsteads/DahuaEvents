#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# Filename:    test_stream.py
# Description: Contract tests for the parser and the hold state machine.
#              No socket, no clock, no waiting — time is a parameter, so the hold
#              behaviour is tested in microseconds and the awkward cases (a Start
#              landing on the exact expiry instant, a hold of zero) are reachable
#              at all, which they would not be against a live camera.
# Author:      CliveS & Claude Opus 5
# Date:        01-09-2026
# Version:     1.0

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "DahuaEvents.indigoPlugin", "Contents", "Server Plugin"))

from dahua_stream import (MAX_DRAIN_PER_TICK, Event, HoldTimer, StreamParser,
                          drain, parse_event_line)

# A real frame, captured from the Drive camera on 01-09-2026. Using the genuine
# shape rather than an invented one matters: a fixture built from an assumption
# tests the assumption, and passes just as convincingly when it is wrong.
REAL_FRAME = (
    "--myboundary\r\n"
    "Content-Type: text/plain\r\n"
    "Content-Length: 141\r\n"
    "\r\n"
    "Code=VideoMotion;action=Start;index=0;data={\r\n"
    '   "Id" : [ 0 ],\r\n'
    '   "RegionName" : [ "Region1" ]\r\n'
    "}\r\n"
    "\r\n"
)


# Captured verbatim off the Drive camera on 01-09-2026 — this is what the stream
# looks like when NOTHING is happening, which is almost all of the time. Recorded
# because "no events" has to be provably the correct answer here rather than a
# parser that silently understands nothing.
REAL_IDLE_STREAM = (
    "--myboundary\r\nContent-Type: text/plain\r\nContent-Length: 9\r\n\r\n"
    "Heartbeat\r\n\r\n"
) * 6 + "--myboundary\r\nContent-Type: text/plain\r\nContent-Length: 9\r\n\r\nHeartbe"


class TestRealIdleStream(unittest.TestCase):
    """Six heartbeats and a truncated seventh, exactly as the camera sent them."""

    def test_heartbeats_produce_no_events(self):
        self.assertEqual(StreamParser().feed(REAL_IDLE_STREAM), [])

    def test_the_truncated_tail_is_held_back_not_mangled(self):
        p = StreamParser()
        p.feed(REAL_IDLE_STREAM)
        self.assertEqual(p.pending(), len("Heartbe"),
                         "the incomplete final line must be buffered for the next read")
        self.assertEqual(p.dropped_bytes, 0)

    def test_an_event_arriving_after_idle_traffic_is_still_seen(self):
        p = StreamParser()
        p.feed(REAL_IDLE_STREAM)
        self.assertEqual(
            p.feed("at\r\n\r\n--myboundary\r\nContent-Type: text/plain\r\n\r\n"
                   "Code=SmartMotionHuman;action=Start;index=0;data={\r\n"),
            [Event("SmartMotionHuman", "Start", 0)],
            "the held-back 'Heartbe' must complete as 'Heartbeat' and not corrupt what follows")


class TestParseEventLine(unittest.TestCase):

    def test_decodes_a_real_event_line(self):
        e = parse_event_line("Code=SmartMotionHuman;action=Start;index=0;data={")
        self.assertEqual(e, Event("SmartMotionHuman", "Start", 0))

    def test_decodes_without_the_data_tail(self):
        self.assertEqual(parse_event_line("Code=SmartMotionVehicle;action=Stop;index=2"),
                         Event("SmartMotionVehicle", "Stop", 2))

    def test_tolerates_spacing_variation(self):
        self.assertEqual(parse_event_line("Code=VideoMotion; action=Start; index=1"),
                         Event("VideoMotion", "Start", 1))

    def test_noise_returns_none_rather_than_raising(self):
        for junk in ("", None, "--myboundary", "Content-Type: text/plain",
                     '   "RegionName" : [ "Region1" ]', "}", "Heartbeat", "\r\n"):
            self.assertIsNone(parse_event_line(junk), f"should ignore: {junk!r}")

    def test_a_json_body_mentioning_code_is_not_an_event(self):
        """Anchored at line start precisely so a payload cannot forge an event."""
        self.assertIsNone(parse_event_line('   "note" : "Code=SmartMotionHuman;action=Start;index=0"'))


class TestStreamParser(unittest.TestCase):

    def test_extracts_the_event_from_a_real_frame(self):
        self.assertEqual(StreamParser().feed(REAL_FRAME),
                         [Event("VideoMotion", "Start", 0)])

    def test_a_line_split_across_reads_is_not_lost(self):
        """The failure this class exists for. Silent, rare, and unreproducible —
        it would be blamed on the camera."""
        p = StreamParser()
        self.assertEqual(p.feed("Code=SmartMotionHuman;acti"), [])
        self.assertEqual(p.feed("on=Start;index=0\n"), [Event("SmartMotionHuman", "Start", 0)])

    def test_split_one_byte_at_a_time_still_yields_exactly_one_event(self):
        p = StreamParser()
        got = []
        for ch in "Code=SmartMotionVehicle;action=Stop;index=1\n":
            got.extend(p.feed(ch))
        self.assertEqual(got, [Event("SmartMotionVehicle", "Stop", 1)])

    def test_several_events_in_one_chunk_arrive_in_order(self):
        chunk = ("Code=SmartMotionHuman;action=Start;index=0\n"
                 "Code=SmartMotionHuman;action=Stop;index=0\n"
                 "Code=SmartMotionVehicle;action=Start;index=0\n")
        self.assertEqual(StreamParser().feed(chunk), [
            Event("SmartMotionHuman", "Start", 0),
            Event("SmartMotionHuman", "Stop", 0),
            Event("SmartMotionVehicle", "Start", 0)])

    def test_an_incomplete_trailing_line_is_held_not_emitted(self):
        p = StreamParser()
        self.assertEqual(p.feed("Code=SmartMotionHuman;action=Start;index=0\nCode=Sma"), 
                         [Event("SmartMotionHuman", "Start", 0)])
        self.assertGreater(p.pending(), 0)

    def test_empty_and_none_chunks_are_harmless(self):
        p = StreamParser()
        self.assertEqual(p.feed(""), [])
        self.assertEqual(p.feed(None), [])

    def test_a_newlineless_flood_is_capped_and_reported(self):
        """An unbounded buffer would be a slow memory leak that only a broken
        camera triggers — the kind nobody reproduces."""
        p = StreamParser()
        p.feed("x" * 200_000)
        self.assertLessEqual(p.pending(), 65536)
        self.assertGreater(p.dropped_bytes, 0)

    def test_it_resynchronises_after_dropping_junk(self):
        p = StreamParser()
        p.feed("x" * 200_000)
        self.assertEqual(p.feed("\nCode=SmartMotionHuman;action=Start;index=0\n"),
                         [Event("SmartMotionHuman", "Start", 0)])


class TestHoldTimer(unittest.TestCase):

    def test_start_switches_on_and_reports_the_change(self):
        t = HoldTimer(20)
        self.assertTrue(t.start(1000))
        self.assertTrue(t.is_on)

    def test_a_repeat_start_reports_no_change(self):
        """Idempotency: the caller writes to Indigo only on a real transition, or a
        busy camera churns the state table and the SQL logger for nothing."""
        t = HoldTimer(20)
        t.start(1000)
        self.assertFalse(t.start(1001))
        self.assertTrue(t.is_on)

    def test_stop_does_not_switch_off_immediately(self):
        t = HoldTimer(20)
        t.start(1000)
        self.assertFalse(t.stop(1005))
        self.assertTrue(t.is_on, "stop must schedule the off, not perform it")

    def test_it_switches_off_only_once_the_hold_elapses(self):
        t = HoldTimer(20)
        t.start(1000)
        t.stop(1005)
        self.assertFalse(t.tick(1024))
        self.assertTrue(t.is_on)
        self.assertTrue(t.tick(1025))
        self.assertFalse(t.is_on)

    def test_a_start_during_the_hold_cancels_the_pending_off(self):
        """One person walking through frame is a burst of Start/Stop pairs. This is
        what turns that into a single continuous detection."""
        t = HoldTimer(20)
        t.start(1000)
        t.stop(1005)
        t.start(1010)
        self.assertIsNone(t.expires_at)
        self.assertFalse(t.tick(1030))
        self.assertTrue(t.is_on, "a re-trigger must cancel the pending off")

    def test_each_stop_restarts_the_full_hold(self):
        t = HoldTimer(20)
        t.start(1000)
        t.stop(1005)
        t.start(1010)
        t.stop(1012)
        self.assertFalse(t.tick(1031))
        self.assertTrue(t.tick(1032))

    def test_tick_is_idempotent_once_off(self):
        t = HoldTimer(20)
        t.start(1000); t.stop(1000)
        self.assertTrue(t.tick(1020))
        self.assertFalse(t.tick(1021))
        self.assertFalse(t.tick(2000))

    def test_expiry_is_inclusive_at_the_exact_instant(self):
        """Reachable only with an injected clock, and exactly the sort of boundary
        that goes wrong once and is never reproduced."""
        t = HoldTimer(20)
        t.start(1000); t.stop(1000)
        self.assertTrue(t.tick(1020))

    def test_a_stop_with_nothing_running_changes_nothing(self):
        t = HoldTimer(20)
        self.assertFalse(t.stop(1000))
        self.assertFalse(t.is_on)
        self.assertIsNone(t.expires_at)

    def test_zero_hold_switches_off_on_stop(self):
        t = HoldTimer(0)
        t.start(1000)
        self.assertTrue(t.stop(1000), "with no hold, 'later' is now")
        self.assertFalse(t.is_on)

    def test_a_negative_hold_is_clamped_not_obeyed(self):
        """A negative deadline is always in the past, which would switch the device
        off on the next tick regardless of what the camera can see."""
        self.assertEqual(HoldTimer(-5).hold_seconds, 0)

    def test_a_string_hold_is_accepted(self):
        """A saved Indigo dialog re-serialises even numeric fields as strings."""
        self.assertEqual(HoldTimer("30").hold_seconds, 30)

    def test_next_deadline_lets_the_caller_sleep_instead_of_spinning(self):
        t = HoldTimer(20)
        self.assertIsNone(t.next_deadline())
        t.start(1000)
        self.assertIsNone(t.next_deadline())
        t.stop(1005)
        self.assertEqual(t.next_deadline(), 1025)


class TestClassesAreIndependent(unittest.TestCase):

    def test_person_and_vehicle_do_not_interfere(self):
        person, vehicle = HoldTimer(20), HoldTimer(20)
        person.start(1000)
        vehicle.start(1002)
        person.stop(1003)
        self.assertTrue(person.tick(1023))
        self.assertFalse(person.is_on)
        self.assertTrue(vehicle.is_on, "a car leaving must not clear the person")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestBoundedDrain(unittest.TestCase):
    """Termination, question 5, for the drain.

    A drain that runs until its queue is empty has no upper bound. Whatever the
    caller does afterwards never happens — here that is hold expiry, so a camera
    flooding events would leave every device stuck on, including the cameras
    behaving perfectly. The failure punishes the innocent ones, which is the worst
    shape it could take.
    """

    def setUp(self):
        import queue
        self.q = queue.Queue()

    def test_it_takes_everything_when_it_fits(self):
        for i in range(5):
            self.q.put(i)
        items, overflowed = drain(self.q, limit=200)
        self.assertEqual(items, list(range(5)))
        self.assertFalse(overflowed)

    def test_an_empty_queue_is_not_an_error(self):
        self.assertEqual(drain(self.q), ([], False))

    def test_it_stops_at_the_limit_and_says_so(self):
        for i in range(500):
            self.q.put(i)
        items, overflowed = drain(self.q, limit=200)
        self.assertEqual(len(items), 200)
        self.assertTrue(overflowed, "falling behind must be reported, not silent")
        self.assertEqual(self.q.qsize(), 300, "the rest must be left for the next tick")

    def test_a_flood_still_returns(self):
        """The whole point: it must RETURN, so the caller gets to expire holds."""
        for i in range(100_000):
            self.q.put(i)
        items, overflowed = drain(self.q)
        self.assertEqual(len(items), MAX_DRAIN_PER_TICK)
        self.assertTrue(overflowed)

    def test_a_zero_or_negative_limit_takes_nothing_rather_than_looping(self):
        self.q.put("x")
        self.assertEqual(drain(self.q, limit=0)[0], [])
        self.assertEqual(drain(self.q, limit=-5)[0], [])
