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

import threading
import urllib.error
import urllib.request

import dahua_probe
from dahua_stream import StreamParser

ATTACH_PATH = ("/cgi-bin/eventManager.cgi?action=attach"
               "&codes=[SmartMotionHuman,SmartMotionVehicle]&heartbeat=5")

# Backoff: 1, 2, 4 ... capped. Uncapped doubling reaches an hour, which reads as
# "the camera never came back" when it simply was not asked.
BACKOFF_START = 1
BACKOFF_MAX   = 60

# A camera sends a heartbeat every 5s. Three missed beats means the connection is
# dead even though the socket is still nominally open — the classic half-open TCP
# case that a plain blocking read never notices.
READ_TIMEOUT  = 20
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
                 status_cb=None, name=None):
        super().__init__(daemon=True, name=name or f"DahuaWorker-{address}")
        self.address    = address
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
        url = f"http://{self.address}{ATTACH_PATH}"
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, url, self._user, self._password)
        opener = urllib.request.build_opener(
            urllib.request.HTTPDigestAuthHandler(mgr),
            urllib.request.HTTPBasicAuthHandler(mgr))
        return opener.open(url, timeout=READ_TIMEOUT)

    def _pump(self):
        """Read until the stream dies or we are asked to stop. Returns normally on
        a clean stop; raises on anything else so the caller can back off."""
        parser = StreamParser()
        self._response = self._open()
        self._set_status(CONNECTED)
        while not self._stop.is_set():
            chunk = self._response.read(CHUNK)
            if not chunk:
                raise OSError("camera closed the stream")
            for event in parser.feed(chunk.decode("utf-8", errors="replace")):
                self.events_seen += 1
                self._queue.put((self.address, event))

    def run(self):
        # The capability check happens ONCE, up front. A camera whose firmware
        # cannot emit these events will never start emitting them, so retrying is
        # pure noise — halt and say why.
        verdict, reason = dahua_probe.probe(self.address, self._user, self._password)
        if verdict == dahua_probe.UNSUPPORTED:
            self._set_status(UNSUPPORTED, reason)
            return
        if verdict == dahua_probe.DISABLED:
            # Recoverable: the user can switch it on at the camera, so keep trying,
            # but say what is wrong rather than silently reconnecting for ever.
            self._set_status(RECONNECTING, reason)

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
        """Ask the thread to finish. The stop Event is shared, so this is belt and
        braces for a single worker; closing the response unblocks a pending read."""
        self._close_response()
