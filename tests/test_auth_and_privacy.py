#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_auth_and_privacy.py
# Description: What happens when a camera REFUSES the login, tested against a
#              real local HTTP server that answers every request with a Digest
#              401 and a fresh nonce (the shape a Dahua camera uses). No camera.
#                - the password and username never reach a status detail, a
#                  probe reason, or the Show Plugin Info banner
#                - XFAIL: one connect attempt with a wrong password must not
#                  send the wrong password several times over (it sends it six
#                  times — urllib's digest handler retries up to 5 times on its
#                  own, and Dahua cameras lock an account after a few failures)
# Author:      CliveS & Claude Opus 5.5
# Date:        01-11-2026
# Version:     1.0

import http.server
import queue
import threading

import pytest

import dahua_probe
import dahua_worker
from dahua_worker import CameraWorker

USER     = "camuser_7f3a"
PASSWORD = "S3cret-Pass_91x"


class _Refuser(http.server.BaseHTTPRequestHandler):
    """Every request gets a Digest challenge with a new nonce, like a camera
    that does not accept these credentials."""
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        self.send_response(401)
        self.send_header("WWW-Authenticate",
                         f'Digest realm="Login to TEST", qop="auth", '
                         f'nonce="n{type(self).hits}", opaque="o"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args):
        pass


@pytest.fixture
def refusing_camera():
    handler = type("Refuser", (_Refuser,), {"hits": 0})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_port}", handler
    finally:
        server.shutdown()
        server.server_close()


def _no_secret(text):
    return PASSWORD not in str(text) and USER not in str(text)


# ------------------------------------------------------------ privacy

def test_a_refused_stream_reports_reconnecting_without_the_credentials(refusing_camera):
    address, _ = refusing_camera
    statuses = []
    stop = threading.Event()
    seen = threading.Event()

    def cb(addr, status, detail):
        statuses.append((addr, status, detail))
        if status == dahua_worker.RECONNECTING:
            seen.set()

    worker = CameraWorker(address, USER, PASSWORD, queue.Queue(), stop, status_cb=cb)
    worker.start()
    try:
        assert seen.wait(10), f"no RECONNECTING status after a refused login: {statuses}"
    finally:
        worker.stop()
        worker.join(5)
    assert not worker.is_alive()
    for _addr, _status, detail in statuses:
        assert _no_secret(detail), f"credentials leaked into a status detail: {detail!r}"


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


# ------------------------------------------------------------ lockout risk

@pytest.mark.xfail(strict=True, reason=(
    "BUG: CameraWorker._open (and dahua_probe.fetch) use urllib's "
    "HTTPDigestAuthHandler, which retries a rejected login itself up to ~5 times. "
    "One connect with a wrong password sends it 6 times, and the worker repeats "
    "that every <=60s for ever, which can keep a Dahua account locked out."))
def test_one_connect_with_a_wrong_password_tries_it_at_most_once(refusing_camera):
    address, handler = refusing_camera
    worker = CameraWorker(address, USER, PASSWORD, queue.Queue(), threading.Event())
    with pytest.raises(Exception):
        worker._open()
    # 1 unauthenticated request to get the challenge + 1 with the password.
    assert handler.hits <= 2, f"{handler.hits} requests for one connect attempt"
