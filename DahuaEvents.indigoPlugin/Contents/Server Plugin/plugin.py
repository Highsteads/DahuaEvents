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
#              1.17 adds a Doorbell button class (CallNoAnswered) for Dahua and
#              Amcrest video doorbells.
#              1.18 fixes a camera's second device never switching on: a running
#              stream is replaced when it does not ask for every device's code.
#              1.19 applies a new hold, username or password from Configure, and a
#              device's own hold override, on Save, and ships
#              IndigoSecrets_example.py.
# Author:      CliveS & Claude Opus 5.5
# Date:        27-09-2026 BST
#              1.19.1 stops a replaced stream's 'stopped' status reaching
#              streamState, which is not one of its values.
#              1.20 keeps a device's error state through routine state writes
#              (clearErrorState=False), so the midnight counter reset or a
#              stream status no longer wipes "no rule drawn on the camera",
#              and a blocked device returns to its own verdict when the stream
#              comes back, rather than staying on "reconnecting".
#              1.21 (05-10-2026) stops a device staying on when its Stop is lost:
#              a dropped or replaced stream gives every device of that camera
#              that is on the normal hold, and a new "Longest detection" setting
#              (10 minutes, 0 = off) clears one that hears no Stop. A second
#              device for the same camera and detection is refused, and stopping
#              one no longer removes another device's route.
# Version:     1.21
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
    from plugin_utils import as_bool, install_timestamp_filter, log_startup_banner
except ImportError:
    log_startup_banner = None
    install_timestamp_filter = None
    as_bool = None

import dahua_probe
from dahua_stream import HoldTimer, drain
from dahua_worker import CameraWorker
from dahua_worker import STOPPED as WORKER_STOPPED

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
PLUGIN_VERSION = "1.21"

DEFAULT_HOLD_SECONDS = 20

# The longest a detection may stay on with no Stop from the camera, in minutes.
# A Start whose Stop is lost (stream drop, camera reboot) would otherwise leave
# the device on until the next detection of that class, which on a quiet camera
# can be hours. 0 turns the limit off. Ten minutes is far longer than anything
# walking or driving through frame, and a doorbell call ends well inside it.
DEFAULT_MAX_DETECTION_MINUTES = 10
MAX_DETECTION_MINUTES_LIMIT   = 1440        # a day; anything longer is a typo

# Whether the per-detection narration ("Drive Person: DETECTED" / "clear") is
# echoed to Indigo's SHARED event log. Default OFF, and deliberately so.
#
# The event log is the whole estate's dashboard and every plugin writes to it. Two
# cameras on their own put 52-54 lines a day into it (measured 03-05 Sep 2026, and
# those were the ONLY DahuaEvents lines on each of those days): a pair per
# detection, saying exactly what the device's own onOffState already says, and
# growing with every camera added. The narration is not lost when this is off: it
# still goes to the plugin's own log at Logs/<bundle id>/plugin.log, because
# Indigo's event-log handler sits at INFO while the plugin's file handler sits at
# THREADDEBUG (plugin_base.py:274 and :300).
DEFAULT_LOG_ACTIVITY = False

# Detection classes, and the camera event code that drives each.
# Two families. SMD (2022-ish firmware onward) classifies on its own with nothing
# to configure. IVS is the older generation's answer and needs a tripwire or zone
# DRAWN on the camera before it emits anything at all — so its capability check is
# a different question, see _verdict_for().
CLASS_CODES = {
    "person":      "SmartMotionHuman",
    "vehicle":     "SmartMotionVehicle",
    "crossline":   "CrossLineDetection",
    "crossregion": "CrossRegionDetection",
    # A third family: a doorbell's button. Not a detection at all, but it arrives
    # on the same event stream with the same Start/Stop shape, so the hold logic
    # serves it unchanged. Capability is judged by device type, see
    # dahua_probe.assess_doorbell.
    "doorbell":    "CallNoAnswered",
}
CLASS_LABELS = {
    "person":      "Person",
    "vehicle":     "Vehicle",
    "crossline":   "Tripwire",
    "crossregion": "Intrusion",
    "doorbell":    "Pressed",          # so a camera named Doorbell gives "Doorbell Pressed"
}
IVS_CLASSES = ("crossline", "crossregion")

# A verdict that blocks a device -> the streamState and error it shows. Held per
# device in Plugin._blocked, so the stream coming back restores it.
BLOCKED_VERDICTS = {
    dahua_probe.NO_RULE:     ("noRule",      "no rule drawn on the camera"),
    dahua_probe.DISABLED:    ("disabled",    "switched off at the camera"),
    dahua_probe.UNSUPPORTED: ("unsupported", "camera cannot emit this detection"),
}
DEFAULT_CLASSES = ("person", "vehicle")

