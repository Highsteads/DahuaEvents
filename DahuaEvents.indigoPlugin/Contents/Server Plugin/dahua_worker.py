#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    dahua_worker.py
# Description: One worker thread per camera, holding the long-poll open and pushing
#              decoded events onto a queue. The thread owns its socket and NOTHING
#              else — it never touches an Indigo device. All device writes happen on
#              the plugin's main thread, which is what makes the whole design
#              lock-free: the queue is the only shared object and it is already
#              thread-safe.
#
#              Reconnection backs off and CAPS, and every wait is on an Event so a
#              stop is immediate rather than up to a minute late. A camera that fails
#              the capability check halts rather than retrying for ever — quiet, back
#              off, then stop.
# Author:      CliveS & Claude Opus 5
# Date:        01-09-2026
# Version:     1.0

import select
import threading
import time
import urllib.error
import urllib.request

import dahua_probe
from dahua_stream import StreamParser

def attach_path(codes):
    """Subscribe to exactly the codes this camera's devices need.

    Built from the devices rather than hardcoded, because a camera may be doing
    SMD, IVS, or a mixture, and asking for codes the firmware does not know is a
    good way to get an unhelpful error from an old camera.
    """
    return ("/cgi-bin/eventManager.cgi?action=attach"
            f"&codes=[{','.join(sorted(codes))}]&heartbeat=5")

# Backoff: 1, 2, 4 ... capped. Uncapped doubling reaches an hour, which reads as
# "the camera never came back" when it simply was not asked.
BACKOFF_START = 1
BACKOFF_MAX   = 60

# A camera sends a heartbeat every 5s. Three missed beats means the connection is
# dead even though the socket is still nominally open — the classic half-open TCP
# case that a plain blocking read never notices.
READ_TIMEOUT  = 20

# How long we ever sit inside select() before looking at the stop flag again. This
# is the single number that decides how fast the plugin can quit, because a thread
# blocked in read() cannot be interrupted: closing the response from another thread
# WAITS for the pending read, so a 20s read timeout meant a 20s shutdown and Indigo
# force-killed the process. Measured 01-09-2026: 25.8s to stop five workers.
SELECT_TICK   = 1.0
CHUNK         = 512

CONNECTED    = "connected"
RECONNECTING = "reconnecting"
UNSUPPORTED  = "unsupported"
STOPPED      = "stopped"


class CameraWorker(threading.Thread):
    """Streams one camera's events onto `out_queue` as (address, Event) tuples.

    `status_cb(address, status, detail)` is called on every state change so the
    plugin can reflect it on the devices — from the MAIN thread, because the
    callback only enqueues.
    """

    def __init__(self, address, user, password, out_queue, stop_event,
                 status_cb=None, name=None, codes=None):
        super().__init__(daemon=True, name=name or f"DahuaWorker-{address}")
        self.address    = address
        self.codes      = set(codes) if codes else set(dahua_probe.SMART_CODES)
        self._user      = user
        self._password  = password
        self._queue     = out_queue
        self._stop      = stop_event
        self._status_cb = status_cb
        self._response  = None
        self.events_seen = 0

    # ----------------------------------------------------------
    def _set_status(self, status, detail=""):
        if self._status_cb:
            try:
                self._status_cb(self.address, status, detail)
            except Exception:
                pass        # a broken callback must never kill the stream

    def _open(self):
        url = f"http://{self.address}{attach_path(self.codes)}"
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, url, self._user, self._password)
        opener = urllib.request.build_opener(
            urllib.request.HTTPDigestAuthHandler(mgr),
            urllib.request.HTTPBasicAuthHandler(mgr))
        return opener.open(url, timeout=READ_TIMEOUT)

    def _pump(self):
        """Read until the stream dies or we are asked to stop.

        NEVER blocks in read(). select() waits for the socket to become readable,
        with a short tick, so the stop flag is looked at at least once a second and
        read() only ever runs when bytes are already there. A thread blocked in
        read() cannot be interrupted — and close() from another thread waits for it
        rather than cancelling it, which is what made shutdown take 20+ seconds and
        got the plugin force-killed.

        Silence is normal and is NOT an error: the camera sends a heartbeat every
        five seconds, so most ticks legitimately have nothing to read. Only a run of
        silence longer than several heartbeats means the connection is really dead.
        """
        parser = StreamParser()
        self._response = self._open()
        self._set_status(CONNECTED)
        last_data = time.monotonic()

        while not self._stop.is_set():
            try:
                readable, _, _ = select.select([self._response], [], [], SELECT_TICK)
            except (OSError, ValueError):
                # The response was closed under us — that is a stop, not a fault.
                return
            if not readable:
                if time.monotonic() - last_data > READ_TIMEOUT:
                    raise OSError("no heartbeat from the camera")
                continue

            chunk = self._response.read1(CHUNK)
            if not chunk:
                raise OSError("camera closed the stream")
            last_data = time.monotonic()
            for event in parser.feed(chunk.decode("utf-8", errors="replace")):
                self.events_seen += 1
                self._queue.put((self.address, event))

    def run(self):
        # The capability check happens ONCE, up front, and asks only whether this
        # camera can emit ANY of the codes we want. Whether each individual class
        # will actually fire is a per-DEVICE question, decided in deviceStartComm
        # and shown on that device — a camera may do SMD perfectly while having no
        # IVS rule drawn, and halting the whole stream for that would take the
        # working half down with the broken one.
        advertised = dahua_probe.parse_event_list(
            dahua_probe.fetch(self.address,
                              "/cgi-bin/eventManager.cgi?action=getExposureEvents",
                              self._user, self._password) or "")
        if advertised and not (self.codes & advertised):
            self._set_status(UNSUPPORTED,
                             "firmware advertises none of " + ", ".join(sorted(self.codes)))
            return

        backoff = BACKOFF_START
        while not self._stop.is_set():
            try:
                self._pump()
                backoff = BACKOFF_START          # a clean run resets the penalty
            except Exception as exc:             # noqa: BLE001 - one camera, one failure
                if self._stop.is_set():
                    break
                self._set_status(RECONNECTING, f"{type(exc).__name__}: {exc}")
            finally:
                self._close_response()
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, BACKOFF_MAX)
        self._set_status(STOPPED)

    def _close_response(self):
        if self._response is not None:
            try:
                self._response.close()
            except Exception:
                pass
            self._response = None

    def stop(self):
        """Ask the thread to finish.

        Deliberately does NOT close the response. close() blocks until any pending
        read completes, so calling it from the plugin's thread made shutdown wait
        out the socket timeout — the very thing this is supposed to avoid. The
        worker notices the stop Event within SELECT_TICK and closes its own socket
        on the way out, which is the only thread that can do it without blocking.
        """
        self._stop.set()
