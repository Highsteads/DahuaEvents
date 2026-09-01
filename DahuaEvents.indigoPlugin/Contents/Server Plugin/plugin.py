#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    plugin.py
# Description: DahuaEvents — turns the Dahua cameras' own onboard smart-motion
#              detection into native Indigo devices, so person and vehicle
#              detections can drive triggers, notifications and dashboards without
#              Frigate, Scrypted NVR, a subscription, or any AI on the server.
#
#              STAGE 3 of 4 (see SPEC.md): live. A worker thread per camera holds
#              the long-poll and pushes events onto a queue; the plugin's main
#              thread drains it and is the ONLY thing that writes a device state.
#              That is what makes this lock-free — the queue is the sole shared
#              object and it is already thread-safe.
#
#              Stage 4 remains: rollout across all cameras, README, release.
# Author:      CliveS & Claude Opus 5
# Date:        01-09-2026
# Version:     1.1

try:
    import indigo
except ImportError:
    pass

import logging
import os as _os
import queue
import sys as _sys
import threading
import time
from datetime import datetime

_sys.path.insert(0, _os.getcwd())   # bundled alongside this file in Server Plugin/
try:
    from plugin_utils import log_startup_banner
except ImportError:
    log_startup_banner = None

import dahua_probe
from dahua_stream import HoldTimer
from dahua_worker import CameraWorker

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
PLUGIN_VERSION = "1.1"

DEFAULT_HOLD_SECONDS = 20

# Detection classes, and the camera event code that drives each.
CLASS_CODES = {
    "person":  "SmartMotionHuman",
    "vehicle": "SmartMotionVehicle",
}
CLASS_LABELS = {"person": "Person", "vehicle": "Vehicle"}

DEVICE_TYPE = "dahuaDetection"
MODEL_NAME  = "Dahua Camera"

