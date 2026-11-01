#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_stream_buffering.py
# Description: Events must reach the plugin when they ARRIVE, not when the camera
#              next sends something. The worker waits with select() on the
#              socket, but Python's HTTP layer reads ahead into its own buffer:
#              with chunked transfer encoding (each event wrapped in a size line)
#              it pulls several chunks in at once and hands back one, and
#              select() then sees an empty socket while the rest sits in that
#              buffer. The same happens to an event that arrives in the same
#              packet as the response headers. The stuck event came out only
#              with the next heartbeat, up to 5 s later.
#              Tested against a real local socket server and Python's own
#              http.client, so the buffering is the real thing, not a fake.
# Author:      CliveS & Claude Opus 5.5
# Date:        01-11-2026
# Version:     1.0

import queue
import socket
import threading
import time

import pytest

import dahua_worker
from dahua_stream import Event
from dahua_worker import CameraWorker

START = b"Code=SmartMotionHuman;action=Start;index=0\r\n"
STOP  = b"Code=SmartMotionHuman;action=Stop;index=0\r\n"


def _chunk(data):
    return f"{len(data):x}\r\n".encode() + data + b"\r\n"


class OneShotCamera:
    """Accepts one connection, sends the headers and `body` in ONE write, then
    stays silent (no heartbeat) until closed."""

    def __init__(self, body, chunked):
        self.body, self.chunked = body, chunked
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.address = f"127.0.0.1:{self.server.getsockname()[1]}"
        self.conn = None
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.server.accept()
        self.conn = conn
        request = b""
        while b"\r\n\r\n" not in request:
            request += conn.recv(4096)
        head = (b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: multipart/x-mixed-replace; boundary=myboundary\r\n")
        head += b"Transfer-Encoding: chunked\r\n\r\n" if self.chunked else b"Connection: close\r\n\r\n"
        conn.sendall(head + self.body)

    def close(self):
        for s in (self.conn, self.server):
            try:
                s.close()
            except Exception:
                pass


def _events_within(camera, seconds, until=None):
    """Run the worker's real _pump against the camera; return the events that
    arrived within `seconds`, stopping early once `until` is among them."""
    out = queue.Queue()
    stop = threading.Event()
    worker = CameraWorker(camera.address, "u", "p", out, stop,
                          codes={"SmartMotionHuman"})
    thread = threading.Thread(target=lambda: _quietly(worker._pump), daemon=True)
    thread.start()
    deadline = time.monotonic() + seconds
    got = []
    while time.monotonic() < deadline:
        try:
            got.append(out.get(timeout=0.05)[1])
        except queue.Empty:
            continue
        if until is not None and until in got:
            break
    stop.set()
    thread.join(dahua_worker.SELECT_TICK + 2)
    camera.close()
    return got


def _quietly(fn):
    try:
        fn()
    except Exception:
        pass


@pytest.fixture
def camera():
    made = []

    def make(body, chunked):
        c = OneShotCamera(body, chunked)
        made.append(c)
        return c

    yield make
    for c in made:
        c.close()


def test_two_chunks_in_one_packet_both_arrive_at_once(camera):
    cam = camera(_chunk(START) + _chunk(STOP), chunked=True)
    assert _events_within(cam, 2.0, until=Event("SmartMotionHuman", "Stop", 0)) == [Event("SmartMotionHuman", "Start", 0),
                                         Event("SmartMotionHuman", "Stop", 0)]


def test_an_event_larger_than_one_read_arrives_whole(camera):
    """CHUNK is 512 bytes, and an event with its JSON body can be longer."""
    padding = b"   \"RegionName\" : [ \"" + b"x" * 900 + b"\" ]\r\n"
    cam = camera(_chunk(START + padding + STOP), chunked=True)
    assert Event("SmartMotionHuman", "Stop", 0) in _events_within(cam, 2.0, until=Event("SmartMotionHuman", "Stop", 0))


def test_an_event_in_the_same_packet_as_the_headers_arrives_at_once(camera):
    cam = camera(START + STOP, chunked=False)
    assert _events_within(cam, 2.0, until=Event("SmartMotionHuman", "Stop", 0)) == [Event("SmartMotionHuman", "Start", 0),
                                         Event("SmartMotionHuman", "Stop", 0)]


def test_a_buffered_stream_still_stops_promptly(camera):
    cam = camera(_chunk(START), chunked=True)
    out, stop = queue.Queue(), threading.Event()
    worker = CameraWorker(cam.address, "u", "p", out, stop, codes={"SmartMotionHuman"})
    thread = threading.Thread(target=lambda: _quietly(worker._pump), daemon=True)
    thread.start()
    out.get(timeout=2)
    began = time.monotonic()
    stop.set()
    thread.join(5)
    assert not thread.is_alive()
    assert time.monotonic() - began < dahua_worker.SELECT_TICK + 0.5


def test_python_still_exposes_the_socket_the_fix_needs(camera):
    """_buffered() reaches the socket through response.fp.raw._sock, which
    Python does not document. If a Python upgrade removes it the fix quietly
    turns itself off, so this test is what notices."""
    cam = camera(_chunk(START), chunked=True)
    worker = CameraWorker(cam.address, "u", "p", queue.Queue(), threading.Event(),
                          codes={"SmartMotionHuman"})
    response = worker._open()
    try:
        assert isinstance(response.fp.raw._sock, socket.socket)
    finally:
        response.close()


def test_looking_in_the_buffer_puts_the_timeout_back(camera):
    cam = camera(_chunk(START), chunked=True)
    worker = CameraWorker(cam.address, "u", "p", queue.Queue(), threading.Event(),
                          codes={"SmartMotionHuman"})
    worker._response = worker._open()
    try:
        sock = worker._response.fp.raw._sock
        before = sock.gettimeout()
        assert before == dahua_worker.READ_TIMEOUT
        time.sleep(0.2)
        assert worker._buffered() is True          # the event is sitting there
        assert sock.gettimeout() == before
    finally:
        worker._close_response()


def test_a_response_with_nothing_buffered_says_so():
    class Bare:
        pass
    worker = CameraWorker("127.0.0.1:1", "u", "p", queue.Queue(), threading.Event())
    worker._response = Bare()
    assert worker._buffered() is False
