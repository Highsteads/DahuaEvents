#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    conftest.py
# Description: Shared test seam. Puts the bundle's "Server Plugin" folder on
#              sys.path and installs the ONE indigo stand-in this suite uses
#              (test_event_log_quiet._install_stubs) before any test module is
#              collected, so plugin.py can be imported with no Indigo server.
#              Deliberately does NOT define a second stub: the existing tests rely
#              on there being exactly one sys.modules["indigo"].
# Author:      CliveS & Claude Opus 5.5
# Date:        01-11-2026
# Version:     1.0

import os
import sys

import pytest

TESTS_DIR     = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT     = os.path.dirname(TESTS_DIR)
SERVER_PLUGIN = os.path.join(REPO_ROOT, "DahuaEvents.indigoPlugin", "Contents", "Server Plugin")

for _path in (SERVER_PLUGIN, TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from test_event_log_quiet import _install_stubs, make_plugin  # noqa: E402

_install_stubs()


@pytest.fixture
def plugin():
    """A fresh Plugin instance built on the shared indigo stand-in."""
    return make_plugin(holdSeconds="20", maxDetectionMinutes="10")