# The drain tick. Short enough that a hold expires close to its moment, long
# enough that an idle plugin costs nothing measurable.
DRAIN_TICK = 0.5


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

        # Runtime state. OWNERSHIP, deliberately: everything below is touched only
        # by the plugin's main thread (Indigo dispatches every callback on it), with
        # the single exception of _events, which is the thread-safe hand-off from the
        # workers. No other lock is needed, and none is taken.
        self._events   = queue.Queue()       # (address, Event) from the workers
        self._statuses = queue.Queue()       # (address, status, detail) from the workers
        self._workers  = {}                  # address -> CameraWorker
        self._stops    = {}                  # address -> threading.Event
        self._timers   = {}                  # device id -> HoldTimer
        self._by_camera = {}                 # address -> {class -> device id}

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
        self._stop_all_workers()

    def _stop_all_workers(self):
        for address in list(self._workers):
            self._stop_worker(address)

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
        address = dev.pluginProps.get("address", "").strip()
        klass   = dev.pluginProps.get("detectionClass", "person")
        if not address:
            self._mark_error(dev, "no camera address configured")
            return

        # A binary sensor needs SupportsOnState for the native onOffState to exist
        # at all. Setting it also makes Indigo re-derive displayStateId, which is
        # read-only afterwards — so it has to happen before anything is written.
        props = dict(dev.pluginProps)
        if not props.get("SupportsOnState"):
            props["SupportsOnState"]     = True
            props["SupportsSensorValue"] = False
            dev.replacePluginPropsOnServer(props)
            dev = indigo.devices[dev.id]        # re-fetch: the old object is stale

        self._timers[dev.id] = HoldTimer(self._hold_for(dev))
        self._by_camera.setdefault(address, {})[klass] = dev.id
        dev.updateStateOnServer("onOffState", False)
        self._ensure_worker(address)
        self.logger.debug(f"deviceStartComm: {dev.name} ({address}, {klass})")

    def deviceStopComm(self, dev):
        address = dev.pluginProps.get("address", "").strip()
        klass   = dev.pluginProps.get("detectionClass", "person")
        self._timers.pop(dev.id, None)
        if address in self._by_camera:
            self._by_camera[address].pop(klass, None)
            # The stream is shared by the pair, so it only stops when the last
            # device using it goes. Stopping on the first would silently kill the
            # other half of the camera.
            if not self._by_camera[address]:
                del self._by_camera[address]
                self._stop_worker(address)
        self.logger.debug(f"deviceStopComm: {dev.name}")

    @staticmethod
    def didDeviceCommPropertyChange(oldDevice, newDevice):
        """Restart comm only for changes that actually affect the connection.

        The default restarts on ANY prop change, including the hold, which would
        drop and rebuild a perfectly good stream every time the user nudges a
        number.
        """
        old, new = oldDevice.pluginProps, newDevice.pluginProps
        return (old.get("address") != new.get("address")
                or old.get("detectionClass") != new.get("detectionClass"))

    def _hold_for(self, dev):
        """Per-device override, falling back to the plugin default. Guarded: a
        blank or non-numeric override must not stop the device starting."""
        raw = dev.pluginProps.get("holdOverride", "")
        if str(raw).strip() == "":
            return self.hold_seconds
        try:
            value = int(raw)
        except (TypeError, ValueError):
            self.logger.warning(
                f"{dev.name}: hold override {raw!r} is not a number — "
                f"using the plugin default of {self.hold_seconds}s")
            return self.hold_seconds
        return max(0, value)

    def _mark_error(self, dev, reason):
        try:
            dev.updateStateOnServer("streamState", "unsupported")
            dev.setErrorStateOnServer(reason)
        except Exception:
            self.logger.exception(f"could not mark {dev.name} in error")

    # --------------------------------------------------------
    # Workers
    # --------------------------------------------------------

    def _ensure_worker(self, address):
        """One worker per CAMERA, not per device — the pair share a stream."""
        if address in self._workers and self._workers[address].is_alive():
            return
        stop = threading.Event()
        worker = CameraWorker(address, self.cam_user, self.cam_pass,
                              self._events, stop,
                              status_cb=lambda a, s, d: self._statuses.put((a, s, d)))
        self._stops[address]   = stop
        self._workers[address] = worker
        worker.start()

    def _stop_worker(self, address):
        stop = self._stops.pop(address, None)
        worker = self._workers.pop(address, None)
        if stop:
            stop.set()
        if worker:
            worker.stop()
            worker.join(timeout=3)

    # --------------------------------------------------------
    # The drain — the ONLY place a device state is written
    # --------------------------------------------------------

    def runConcurrentThread(self):
        try:
            while True:
                self._drain_once()
        except self.StopThread:
            pass

    def _drain_once(self):
        """One tick. The WHOLE body is wrapped: a surprise in one event must cost
        that event, not the loop that drives every camera in the house."""
        try:
            self._drain_statuses()
            self._drain_events()
            self._expire_holds()
        except self.StopThread:
            raise                      # never swallow the stop
        except Exception:
            self.logger.exception("event drain failed; continuing")
            self.sleep(DRAIN_TICK)     # do not spin on a repeating fault

    def _drain_statuses(self):
        while True:
            try:
                address, status, detail = self._statuses.get_nowait()
            except queue.Empty:
                return
            for dev_id in self._by_camera.get(address, {}).values():
                dev = indigo.devices.get(dev_id)
                if dev is None:
                    continue
                dev.updateStateOnServer("streamState", status)
                if status == "unsupported":
                    dev.setErrorStateOnServer(detail or "camera cannot emit smart detections")
                elif status == "connected":
                    dev.setErrorStateOnServer("")
            if detail:
                level = self.logger.warning if status != "connected" else self.logger.info
                level(f"{address}: {status} — {detail}")

    def _drain_events(self):
        """Block briefly on the queue so an idle plugin costs nothing, then take
        whatever else is already waiting."""
        try:
            item = self._events.get(timeout=DRAIN_TICK)
        except queue.Empty:
            return
        now = time.monotonic()
        self._apply(item, now)
        while True:
            try:
                self._apply(self._events.get_nowait(), now)
            except queue.Empty:
                return

    def _apply(self, item, now):
        address, event = item
        klass = next((k for k, code in CLASS_CODES.items() if code == event.code), None)
        if klass is None:
            return
        dev_id = self._by_camera.get(address, {}).get(klass)
        if dev_id is None:
            return
        timer = self._timers.get(dev_id)
        dev = indigo.devices.get(dev_id)
        if timer is None or dev is None:
            return

        changed = timer.start(now) if event.action == "Start" else timer.stop(now)
        if event.action == "Start":
            dev.updateStateOnServer("lastDetection",
                                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self._bump_count(dev)
        if changed:
            self._write_on_off(dev, timer.is_on)

    def _expire_holds(self):
        now = time.monotonic()
        for dev_id, timer in list(self._timers.items()):
            if timer.tick(now):
                dev = indigo.devices.get(dev_id)
                if dev is not None:
                    self._write_on_off(dev, timer.is_on)

    def _write_on_off(self, dev, on):
        dev.updateStateOnServer("onOffState", on)
        self.logger.info(f"{dev.name}: {'DETECTED' if on else 'clear'}")

    @staticmethod
    def _bump_count(dev):
        """Detections today, resetting at local midnight. Derived from lastDetection
        rather than a timer, so it is correct after a restart at any hour."""
        today = datetime.now().strftime("%Y-%m-%d")
        stamp = dev.states.get("lastDetectionDay", "")
        count = dev.states.get("detectionsToday", 0) or 0
        if stamp != today:
            count = 0
        dev.updateStateOnServer("detectionsToday", count + 1)
        dev.updateStateOnServer("lastDetectionDay", today)

    # --------------------------------------------------------
    # Device factory
    # --------------------------------------------------------

    def getDeviceFactoryUiValues(self, devIdList):
        values, errors = indigo.Dict(), indigo.Dict()
        for dev_id in devIdList:
            dev = indigo.devices.get(dev_id)
            if dev is not None and dev.pluginProps.get("address"):
                values["address"] = dev.pluginProps["address"]
                values["cameraName"] = dev.name.rsplit(" ", 1)[0]
                break
        return (values, errors)

    def validateDeviceFactoryUi(self, valuesDict, devIdList):
        errors = indigo.Dict()
        if not valuesDict.get("address", "").strip():
            errors["address"] = "Enter the camera's IP address or hostname."
        if not valuesDict.get("cameraName", "").strip():
            errors["cameraName"] = "Give the camera a name, e.g. Drive."
        return (not bool(errors), valuesDict, errors)

    def closedDeviceFactoryUi(self, valuesDict, userCancelled, devIdList):
        if userCancelled:
            return
        address = valuesDict.get("address", "").strip()
        name    = valuesDict.get("cameraName", "").strip()
        hold    = str(valuesDict.get("holdOverride", "")).strip()

        existing = {indigo.devices[d].pluginProps.get("detectionClass")
                    for d in devIdList if d in indigo.devices}
        for klass, label in CLASS_LABELS.items():
            if klass in existing:
                continue
            try:
                dev = indigo.device.create(
                    indigo.kProtocol.Plugin,
                    name=f"{name} {label}",
                    deviceTypeId=DEVICE_TYPE,
                    props={"address": address, "detectionClass": klass,
                           "holdOverride": hold,
                           "SupportsOnState": True, "SupportsSensorValue": False})
                dev.model   = MODEL_NAME
                dev.subType = label
                dev.replaceOnServer()
                self.logger.info(f"Created {dev.name} for {address}")
            except Exception:
                self.logger.exception(f"could not create the {label} device for {address}")

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
        """Probe one camera and log the verdict. Returns the verdict string.

        Wraps the WHOLE body, not a fraction of it: in a sweep of seven cameras one
        unexpected throw must cost that camera and nothing else. probe() already
        swallows network errors, so anything reaching here is a genuine surprise and
        is worth a stack trace — but it still must not take the other six with it.
        """
        try:
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
        except Exception:
            self.logger.exception(f"probing {address} failed unexpectedly")
            return dahua_probe.UNREACHABLE

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
        try:
            address = (valuesDict or {}).get("address", "").strip()
        except Exception:
            self.logger.exception("could not read the address from the dialog")
            return False
        if not address:
            self.logger.error("No camera address given.")
            return False
        self._probe_and_log(address)
        return True

    def testConnection(self, valuesDict=None, typeId=None):
        """Probe every camera this plugin knows about.

        THREADED, and it has to be. Indigo's UI callbacks have a ~30 second hard
        timeout, after which the client shows "Communication with the plugin timed
        out" and the dialog is left broken. Seven cameras at three HTTP calls each
        with an 8s timeout is up to 168 seconds — two unreachable cameras is enough
        to blow the budget. Return at once, report progress to the event log.
        """
        if log_startup_banner:
            log_startup_banner(self.pluginId, self.pluginDisplayName, self.pluginVersion,
                               extras=self._banner_extras())
        try:
            addresses = sorted({
                dev.pluginProps.get("address", "").strip()
                for dev in indigo.devices.iter("self")
                if dev.pluginProps.get("address", "").strip()
            })
        except Exception:
            self.logger.exception("could not read the camera list from the devices")
            return False

        if not addresses:
            # Say what was NOT checked. A sweep that covered nothing must never read
            # as a sweep that found nothing wrong.
            self.logger.warning(
                "No cameras configured yet, so nothing was tested. Add a camera, or use "
                "'Probe a Camera...' to check one by address.")
            return True

        threading.Thread(target=self._probe_sweep, args=(addresses,), daemon=True,
                         name=f"DahuaProbeSweep-{int(time.time())}").start()
        self.logger.info(f"Probing {len(addresses)} camera(s) in the background...")
        return True

    def _probe_sweep(self, addresses):
        counts = {}
        for address in addresses:
            verdict = self._probe_and_log(address)
            counts[verdict] = counts.get(verdict, 0) + 1
        self.logger.info("Probe complete: " +
                         ", ".join(f"{n} {v}" for v, n in sorted(counts.items())))