DEVICE_TYPE = "dahuaDetection"
MODEL_NAME  = "Dahua Camera"

# The drain tick. Short enough that a hold expires close to its moment, long
# enough that an idle plugin costs nothing measurable.
DRAIN_TICK = 0.5

# Total time shutdown will spend waiting for workers, however many cameras
# there are. Indigo force-kills a plugin that does not quit politely.
SHUTDOWN_BUDGET = 2.0


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


def worker_is_current(worker, codes):
    """True when a camera's running worker already asks for exactly these codes.

    A worker that has died, or that opened its stream for a different set of
    codes, must be replaced — its codes are fixed once the stream is open.
    """
    return (worker is not None and worker.is_alive()
            and set(getattr(worker, "codes", ())) == set(codes))


# ============================================================
# Plugin class
# ============================================================

class Plugin(indigo.PluginBase):

    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        super().__init__(pluginId, pluginDisplayName, pluginVersion, pluginPrefs)

        # Every CliveS plugin prefixes its log lines with [HH:MM:SS.mmm]. Without it
        # this plugin's lines are the only ones in the event log you cannot time,
        # which matters here more than most: the whole point of the hold is a
        # duration, and it cannot be read off an untimed line.
        if install_timestamp_filter:
            install_timestamp_filter(self)

        # Credentials resolve once here so every path uses the same answer.
        self.cam_user = DAHUA_USER or pluginPrefs.get("dahuaUser", "")
        self.cam_pass = DAHUA_PASS or pluginPrefs.get("dahuaPass", "")
        self.hold_seconds = self._hold_from_prefs(pluginPrefs)
        self.max_on_seconds = self._max_on_from_prefs(pluginPrefs)
        self.log_activity = self._log_activity_from_prefs(pluginPrefs)

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
        self._blocked  = {}                  # device id -> (streamState, error) of the
                                              # settled verdict while camera-side config
                                              # (no rule / disabled / unsupported) blocks
                                              # it, None when it does not
        self._counter_day = datetime.now().strftime("%Y-%m-%d")

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

    @staticmethod
    def _parse_max_minutes(raw):
        """The maximum detection time as whole minutes, or None if it is not one.
        Shared by the validator and the reader so they cannot disagree."""
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return None
        if value < 0 or value > MAX_DETECTION_MINUTES_LIMIT:
            return None
        return value

    def _max_on_from_prefs(self, prefs):
        """Maximum detection time in SECONDS (0 = no limit), coerced AND guarded.

        A never-saved install has no value and gets the default. Junk that got
        past the dialog (a hand-edited .indiPref) also gets the default, with a
        warning, rather than stopping the plugin loading.
        """
        raw = prefs.get("maxDetectionMinutes", DEFAULT_MAX_DETECTION_MINUTES)
        minutes = self._parse_max_minutes(raw)
        if minutes is None:
            self.logger.warning(
                f"maxDetectionMinutes is not a whole number from 0 to "
                f"{MAX_DETECTION_MINUTES_LIMIT} ({raw!r}) — using "
                f"{DEFAULT_MAX_DETECTION_MINUTES} minutes")
            minutes = DEFAULT_MAX_DETECTION_MINUTES
        return minutes * 60

    def validatePrefsConfigUi(self, valuesDict):
        """Refuse values that would otherwise be quietly replaced by a default."""
        errors = indigo.Dict()
        try:
            hold_ok = int(str(valuesDict.get("holdSeconds", DEFAULT_HOLD_SECONDS)).strip()) >= 0
        except (TypeError, ValueError):
            hold_ok = False
        if not hold_ok:
            errors["holdSeconds"] = "Enter a whole number of seconds, 0 or more."
        if self._parse_max_minutes(valuesDict.get("maxDetectionMinutes", "")) is None:
            errors["maxDetectionMinutes"] = (
                f"Enter a whole number of minutes from 0 to {MAX_DETECTION_MINUTES_LIMIT}. "
                f"0 means no limit.")
        return (not bool(errors), valuesDict, errors)

    def _log_activity_from_prefs(self, prefs):
        """Whether detections are echoed to the shared event log. Quiet by default.

        A checkbox round-trips as a real bool, but a value that has never been saved
        arrives absent and a hand-edited .indiPref can hold text, and bool("false")
        is True, which is the wrong answer in the direction that fills the log.
        as_bool() returns the DEFAULT for anything it does not recognise, so junk in
        the pref leaves the plugin quiet rather than loud. Without plugin_utils only
        a literal True counts, which errs the same way.
        """
        raw = prefs.get("logActivityToEventLog", DEFAULT_LOG_ACTIVITY)
        if as_bool is None:
            return raw is True
        return as_bool(raw, DEFAULT_LOG_ACTIVITY)

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
        """Signal everything FIRST, then join against ONE shared budget.

        Stopping cameras one at a time meant up to 3s of join each, so five cameras
        could hold shutdown for 15 seconds — past Indigo's patience, which is why an
        upgrade force-killed the process. Signalling first lets every worker wind
        down in parallel; the joins are then a formality. They are daemon threads, so
        a straggler dies with the process and is not worth waiting for.
        """
        for stop in list(self._stops.values()):
            stop.set()
        for worker in list(self._workers.values()):
            worker.stop()                 # closes the socket, unblocking a pending read

        deadline = time.monotonic() + SHUTDOWN_BUDGET
        for worker in list(self._workers.values()):
            worker.join(timeout=max(0.0, deadline - time.monotonic()))

        still_running = [w.name for w in self._workers.values() if w.is_alive()]
        self._workers.clear()
        self._stops.clear()
        if still_running:
            # Say it rather than leaving a silent delay for someone to wonder about.
            self.logger.debug(f"{len(still_running)} worker(s) still winding down; "
                              f"they are daemon threads and will not hold anything up")

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        """Mirror the startup guards, then put the new values to work at once.

        Until 1.19 this only updated the attributes. Every device's HoldTimer kept
        the hold it was built with, and every camera's worker kept the username and
        password it was started with, so a change made in Configure did nothing
        until the plugin restarted.
        """
        if userCancelled:
            return
        old_creds = (self.cam_user, self.cam_pass)
        old_hold  = self.hold_seconds
        old_max   = self.max_on_seconds
        self.cam_user = DAHUA_USER or valuesDict.get("dahuaUser", "")
        self.cam_pass = DAHUA_PASS or valuesDict.get("dahuaPass", "")
        self.hold_seconds = self._hold_from_prefs(valuesDict)
        self.max_on_seconds = self._max_on_from_prefs(valuesDict)
        self.log_activity = self._log_activity_from_prefs(valuesDict)

        if self.hold_seconds != old_hold:
            self._apply_hold_to_timers()
        if self.max_on_seconds != old_max:
            for timer in self._timers.values():
                timer.max_on_seconds = self.max_on_seconds
        if (self.cam_user, self.cam_pass) != old_creds:
            self._restart_workers_with_new_credentials()

    def _apply_hold_to_timers(self):
        """Give every running device the hold it should now have.

        _hold_for() is the same rule deviceStartComm uses, so a device with its own
        override keeps it and every other device takes the new plugin default. A
        hold already counting down finishes on the old value; the next one uses the
        new.
        """
        changed = 0
        for dev_id, timer in list(self._timers.items()):
            dev = indigo.devices.get(dev_id)
            if dev is None:
                continue
            hold = self._hold_for(dev)
            if timer.hold_seconds != hold:
                timer.hold_seconds = hold
                changed += 1
        if changed:
            self.logger.info(f"Detection hold is now {self.hold_seconds}s on "
                             f"{changed} device(s)")

    def _restart_workers_with_new_credentials(self):
        """Reconnect every camera with the new username and password.

        A worker is handed its credentials when it starts, so the only way to change
        them is a new worker. Stops go through _stop_all_workers, which signals all
        of them first and then waits on one shared budget, because this runs inside
        a dialog callback and Indigo gives those about 30 seconds.

        Each device is then settled again, since a verdict reached with the old
        credentials may have been "could not tell" when the camera simply refused
        the login.
        """
        addresses = list(self._by_camera)
        if not addresses:
            return
        self._stop_all_workers()
        for address in addresses:
            self._ensure_worker(address)
            for klass, dev_id in list(self._by_camera.get(address, {}).items()):
                threading.Thread(target=self._settle_device, args=(dev_id, address, klass),
                                 daemon=True, name=f"DahuaVerdict-{dev_id}").start()
        self.logger.info(f"Camera username or password changed, reconnecting "
                         f"{len(addresses)} camera(s)")

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

        # One device per camera and class. Validation refuses a second one now,
        # but one made before 1.21 (or by script) would silently take the route
        # from the first; it is put in error instead, naming the device it copies.
        owner_id = self._by_camera.get(address, {}).get(klass)
        if owner_id is not None and owner_id != dev.id and owner_id in self._timers:
            owner = indigo.devices.get(owner_id)
            owner_name = owner.name if owner is not None else str(owner_id)
            self._mark_error(dev, f"duplicate of {owner_name}")
            self.logger.error(f"{dev.name} watches the same camera and detection as "
                              f"{owner_name}, so it has been left switched off. "
                              f"Delete one of them.")
            return

        self._timers[dev.id] = HoldTimer(self._hold_for(dev), self.max_on_seconds)
        self._by_camera.setdefault(address, {})[klass] = dev.id
        dev.updateStateOnServer("onOffState", False, clearErrorState=False)
        # A (re)start begins from a clean slate: _settle_device below decides the
        # device's fault afresh. This is the one deliberate clear outside a
        # verdict — every state write in this file passes clearErrorState=False,
        # because Indigo's default wipes the error on ANY write (see _write_on_off).
        dev.setErrorStateOnServer("")

        # Per-device verdict, so one class failing does not condemn the others on
        # the same camera. Threaded: this is device startup, not a UI callback, but
        # it still makes HTTP calls and Indigo starts every device in turn.
        threading.Thread(target=self._settle_device, args=(dev.id, address, klass),
                         daemon=True, name=f"DahuaVerdict-{dev.id}").start()
        self._ensure_worker(address)
        self.logger.debug(f"deviceStartComm: {dev.name} ({address}, {klass})")

    def _settle_device(self, dev_id, address, klass):
        """Decide and show what this one device can actually do."""
        try:
            verdict, reason = self._verdict_for(address, klass)
            dev = indigo.devices.get(dev_id)
            if dev is None:
                return
            # BLOCKED means the CAMERA has told us this class cannot fire until
            # something changes there — no rule drawn, switched off, or the
            # firmware doesn't support it. That is a fact about configuration,
            # not about whether the event STREAM happens to be reachable right
            # now, so _drain_statuses must not let a worker-level "connected"
            # (the stream merely being open) paint a blocked device healthy —
            # see the comment there.
            # The pair is remembered so _drain_statuses can put it back when the
            # stream returns (1.20), rather than leave the device on "reconnecting".
            blocked = BLOCKED_VERDICTS.get(verdict)
            self._blocked[dev_id] = blocked
            if verdict == dahua_probe.CAPABLE:
                dev.updateStateOnServer("streamState", "connected", clearErrorState=False)
                dev.setErrorStateOnServer("")
            elif verdict == dahua_probe.NO_RULE:
                dev.updateStateOnServer("streamState", blocked[0], clearErrorState=False)
                dev.setErrorStateOnServer(blocked[1])
                self.logger.warning(f"{dev.name}: {reason}")
            elif verdict == dahua_probe.DISABLED:
                dev.updateStateOnServer("streamState", blocked[0], clearErrorState=False)
                dev.setErrorStateOnServer(blocked[1])
                self.logger.warning(f"{dev.name}: {reason}")
            elif verdict == dahua_probe.UNSUPPORTED:
                dev.updateStateOnServer("streamState", blocked[0], clearErrorState=False)
                dev.setErrorStateOnServer(blocked[1])
                self.logger.warning(f"{dev.name}: {reason}")
            else:
                dev.updateStateOnServer("streamState", "reconnecting", clearErrorState=False)
                self.logger.error(f"{dev.name}: {reason}")
        except Exception:
            self.logger.exception(f"could not settle device {dev_id}")

    def deviceStopComm(self, dev):
        address = dev.pluginProps.get("address", "").strip()
        klass   = dev.pluginProps.get("detectionClass", "person")
        self._timers.pop(dev.id, None)
        self._blocked.pop(dev.id, None)
        if address in self._by_camera:
            # Only this device's own route. A duplicate that never took the route
            # must not remove the device that holds it (1.21).
            if self._by_camera[address].get(klass) == dev.id:
                del self._by_camera[address][klass]
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
        return self._hold_from_override(dev.pluginProps.get("holdOverride", ""), dev.name)

    def _hold_from_override(self, raw, name):
        """The rule behind _hold_for(), on a raw override value. Shared with
        closedDeviceConfigUi, which has the dialog's values in hand."""
        if str(raw).strip() == "":
            return self.hold_seconds
        try:
            value = int(raw)
        except (TypeError, ValueError):
            self.logger.warning(
                f"{name}: hold override {raw!r} is not a number — "
                f"using the plugin default of {self.hold_seconds}s")
            return self.hold_seconds
        return max(0, value)

    def closedDeviceConfigUi(self, valuesDict, userCancelled, typeId, devId):
        """Apply a device's new Hold override on Save.

        didDeviceCommPropertyChange deliberately does not restart a device for a
        hold change, so until 1.19 nothing else picked the new value up and it only
        took effect at the next plugin restart. The value is read from the dialog
        rather than the device, so it does not matter whether Indigo has stored the
        new props yet. A device that is not running has no timer; it gets the right
        hold from deviceStartComm when it starts.
        """
        if userCancelled:
            return
        timer = self._timers.get(devId)
        if timer is None:
            return
        dev = indigo.devices.get(devId)
        name = dev.name if dev is not None else str(devId)
        hold = self._hold_from_override(valuesDict.get("holdOverride", ""), name)
        if timer.hold_seconds != hold:
            timer.hold_seconds = hold
            self.logger.info(f"{name}: detection hold is now {hold}s")

    @staticmethod
    def _norm_address(address):
        """An address as compared for duplicates: no spaces, any case."""
        return str(address or "").strip().lower()

    def _route_owner(self, address, klass, exclude_ids=()):
        """The other device of this plugin already watching this camera and class,
        or None."""
        wanted = self._norm_address(address)
        for dev in indigo.devices.iter("self"):
            if dev.id in exclude_ids:
                continue
            props = dev.pluginProps
            if (self._norm_address(props.get("address", "")) == wanted
                    and props.get("detectionClass", "person") == klass):
                return dev
        return None

    def validateDeviceConfigUi(self, valuesDict, typeId, devId):
        """One device per camera and class: two would share a single route, and
        whichever started last would take every detection from the other."""
        errors = indigo.Dict()
        address = str(valuesDict.get("address", "")).strip()
        klass = valuesDict.get("detectionClass", "person")
        if not address:
            errors["address"] = "Enter the camera's IP address or hostname."
        else:
            owner = self._route_owner(address, klass, exclude_ids=(devId,))
            if owner is not None:
                errors["detectionClass"] = (
                    f"{owner.name} already watches this camera for "
                    f"{CLASS_LABELS.get(klass, klass)}. Use that device, or delete it first.")
        return (not bool(errors), valuesDict, errors)

    def _verdict_for(self, address, klass):
        """Ask the right question for this class.

        SMD asks 'can the firmware do it and is it switched on'. IVS asks that AND
        'is a rule actually drawn', because an IVS camera with no rule advertises
        the event and never fires — healthy-looking and permanently silent, which
        is the exact failure this plugin was built to refuse.
        """
        if klass in IVS_CLASSES:
            return dahua_probe.probe_ivs(address, self.cam_user, self.cam_pass, klass)
        if klass == "doorbell":
            return dahua_probe.probe_doorbell(address, self.cam_user, self.cam_pass)
        return dahua_probe.probe(address, self.cam_user, self.cam_pass)

    def _codes_for_camera(self, address):
        """The union of event codes the devices on this camera actually need."""
        return {CLASS_CODES[k] for k in self._by_camera.get(address, {})
                if k in CLASS_CODES} or set(dahua_probe.SMART_CODES)

    def _mark_error(self, dev, reason):
        try:
            dev.updateStateOnServer("streamState", "unsupported", clearErrorState=False)
            dev.setErrorStateOnServer(reason)
        except Exception:
            self.logger.exception(f"could not mark {dev.name} in error")

    # --------------------------------------------------------
    # Workers
    # --------------------------------------------------------

    def _ensure_worker(self, address):
        """One worker per CAMERA, not per device — the pair share a stream.

        The stream asks the camera for a fixed list of event codes, chosen when it
        opens. Devices start one at a time, so the camera's first device opens the
        stream for its code alone, and until 1.18 a second device found it running
        and returned — Vehicle never got its code, and never once switched on in 26
        days while Person on the same camera switched 1,105 times. So a running
        worker is kept only when it already asks for every code needed now.
        """
        codes = self._codes_for_camera(address)
        if worker_is_current(self._workers.get(address), codes):
            return
        if address in self._workers:
            self._stop_worker(address)
        stop = threading.Event()
        worker = CameraWorker(address, self.cam_user, self.cam_pass,
                              self._events, stop,
                              status_cb=lambda a, s, d: self._statuses.put((a, s, d)),
                              codes=codes)
        self._stops[address]   = stop
        self._workers[address] = worker
        worker.start()

    def _stop_worker(self, address):
        """Stop one camera's worker.

        Called from deviceStopComm, which Indigo also runs for every device while
        shutting the plugin down — so this sits on the same critical path as the
        polite-quit deadline. It is a daemon thread; waiting three seconds for it
        bought nothing and risked everything.
        """
        stop = self._stops.pop(address, None)
        worker = self._workers.pop(address, None)
        if stop:
            stop.set()
        if worker:
            worker.stop()
            worker.join(timeout=SHUTDOWN_BUDGET)

    # --------------------------------------------------------
    # Sensor actions
    # --------------------------------------------------------

    def actionControlSensor(self, action, dev):
        """Declaring type="sensor" obliges this method.

        Without it Indigo logs `plugin does not define method actionControlSensor`
        and silently drops the action — so a script calling indigo.device.turnOn()
        on one of these, or a user pressing the button in the UI, gets nothing and
        no explanation. These devices are read-only: what they report is what the
        camera saw, and pretending otherwise would put a state on screen that no
        camera ever produced.
        """
        if action.sensorAction == indigo.kSensorAction.RequestStatus:
            address = dev.pluginProps.get("address", "").strip()
            klass = dev.pluginProps.get("detectionClass", "person")
            self.logger.info(f"{dev.name}: re-checking the camera...")
            threading.Thread(target=self._settle_device, args=(dev.id, address, klass),
                             daemon=True, name=f"DahuaVerdict-{dev.id}").start()
        elif action.sensorAction in (indigo.kSensorAction.TurnOn,
                                     indigo.kSensorAction.TurnOff,
                                     indigo.kSensorAction.Toggle):
            self.logger.warning(
                f"{dev.name} is read-only — it reports what the camera sees and "
                f"cannot be switched by hand.")
        else:
            # An unhandled action that logs nothing is indistinguishable from one
            # that was never dispatched, and the two have very different causes.
            self.logger.warning(f"{dev.name}: unhandled sensor action "
                                f"{action.sensorAction!r} — ignored")

    # --------------------------------------------------------
    # The drain — the ONLY place a device state is written
    # --------------------------------------------------------

    def runConcurrentThread(self):
        try:
            while True:
                # self.StopThread is raised ONLY from inside self.sleep() — that is
                # the whole mechanism (plugin_base.py:491). This loop does its own
                # waiting on the queue, so without this call it would never learn to
                # stop, and Indigo would force-kill the process on every upgrade.
                # sleep(0) returns instantly when running and raises when stopping.
                self.sleep(0)
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
            self._roll_day_if_needed()
        except self.StopThread:
            raise                      # never swallow the stop
        except Exception:
            self.logger.exception("event drain failed; continuing")
            self.sleep(DRAIN_TICK)     # do not spin on a repeating fault

    def _drain_statuses(self):
        statuses, overflowed = drain(self._statuses)
        for address, status, detail in statuses:
            if status != "connected":
                # The stream has dropped, failed or been replaced, so a Stop that
                # was on its way may never arrive. Every device of this camera that
                # is on gets the normal hold, as if it had (1.21). "connected" is
                # left out: the worker re-affirms it every few minutes while
                # healthy, and a fresh connection carries no news about old events.
                self._stream_lost(address)
            if status == WORKER_STOPPED:
                # A worker we stopped on purpose. Since 1.18 that happens while
                # the plugin runs (a camera's stream is replaced when a second
                # device needs another event code), and "stopped" is not one of
                # the streamState values, so writing it logged an error per
                # device. The replacement worker reports its own status.
                continue
            for dev_id in self._by_camera.get(address, {}).values():
                dev = indigo.devices.get(dev_id)
                if dev is None:
                    continue
                blocked = self._blocked.get(dev_id)
                if status == "connected" and blocked:
                    # This device's own settled verdict — no rule drawn,
                    # switched off, or unsupported — is a camera-CONFIG fact.
                    # The shared per-camera worker reconnecting says only that
                    # the event STREAM is reachable, which is a different axis
                    # and has no bearing on it. Painting the device "connected"
                    # here is exactly the failure this plugin exists to catch:
                    # a camera that looks perfectly healthy and will never
                    # fire. So the verdict itself goes back (1.20): until then
                    # the connected status was skipped, and a device that had
                    # heard "reconnecting" during a blip stayed on it after the
                    # stream came back. A fresh Send Status Request (or a device
                    # restart) is what re-settles the verdict, not the stream.
                    dev.updateStateOnServer("streamState", blocked[0],
                                            clearErrorState=False)
                    dev.setErrorStateOnServer(blocked[1])
                    continue
                dev.updateStateOnServer("streamState", status, clearErrorState=False)
                if status == "unsupported":
                    dev.setErrorStateOnServer(
                    dahua_probe.ascii_only(detail) or "camera cannot emit smart detections")
                elif status == "connected":
                    dev.setErrorStateOnServer("")
            if detail:
                # Written out as two direct calls rather than the one-line ternary
                # this used to be (level = self.logger.warning if ... else ...;
                # level(line)). The suite's structural guards match on a literal
                # self.logger.<level>(...) call, so a bound-method alias made this
                # WARNING invisible to them: it was not counted by the "no fault
                # line was lost" guard, and could have been demoted to info with the
                # whole suite still green. test_no_logger_method_is_reached_through
                # _an_alias now forbids the alias so it cannot come back.
                line = f"{address}: {status} — {detail}"
                if status == "connected":
                    self.logger.info(line)
                else:
                    self.logger.warning(line)
        if overflowed:
            self.logger.warning("status queue is running behind — a camera is flapping")

    def _drain_events(self):
        """Block briefly so an idle plugin costs nothing, then take what is waiting.

        BOUNDED. Draining until empty has no upper limit, and a camera producing
        events faster than they are consumed would mean _expire_holds() never runs
        — leaving every device stuck on, including the cameras behaving perfectly.
        Anything past the cap waits for the next tick half a second later.
        """
        try:
            first = self._events.get(timeout=DRAIN_TICK)
        except queue.Empty:
            return
        now = time.monotonic()
        self._apply(first, now)
        items, overflowed = drain(self._events)
        for item in items:
            self._apply(item, now)
        if overflowed:
            self.logger.warning(
                "event queue is running behind — a camera is producing detections "
                "faster than they can be applied")

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
                                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    clearErrorState=False)
            self._bump_count(dev)
        if changed:
            self._write_on_off(dev, timer.is_on)

    def _stream_lost(self, address):
        """Arm the trailing hold on every device of this camera that is on."""
        now = time.monotonic()
        for dev_id in list(self._by_camera.get(address, {}).values()):
            timer = self._timers.get(dev_id)
            if timer is not None and timer.stream_lost(now):
                dev = indigo.devices.get(dev_id)        # only with a hold of 0
                if dev is not None:
                    self._write_on_off(dev, timer.is_on)

    def _expire_holds(self):
        now = time.monotonic()
        for dev_id, timer in list(self._timers.items()):
            if timer.tick(now):
                dev = indigo.devices.get(dev_id)
                if dev is not None:
                    self._write_on_off(dev, timer.is_on)
                    if timer.cleared_by_cap:
                        minutes = timer.max_on_seconds // 60
                        self.logger.info(
                            f"{dev.name}: no stop arrived from the camera "
                            f"{minutes} minute{'' if minutes == 1 else 's'} after the "
                            f"last detection, so it has been cleared")

    def _roll_day_if_needed(self):
        """Zero the daily counters when the date changes.

        Resetting only inside _bump_count meant the count reset on the next
        DETECTION rather than at midnight — so a device that saw twelve yesterday
        read twelve all morning until something walked past. A number that reports
        a different day from the one it claims is worse than no number.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        if today == self._counter_day:
            return
        self._counter_day = today
        for dev_id in list(self._timers):
            dev = indigo.devices.get(dev_id)
            if dev is None:
                continue
            if dev.states.get("lastDetectionDay", "") != today:
                dev.updateStateOnServer("detectionsToday", 0, clearErrorState=False)

    def _write_on_off(self, dev, on):
        """Write the device state, and narrate it to the plugin's OWN log.

        This line used to go to Indigo's shared event log at INFO, and it was the
        only thing this plugin put there once a camera was running: 52, 54 and 52
        lines on 03, 04 and 05 Sep 2026, a DETECTED/clear pair per person walking
        past, and rising with every camera added. It said nothing the device's own
        onOffState did not already say, and the estate's event log was carrying
        about 2,031 lines a day between all the plugins.

        At DEBUG it still reaches Logs/<bundle id>/plugin.log in full, because
        Indigo's event-log handler is set to INFO while the plugin's own file
        handler is set to THREADDEBUG. Nothing is lost. It just stops filling a
        shared log by default, and anyone who wants the running commentary back
        ticks "Log detections to the Indigo event log" in Configure.

        Every state write in this plugin passes clearErrorState=False (1.20).
        Indigo's default CLEARS the device's error state on any write, so the
        midnight detectionsToday reset, a detection, or a stream status used to
        wipe "no rule drawn on the camera" and its like, and anything reading
        errorState (Device Health Monitor) saw a healthy device. An error now
        changes only where the plugin decides it: setErrorStateOnServer().

        The message is identical either way, so the plugin's own log reads the same
        whichever way the checkbox is set.
        """
        dev.updateStateOnServer("onOffState", on, clearErrorState=False)
        line = f"{dev.name}: {'DETECTED' if on else 'clear'}"
        if self.log_activity:
            self.logger.info(line)
        else:
            self.logger.debug(line)

    @staticmethod
    def _bump_count(dev):
        """Detections today, resetting at local midnight. Derived from lastDetection
        rather than a timer, so it is correct after a restart at any hour."""
        today = datetime.now().strftime("%Y-%m-%d")
        stamp = dev.states.get("lastDetectionDay", "")
        count = dev.states.get("detectionsToday", 0) or 0
        if stamp != today:
            count = 0
        dev.updateStateOnServer("detectionsToday", count + 1, clearErrorState=False)
        dev.updateStateOnServer("lastDetectionDay", today, clearErrorState=False)

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

    def checkCamera(self, valuesDict, *args):
        """Ask the camera what it can do, and tick the boxes for you.

        Signature is deliberately tolerant: Indigo passes a device ConfigUI button
        (valuesDict, typeId, devId) and a device-factory one (valuesDict, devIdList),
        and a mismatch here is a TypeError inside a dialog the user cannot then use.

        Runs inline rather than threaded, unusually — a dialog cannot show a result
        it has not waited for. That is only safe because capabilities() fetches each
        document once and caps its timeouts: measured 0.24s on a live camera and
        4.0s on an address with nothing at it, against Indigo's ~30s limit.
        """
        address = (valuesDict.get("address") or "").strip()
        if not address:
            valuesDict["capabilitySummary"] = "Enter the camera's address first."
            return valuesDict

        caps = dahua_probe.capabilities(address, self.cam_user, self.cam_pass)
        valuesDict["capabilitySummary"] = dahua_probe.summarise(caps)

        # Tick what works, and leave what does not — but never silently UNTICK
        # something the user chose on purpose, because a camera can gain a
        # capability later and they may be setting it up ahead of time.
        for klass, (verdict, _) in caps.items():
            if verdict == dahua_probe.CAPABLE:
                valuesDict[f"want_{klass}"] = True

        for klass, (verdict, reason) in sorted(caps.items()):
            self.logger.info(f"{address} {CLASS_LABELS.get(klass, klass)}: {reason}")
        return valuesDict

    def validateDeviceFactoryUi(self, valuesDict, devIdList):
        errors = indigo.Dict()
        if not valuesDict.get("address", "").strip():
            errors["address"] = "Enter the camera's IP address or hostname."
        if not valuesDict.get("cameraName", "").strip():
            errors["cameraName"] = "Give the camera a name, e.g. Drive."
        address = valuesDict.get("address", "").strip()
        if address and "address" not in errors:
            wanted = [k for k in CLASS_LABELS
                      if valuesDict.get(f"want_{k}", k in DEFAULT_CLASSES)]
            taken = [self._route_owner(address, k, exclude_ids=tuple(devIdList))
                     for k in wanted]
            taken = [d for d in taken if d is not None]
            if taken:
                names = ", ".join(d.name for d in taken)
                errors["address"] = (f"This camera is already set up as {names}. "
                                     f"Untick that detection, or delete the existing device.")
        return (not bool(errors), valuesDict, errors)

    def closedDeviceFactoryUi(self, valuesDict, userCancelled, devIdList):
        if userCancelled:
            return
        address = dahua_probe.ascii_only(valuesDict.get("address", "")).strip()
        name    = dahua_probe.ascii_only(valuesDict.get("cameraName", "")).strip()
        hold    = dahua_probe.ascii_only(valuesDict.get("holdOverride", "")).strip()

        # The capability summary is a DISPLAY field. It must not travel any further:
        # Indigo persists the factory's dialog values, and a runtime string reaching
        # its XML layer is how device creation failed with
        # "illegal character in XML tag name or value", naming neither field nor
        # character. Clearing it also means the next dialog starts blank rather than
        # showing a verdict for the previous camera.
        valuesDict["capabilitySummary"] = ""

        existing = {indigo.devices[d].pluginProps.get("detectionClass")
                    for d in devIdList if d in indigo.devices}
        wanted = [k for k in CLASS_LABELS
                  if valuesDict.get(f"want_{k}", k in DEFAULT_CLASSES)]
        for klass in wanted:
            label = CLASS_LABELS[klass]
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
            ("Max on:",      f"{self.max_on_seconds // 60} min" if self.max_on_seconds
                             else "no limit"),
            ("Cameras:",     str(len(self._by_camera))),
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
