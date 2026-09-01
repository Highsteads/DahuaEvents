#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# Filename:    test_worker.py
# Description: Contract tests for the camera worker. It never imports indigo, so the
#              whole of its lifecycle — including the guarantees that matter most,
#              that it TERMINATES and that it does not retry the hopeless — can be
#              tested with no camera and no Indigo.
# Author:      CliveS & Claude Opus 5
# Date:        01-09-2026
# Version:     1.0

import os
import queue
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "DahuaEvents.indigoPlugin", "Contents", "Server Plugin"))

import dahua_probe
import dahua_worker
from dahua_worker import CameraWorker


class FakeResponse:
    """Hands out chunks then behaves however the test asks."""

    def __init__(self, chunks, then=None):
        self._chunks = list(chunks)
        self._then = then
        self.closed = False

    def read(self, _n):
        if self._chunks:
            return self._chunks.pop(0)
        if self._then == "raise":
            raise OSError("stream died")
        return b""                      # empty = camera closed the stream

    def close(self):
        self.closed = True


class WorkerHarness:
    """Builds a worker with the network and the capability check stubbed out."""

    def __init__(self, verdict=dahua_probe.CAPABLE, responses=None):
        self.events = queue.Queue()
        self.statuses = []
        self.stop = threading.Event()
        self.opens = 0
        self._responses = list(responses or [])
        self._orig_probe = dahua_probe.probe
        dahua_probe.probe = lambda *a, **k: (verdict, "stubbed")
        self.worker = CameraWorker("10.0.0.1", "u", "p", self.events, self.stop,
                                   status_cb=lambda a, s, d: self.statuses.append((s, d)))
        self.worker._open = self._open

    def _open(self):
        self.opens += 1
        if self._responses:
            return self._responses.pop(0)
        return FakeResponse([], then="raise")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        dahua_probe.probe = self._orig_probe
        self.stop.set()
        self.worker.stop()
        self.worker.join(timeout=3)


class TestUnsupportedCameraHalts(unittest.TestCase):

    def test_it_does_not_retry_a_camera_that_never_can_work(self):
        """Termination, question 5. A camera whose firmware cannot emit these
        events will never start emitting them, so a retry loop is pure noise —
        and a loop with no exit is the specific failure the standing rules name."""
        with WorkerHarness(verdict=dahua_probe.UNSUPPORTED) as h:
            h.worker.start()
            h.worker.join(timeout=3)
            self.assertFalse(h.worker.is_alive(), "an unsupported camera must halt")
            self.assertEqual(h.opens, 0, "it must not even open a connection")
            self.assertEqual(h.statuses[0][0], dahua_worker.UNSUPPORTED)


class TestEventsReachTheQueue(unittest.TestCase):

    def test_a_detection_is_delivered(self):
        frame = b"Code=SmartMotionHuman;action=Start;index=0;data={\r\n"
        with WorkerHarness(responses=[FakeResponse([frame], then="raise")]) as h:
            h.worker.start()
            address, event = h.events.get(timeout=3)
            self.assertEqual(address, "10.0.0.1")
            self.assertEqual((event.code, event.action), ("SmartMotionHuman", "Start"))

    def test_it_reports_connected_before_any_event(self):
        with WorkerHarness(responses=[FakeResponse([b""], then=None)]) as h:
            h.worker.start()
            deadline = time.time() + 3
            while time.time() < deadline and not h.statuses:
                time.sleep(0.01)
            self.assertEqual(h.statuses[0][0], dahua_worker.CONNECTED)


class TestReconnection(unittest.TestCase):

    def test_a_dropped_stream_is_retried(self):
        good = FakeResponse([b"Code=SmartMotionVehicle;action=Start;index=0\r\n"], then="raise")
        with WorkerHarness(responses=[FakeResponse([], then="raise"), good]) as h:
            h.worker.start()
            address, event = h.events.get(timeout=5)
            self.assertEqual(event.code, "SmartMotionVehicle")
            self.assertGreaterEqual(h.opens, 2, "it must have reconnected")

    def test_backoff_is_capped_in_the_code_not_just_the_constant(self):
        """Asserts what the WORKER actually waits, by recording every value it
        passes to the stop Event.

        The first version of this test recomputed the doubling itself and asserted
        that — which passes just as happily when the module is uncapped, because it
        was testing its own arithmetic and never touched the code. Uncapped doubling
        reaches an hour, which reads as "the camera never came back" when it simply
        was not asked.
        """
        with WorkerHarness() as h:
            waits = []
            real_wait = h.stop.wait

            def recording_wait(timeout=None):
                waits.append(timeout)
                if len(waits) >= 12:          # enough for 2**12 to blow any cap
                    h.stop.set()
                return real_wait(0)           # never actually sleep

            h.stop.wait = recording_wait
            h.worker.start()
            h.worker.join(timeout=5)

        self.assertGreaterEqual(len(waits), 8, "expected repeated reconnect attempts")
        self.assertTrue(all(w <= dahua_worker.BACKOFF_MAX for w in waits),
                        f"backoff exceeded the cap: {waits}")
        self.assertEqual(max(waits), dahua_worker.BACKOFF_MAX,
                         f"backoff should reach and hold the cap: {waits}")


class TestStopIsHonoured(unittest.TestCase):

    def test_it_waits_on_the_event_rather_than_sleeping(self):
        """Asserts the MECHANISM, not a stopwatch.

        A wall-clock threshold passes a sleeping implementation whenever the first
        backoff happens to be short, and is flaky on a loaded machine besides. If
        the worker sleeps instead of waiting on the Event, stop.wait is simply never
        called — which is exactly what this observes. Sleeping would make a plugin
        restart take up to a minute per camera.
        """
        with WorkerHarness(responses=[FakeResponse([], then="raise")]) as h:
            called = []
            real_wait = h.stop.wait

            def recording_wait(timeout=None):
                called.append(timeout)
                h.stop.set()
                return real_wait(0)

            h.stop.wait = recording_wait
            h.worker.start()
            h.worker.join(timeout=5)
            self.assertFalse(h.worker.is_alive(), "the worker must terminate")
            self.assertTrue(called, "the backoff must wait on the stop Event, not sleep")

    def test_a_broken_status_callback_cannot_kill_the_stream(self):
        with WorkerHarness(responses=[
                FakeResponse([b"Code=SmartMotionHuman;action=Start;index=0\r\n"], then="raise")]) as h:
            h.worker._status_cb = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
            h.worker.start()
            address, event = h.events.get(timeout=3)
            self.assertEqual(event.code, "SmartMotionHuman")


if __name__ == "__main__":
    unittest.main(verbosity=2)
