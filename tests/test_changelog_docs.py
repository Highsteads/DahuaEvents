#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_changelog_docs.py
# Description: docs/changelog.md (the published version history) must have an
#              entry for the version Info.plist ships, as its NEWEST entry.
#              test_version_consistency.py already checks the README; this is the
#              same check for the docs site, which nothing watched.
# Author:      CliveS & Claude Opus 5.5
# Date:        01-11-2026
# Version:     1.0

import os
import plistlib
import re

import pytest

from conftest import REPO_ROOT

CHANGELOG = os.path.join(REPO_ROOT, "docs", "changelog.md")
PLIST     = os.path.join(REPO_ROOT, "DahuaEvents.indigoPlugin", "Contents", "Info.plist")
ENTRY_RE  = re.compile(r"^##\s+v?(\d+(?:\.\d+)+)\b", re.M)


def _version():
    with open(PLIST, "rb") as fh:
        return plistlib.load(fh)["PluginVersion"]


def _entries():
    if not os.path.isfile(CHANGELOG):
        pytest.skip("no docs/changelog.md")
    with open(CHANGELOG, encoding="utf-8") as fh:
        return ENTRY_RE.findall(fh.read())


def test_docs_changelog_newest_entry_is_this_version():
    entries = _entries()
    assert entries, "docs/changelog.md has no '## <version>' entries"
    assert entries[0] == _version(), (
        f"newest docs/changelog.md entry is {entries[0]}, Info.plist says {_version()}")


def test_docs_changelog_has_no_duplicate_versions():
    entries = _entries()
    dupes = sorted({v for v in entries if entries.count(v) > 1})
    assert not dupes, f"versions listed twice: {dupes}"
