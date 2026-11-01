#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_auth_and_privacy.py
# Description: What happens when a camera REFUSES the login, tested against a
#              real local HTTP server. No camera.
#                - a wrong password is sent ONCE per request (1.22; it used to be
#                  six times, because urllib's digest handler retries by itself)
#                - a refused stream says LOGIN_REFUSED, not "reconnecting", and
#                  does not try again within the 30-minute LOGIN_RETRY
#                - a camera that accepts the login still connects (the one-try
#                  handler must not break the normal case), for Digest and Basic
#                - the password and username never reach a status detail, a
#                  probe reason, or the Show Plugin Info banner
#                - the plugin shows the refusal on the device, clears it on
#                  reconnect, and a probe does not paint over it
# Author:      CliveS & Claude Opus 5.5
# Date:        01-11-2026
# Version:     1.1

import http.server
import queue
import threading
import time
import urllib.error

import pytest

import dahua_probe
import dahua_worker
import plugin as plugin_module
from dahua_worker import CameraWorker
from test_camera_status_blocking import FakeDevice

USER     = "camuser_7f3a"
PASSWORD = "S3cret-Pass_91x"


def _make_server(scheme="Digest", accept=False):
    """A camera stand-in. Without an Authorization header every request gets a
    401 challenge (a fresh nonce each time, like a Dahua camera). With one, it
    answers 401 again (accept=False: wrong password) or 200 (accept=True)."""

    class Handler(http.server.BaseHTTPRequestHandler):
        hits = 0
        with_password = 0

        def do_GET(self):
            cls = type(self)
            cls.hits += 1
            if self.headers.get("Authorization"):
                cls.with_password += 1
                if accept:
                    body = b"events.EventList[0]=SmartMotionHuman\r\n"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            self.send_response(401)
            if scheme == "Digest":
                self.send_header("WWW-Authenticate",
                                 f'Digest realm="Login to TEST", qop="auth", '
                                 f'nonce="n{cls.hits}", opaque="o"')
            else:
                self.send_header("WWW-Authenticate", 'Basic realm="TEST"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, Handler


@pytest.fixture
def camera_factory():
    servers = []

    def make(**kw):
        server, handler = _make_server(**kw)
        servers.append(server)
        return f"127.0.0.1:{server.server_port}", handler

    yield make
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def refusing_camera(camera_factory):
    return camera_factory()


def _no_secret(text):
    return PASSWORD not in str(text) and USER not in str(text)


# ------------------------------------------------------------ one try per request

@pytest.mark.parametrize("scheme", ["Digest", "Basic"])
def test_one_connect_with_a_wrong_password_tries_it_once(camera_factory, scheme):
    address, handler = camera_factory(scheme=scheme)
    worker = CameraWorker(address, USER, PASSWORD, queue.Queue(), threading.Event())
    with pytest.raises(urllib.error.HTTPError) as caught:
        worker._open()
    assert dahua_probe.is_login_refused(caught.value)
    assert handler.with_password == 1, f"password sent {handler.with_password} times"
    assert handler.hits == 2           # the challenge, then the one try


def test_a_probe_fetch_with_a_wrong_password_tries_it_once(refusing_camera):
    address, handler = refusing_camera
    assert dahua_probe.fetch(address, "/cgi-bin/x.cgi", USER, PASSWORD, timeout=5) is None
    assert handler.with_password == 1


@pytest.mark.parametrize("scheme", ["Digest", "Basic"])
def test_a_camera_that_accepts_the_login_still_answers(camera_factory, scheme):
    address, handler = camera_factory(scheme=scheme, accept=True)
    body = dahua_probe.fetch(address, "/cgi-bin/x.cgi", USER, PASSWORD, timeout=5)
    assert body is not None and "SmartMotionHuman" in body
    assert handler.with_password == 1


def test_only_a_401_counts_as_a_refused_login():
    assert dahua_probe.is_login_refused(
        urllib.error.HTTPError("http://x", 401, "login refused", {}, None))
    assert not dahua_probe.is_login_refused(
        urllib.error.HTTPError("http://x", 503, "busy", {}, None))
    assert not dahua_probe.is_login_refused(OSError("no heartbeat from the camera"))


# ------------------------------------------------------------ the worker

def test_a_refused_stream_says_so_and_does_not_retry_soon(refusing_camera):
    address, handler = refusing_camera
    statuses = []
    stop = threading.Event()
    seen = threading.Event()

    def cb(addr, status, detail):
        statuses.append((addr, status, detail))
        if status == dahua_worker.LOGIN_REFUSED:
            seen.set()

    worker = CameraWorker(address, USER, PASSWORD, queue.Queue(), stop, status_cb=cb)
    worker.start()
    try:
        assert seen.wait(10), f"no LOGIN_REFUSED status after a refused login: {statuses}"
        tries = handler.with_password       # capability check + one stream connect
        # The old loop retried after 1s and then 2s; this must stay put.
        time.sleep(3.5)
        assert handler.with_password == tries, "the worker retried a refused login early"
        assert tries <= 2, f"{tries} failed logins before the worker settled"
    finally:
        worker.stop()
        worker.join(5)
    assert not worker.is_alive(), "a worker waiting LOGIN_RETRY must still stop at once"
    assert dahua_worker.RECONNECTING not in [s for _, s, _ in statuses]
    for _addr, _status, detail in statuses:
        assert _no_secret(detail), f"credentials leaked into a status detail: {detail!r}"


def test_the_login_retry_is_long():
    assert dahua_worker.LOGIN_RETRY >= 15 * 60


# ------------------------------------------------------------ privacy

def test_a_refused_probe_reason_carries_no_credentials(refusing_camera):
    address, _ = refusing_camera
    verdict, reason = dahua_probe.probe(address, USER, PASSWORD, timeout=5)
    assert verdict == dahua_probe.UNREACHABLE
    assert _no_secret(reason)
    assert _no_secret(dahua_probe.describe(verdict, reason, address))


def test_plugin_info_banner_never_shows_the_password(plugin):
    plugin.cam_user = USER
    plugin.cam_pass = PASSWORD
    for label, value in plugin._banner_extras():
        assert PASSWORD not in str(value), f"{label} shows the password"
        assert USER not in str(value), f"{label} shows the username"


# ------------------------------------------------------------ the plugin side

ADDRESS = "192.0.2.10"


def _plugin_with(dev, klass="person"):
    p = plugin_module.Plugin("com.clives.indigoplugin.dahuaevents", "DahuaEvents",
                             "1.22", {})
    plugin_module.indigo.devices = {dev.id: dev}
    p._by_camera[ADDRESS] = {klass: dev.id}
    return p


def _status(p, status, detail=""):
    p._statuses.put((ADDRESS, status, detail))
    p._drain_statuses()


def test_a_refused_login_shows_on_the_device_in_red():
    dev = FakeDevice(1, "Drive Person")
    p = _plugin_with(dev)
    _status(p, dahua_worker.LOGIN_REFUSED, "the camera refused the username or password")
    assert dev.states["streamState"] == "loginRefused"
    assert dev.errorState == "camera refused the login"


def test_reconnecting_clears_the_refusal_flag_only_when_connected():
    dev = FakeDevice(1, "Drive Person")
    p = _plugin_with(dev)
    _status(p, dahua_worker.LOGIN_REFUSED, "x")
    assert ADDRESS in p._login_refused
    _status(p, dahua_worker.CONNECTED)
    assert ADDRESS not in p._login_refused
    assert dev.states["streamState"] == "connected"
    assert dev.errorState == ""


def test_a_failed_probe_does_not_paint_over_the_refusal():
    """The probe cannot tell a refused login from a camera that is away (fetch
    returns None for both), so it must not replace what the stream knows."""
    dev = FakeDevice(1, "Drive Person")
    p = _plugin_with(dev)
    _status(p, dahua_worker.LOGIN_REFUSED, "x")
    p._verdict_for = lambda a, k: (dahua_probe.UNREACHABLE, "no answer on port 80")
    p._settle_device(dev.id, ADDRESS, "person")
    assert dev.states["streamState"] == "loginRefused"
    assert dev.errorState == "camera refused the login"


def test_a_device_that_is_on_starts_its_hold_when_the_login_is_refused():
    dev = FakeDevice(1, "Drive Person")
    p = _plugin_with(dev)
    timer = plugin_module.HoldTimer(20, 600)
    p._timers[dev.id] = timer
    timer.start(time.monotonic())
    _status(p, dahua_worker.LOGIN_REFUSED, "x")
    assert timer.expires_at is not None, "a refused login must arm the normal hold"
