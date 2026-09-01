#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    plugin.py
# Description: DahuaEvents — turns the Dahua cameras' own onboard smart-motion
#              detection into native Indigo devices, so person and vehicle
#              detections can drive triggers, notifications and dashboards without
#              Frigate, Scrypted NVR, a subscription, or any AI on the server.
#
#              STAGE 1 of 4 (see SPEC.md): scaffold, capability probing and the
#              diagnostic menus. The event stream, the device model and the hold
#              state machine arrive in stages 2-4. Nothing here opens a socket
#              except on demand from a menu item.
# Author:      CliveS & Claude Opus 5
# Date:        01-09-2026
# Version:     1.0

try:
    import indigo
except ImportError:
    pass

import logging
import os as _os
import sys as _sys
from datetime import datetime

_sys.path.insert(0, _os.getcwd())   # bundled alongside this file in Server Plugin/
try:
    from plugin_utils import log_startup_banner
except ImportError:
    log_startup_banner = None

import dahua_probe

# Camera credentials: IndigoSecrets.py first, PluginConfig as the fallback.
# Per-key try/except so a missing single key does not blank the others.
_sys.path.insert(0, "/Library/Application Support/Perceptive Automation")
try:
    from IndigoSecrets import DAHUA_USER
except ImportError:
    DAHUA_USER = ""
try:
    from IndigoSecrets import DAHUA_PASS
except ImportError:
    DAHUA_PASS = ""


# ============================================================
# Constants
# ============================================================

PLUGIN_ID      = "com.clives.indigoplugin.dahuaevents"
PLUGIN_VERSION = "1.0"

DEFAULT_HOLD_SECONDS = 20


# ============================================================
# Helpers
# ============================================================

