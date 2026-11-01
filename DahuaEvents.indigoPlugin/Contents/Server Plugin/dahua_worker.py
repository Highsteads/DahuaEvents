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
# Author:      CliveS & Claude Sonnet 5
# Date:        01-11-2026
# Version:     1.2
#
# v1.2 (01-11-2026): a refused login is no longer retried every minute. The
# stream used to treat a 401 like any dropped connection: "reconnecting", then
# again after at most 60 seconds, for ever, and each try sent the wrong password
# six times (see dahua_probe v1.3). With the camera locking an account after a
# few failures, a password changed on the camera but not in Indigo kept the
# account locked, for the NVR and the phone app too. Now one try per connect, a
# LOGIN_REFUSED status that says what happened, and the next try 30 minutes on.
#
# v1.1 (03-09-2026): CONNECTED is now re-affirmed periodically while a stream
# stays open, healthy and silent — not just once, at open. status_cb used to
# fire CONNECTED a single time and never again until the status genuinely
# changed, so a camera with nothing to report (no detection, no reconnect) never
# triggered another device-state write — and plugin.py's _drain_statuses is the
# ONLY place a state gets written, so Indigo's lastSuccessfulComm froze at
# stream-open time. DeviceHealthMonitor's watchdog reads exactly that clock, and
# on a quiet night (nobody walking past four cameras for 70+ minutes) it
# restarted this plugin three times and paged CliveS at 04:39 for cameras that
# answered in under 100ms when checked by hand — see TRIAGE_QUEUE.md,
# 03-Sep-2026, Perceptive Automation project root. Fixed at the source rather
# than relying on the watchdog knowing to be lenient with this plugin.

import select
import threading
import time

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

# After the camera turns the username or password down. Trying again sooner
# only adds failed logins, and Dahua locks an account after a few of them; a
# lock also refuses the RIGHT password until it times out, which is why this
# still retries rather than giving up. New credentials saved in Configure start
# a fresh worker at once, so fixing it in Indigo does not wait for this.
LOGIN_RETRY   = 1800

# A camera sends a heartbeat every 5s. Three missed beats means the connection is
# dead even though the socket is still nominally open — the classic half-open TCP
# case that a plain blocking read never notices.
READ_TIMEOUT  = 20

# How often CONNECTED is re-affirmed to status_cb while the stream is open,
# healthy and has nothing new to say. Without this, a quiet camera writes a
# device state exactly ONCE — at stream-open — and lastSuccessfulComm freezes
# from that moment, indistinguishable from a dead worker to anything watching
# comm age (DeviceHealthMonitor's plugin watchdog, in particular). Comfortably
# under any sane wedge threshold and cheap: one extra queued status per camera
# every five minutes, no HTTP traffic, no log line (status_cb's detail stays
# empty, and plugin.py only logs when detail is truthy).
STATUS_REAFFIRM = 300

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
LOGIN_REFUSED = "loginRefused"
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
        # The shared opener: it sends the password once and stops if it is refused.
        opener = dahua_probe.make_opener(url, self._user, self._password)
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

        A healthy stream that never has an EVENT to report (no detection, no
        status change) would otherwise never call status_cb again after the
        opening CONNECTED — see STATUS_REAFFIRM. That re-affirm is checked here,
        not folded into the heartbeat check above, because they answer different
        questions: last_data is "is the socket still alive", last_reaffirm is
        "have we told the plugin lately" — collapsing them would mean a
        reconnect resets the very clock that is supposed to catch a stream that
        stays open but stops being useful.
        """
        parser = StreamParser()
        self._response = self._open()
        self._set_status(CONNECTED)
        last_data = time.monotonic()
        last_reaffirm = last_data

        while not self._stop.is_set():
            try:
                readable, _, _ = select.select([self._response], [], [], SELECT_TICK)
            except (OSError, ValueError):
                # The response was closed under us — that is a stop, not a fault.
                return
            if not readable:
                now = time.monotonic()
                if now - last_data > READ_TIMEOUT:
                    raise OSError("no heartbeat from the camera")
                if now - last_reaffirm >= STATUS_REAFFIRM:
                    self._set_status(CONNECTED)
                    last_reaffirm = now
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
        # A code the firmware sends WITHOUT listing it (a doorbell's button, see
        # dahua_probe.UNADVERTISED_CODES) counts as offered, or a doorbell-only
        # camera would be halted here for ever over an event it really does send.
        offered = advertised | dahua_probe.UNADVERTISED_CODES
        if advertised and not (self.codes & offered):
            self._set_status(UNSUPPORTED,
                             "firmware advertises none of " + ", ".join(sorted(self.codes)))
            return

        backoff = BACKOFF_START
        while not self._stop.is_set():
            wait = backoff
            try:
                self._pump()
                backoff = BACKOFF_START          # a clean run resets the penalty
                wait = backoff
            except Exception as exc:             # noqa: BLE001 - one camera, one failure
                if self._stop.is_set():
                    break
                if dahua_probe.is_login_refused(exc):
                    # Not a network fault, so not "reconnecting": say what it is,
                    # and do not hand the camera another failed login for a while.
                    self._set_status(LOGIN_REFUSED,
                                     f"the camera refused the username or password; "
                                     f"trying again in {LOGIN_RETRY // 60} minutes")
                    wait = LOGIN_RETRY
                else:
                    self._set_status(RECONNECTING, f"{type(exc).__name__}: {exc}")
            finally:
                self._close_response()
            if self._stop.wait(wait):
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