_LOG_LEVELS = {
    "DEBUG":    logging.DEBUG,
    "INFO":     logging.INFO,
    "WARNING":  logging.WARNING,
    "ERROR":    logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _lvl(level):
    """Map a level NAME to a Python logging int.

    indigo.server.log(level=...) wants an int; a STRING is silently ignored and the
    line logs as plain Info. Kept for the banner path only — everything else in this
    plugin logs through self.logger.
    """
    if isinstance(level, int):
        return level
    return _LOG_LEVELS.get(str(level).upper(), logging.INFO)


def log(message, level="INFO"):
    indigo.server.log(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {message}",
                      level=_lvl(level))


# ============================================================
# Plugin class
# ============================================================

class Plugin(indigo.PluginBase):

    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        super().__init__(pluginId, pluginDisplayName, pluginVersion, pluginPrefs)

        # Credentials resolve once here so every path uses the same answer.
        self.cam_user = DAHUA_USER or pluginPrefs.get("dahuaUser", "")
        self.cam_pass = DAHUA_PASS or pluginPrefs.get("dahuaPass", "")
        self.hold_seconds = self._hold_from_prefs(pluginPrefs)

        # Stage 1 keeps no runtime state: no sockets, no threads, no timers.
        # Boot logs nothing — Indigo's own start line is enough (25-05-2026 convention).

    # --------------------------------------------------------
    # Config coercion
    # --------------------------------------------------------

    def _hold_from_prefs(self, prefs):
        """Detection hold in seconds, coerced AND guarded.

        A saved dialog re-serialises even numeric fields as strings, and a cleared
        field arrives as "". Both must produce a working default rather than an
        exception in __init__, which would stop the plugin loading at all.
        """
        raw = prefs.get("holdSeconds", DEFAULT_HOLD_SECONDS)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            self.logger.warning(
                f"holdSeconds is not a number ({raw!r}) — using {DEFAULT_HOLD_SECONDS}s")
            return DEFAULT_HOLD_SECONDS
        if value < 0:
            self.logger.warning(f"holdSeconds cannot be negative ({value}) — using 0s")
            return 0
        return value

    # --------------------------------------------------------
    # Lifecycle
    # --------------------------------------------------------

    def startup(self):
        if not (self.cam_user and self.cam_pass):
            # Awaiting configuration is INFO, not an error — nothing has gone wrong yet.
            self.logger.info(
                "No camera credentials yet. Set DAHUA_USER / DAHUA_PASS in IndigoSecrets.py, "
                "or fill them in via Plugins -> DahuaEvents -> Configure.")

    def shutdown(self):
        pass

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        """Mirror the startup guards, or a re-save leaves the plugin on stale values."""
        if userCancelled:
            return
        self.cam_user = DAHUA_USER or valuesDict.get("dahuaUser", "")
        self.cam_pass = DAHUA_PASS or valuesDict.get("dahuaPass", "")
        self.hold_seconds = self._hold_from_prefs(valuesDict)

    # --------------------------------------------------------
    # Device lifecycle — stage 3
    # --------------------------------------------------------

    def deviceStartComm(self, dev):
        self.logger.debug(f"deviceStartComm: {dev.name}")

    def deviceStopComm(self, dev):
        self.logger.debug(f"deviceStopComm: {dev.name}")

    # --------------------------------------------------------
    # Probing
    # --------------------------------------------------------

    def _banner_extras(self):
        creds = "IndigoSecrets" if DAHUA_USER else ("PluginConfig" if self.cam_user else "NOT SET")
        return [
            ("Credentials:", creds),
            ("Hold:",        f"{self.hold_seconds}s"),
            ("Stage:",       "1 of 4 — probing only, no event streams yet"),
        ]

    def _probe_and_log(self, address):
        """Probe one camera and log the verdict. Returns the verdict string."""
        verdict, reason = dahua_probe.probe(address, self.cam_user, self.cam_pass)
        line = dahua_probe.describe(verdict, reason, address)
        fw = dahua_probe.firmware(address, self.cam_user, self.cam_pass)
        if fw:
            line += f"  (firmware {fw})"
        if verdict == dahua_probe.CAPABLE:
            self.logger.info(line)
        elif verdict == dahua_probe.UNREACHABLE:
            self.logger.error(line)
        else:
            self.logger.warning(line)
        return verdict

    # --------------------------------------------------------
    # Menu handlers
    # --------------------------------------------------------

    def showPluginInfo(self, valuesDict=None, typeId=None):
        if log_startup_banner:
            log_startup_banner(self.pluginId, self.pluginDisplayName, self.pluginVersion,
                               extras=self._banner_extras())
        else:
            indigo.server.log(f"{self.pluginDisplayName} v{self.pluginVersion}")

    def probeCamera(self, valuesDict=None, typeId=None):
        """Probe a single camera by address, entered in the menu item's dialog.

        A permanent diagnostic: it answers 'can this camera do smart detection'
        without needing a device, which is exactly the question asked when a camera
        is swapped or its firmware updated.
        """
        if log_startup_banner:
            log_startup_banner(self.pluginId, self.pluginDisplayName, self.pluginVersion,
                               extras=self._banner_extras())
        address = (valuesDict or {}).get("address", "").strip()
        if not address:
            self.logger.error("No camera address given.")
            return False
        self._probe_and_log(address)
        return True

    def testConnection(self, valuesDict=None, typeId=None):
        """Probe every camera this plugin knows about, and say so when it knows none."""
        if log_startup_banner:
            log_startup_banner(self.pluginId, self.pluginDisplayName, self.pluginVersion,
                               extras=self._banner_extras())

        addresses = sorted({
            dev.pluginProps.get("address", "").strip()
            for dev in indigo.devices.iter("self")
            if dev.pluginProps.get("address", "").strip()
        })

        if not addresses:
            # Say what was NOT checked. A sweep that covered nothing must never read
            # as a sweep that found nothing wrong.
            self.logger.warning(
                "No cameras configured yet, so nothing was tested. Add a camera, or use "
                "'Probe a Camera...' to check one by address.")
            return True

        self.logger.info(f"Probing {len(addresses)} camera(s)...")
        counts = {}
        for address in addresses:
            verdict = self._probe_and_log(address)
            counts[verdict] = counts.get(verdict, 0) + 1
        self.logger.info("Probe complete: " +
                         ", ".join(f"{n} {v}" for v, n in sorted(counts.items())))
        return True
