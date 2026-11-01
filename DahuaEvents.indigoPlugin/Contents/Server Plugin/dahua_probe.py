#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    dahua_probe.py
# Description: Capability probing for Dahua cameras — decides whether a camera can
#              actually emit SmartMotionHuman / SmartMotionVehicle events.
#
#              The whole point of this module is that a camera's CONFIG CANNOT BE
#              TRUSTED. A camera will accept SmartMotionDetect[0].Enable=true, return
#              OK, and read it back as true, while being entirely incapable of emitting
#              the events (measured 01-09-2026 on a 2.840...25.R camera). The only
#              honest test is the advertised event list, which the firmware cannot fake.
#
#              Everything above the fetch_* functions is PURE — text in, verdict out,
#              no sockets, no clock, no indigo import — so it runs under test with no
#              hardware. The network layer is a thin shell at the bottom.
# Author:      CliveS & Claude Sonnet 5
# Date:        01-11-2026
# Version:     1.4
#
# v1.4 (01-11-2026): the checks can be asked about ONE channel of an NVR
# (channel_index, 0-based like the camera's own tables and event lines). They
# used to read only the [0] entries, so channel 3's device was judged on
# channel 1's settings, and an IVS rule drawn on any channel counted for all.
# channel_index=None keeps the old behaviour exactly.
#
# v1.3 (01-11-2026): a wrong password is sent ONCE per request, not six times.
# urllib's HTTPDigestAuthHandler retries a rejected login on its own until its
# counter passes 5, so every request with a wrong password cost the camera six
# failed logins, which is enough to lock a Dahua account. _OneTryDigestAuthHandler
# stops after the first refusal. Shared with dahua_worker via make_opener().
#
# Also v1.3: one refusal is remembered per camera, so a camera that refuses the
# login is not asked again with the same details for LOGIN_RETRY (30 minutes).
# Without it, a plugin start sent the wrong password once for the stream and once
# more for every device's capability check, all at the same moment. Until a camera
# has accepted the current details, its requests go one at a time, so the first
# refusal stops the rest. Only a hash of the details is kept, never the password.

import contextlib
import hashlib
import re
import threading
import time
import urllib.error
import urllib.request

# ============================================================
# Constants
# ============================================================

SMART_CODES = ("SmartMotionHuman", "SmartMotionVehicle")

# The older generation cannot do SMD but does offer IVS rules — a tripwire
# (CrossLineDetection) or an intrusion zone (CrossRegionDetection), both of which
# can be filtered to people or vehicles. The catch is that IVS only emits anything
# once a rule has been DRAWN on the camera, which the advertised event list does
# not tell you. Treating "firmware supports it" as "it works" would recreate
# exactly the trap this module exists to avoid, one generation down.
IVS_CODES = {
    "crossline":   "CrossLineDetection",
    "crossregion": "CrossRegionDetection",
}
NO_RULE      = "no_rule"        # firmware can, but nothing is configured to fire

# Doorbells report a button press as CallNoAnswered: Start when the button is
# pressed, Stop when the call times out unanswered. The catch is that at least the
# Amcrest AD110 (firmware 1.000.00AC009, 2022) does NOT list that code in
# getExposureEvents while sending it all the same — measured 22-09-2026 on a real
# press: absent from the list, delivered on the stream. So for this class the
# advertised list can only say yes, never no, and the device type has to decide.
DOORBELL_CODE = "CallNoAnswered"
DOORBELL_TYPE_PREFIXES = ("AD1", "AD4", "AD5", "DB", "VTO", "DHI-VTO", "DH-VTO")

# Codes a camera may emit without advertising them. The worker's up-front
# "does this camera offer anything we want" check must not halt on these.
UNADVERTISED_CODES = frozenset({DOORBELL_CODE})

# Verdicts. Anything other than CAPABLE means "do not expect events from this camera",
# and each carries a reason the user can act on.
CAPABLE      = "capable"        # advertises the smart codes and is switched on
DISABLED     = "disabled"       # capable, but SMD and/or MotionDetect is off
UNSUPPORTED  = "unsupported"    # firmware does not advertise the smart codes at all
UNREACHABLE  = "unreachable"    # could not be asked

HTTP_TIMEOUT = 8


# ============================================================
# Pure logic — no network, no clock
# ============================================================

def parse_event_list(text):
    """Return the set of event codes a camera advertises.

    Input is the body of eventManager.cgi?action=getExposureEvents, which looks like
        events[0]=AudioAnomaly
        events[1]=CrossLineDetection
    Returns an empty set for junk or an error string rather than raising — an
    unparseable answer is 'we learned nothing', which the caller must not read as
    'the camera has no events'.
    """
    if not text:
        return set()
    return {m.group(1) for m in re.finditer(r"events\[\d+\]=(\w+)", text)}


def _on_channel(channel_index):
    """' on channel N' for a reason line, numbered as the NVR shows it; '' when
    no channel was asked about."""
    return "" if channel_index is None else f" on channel {channel_index + 1}"


def parse_enable_flag(text, param, channel_index=None):
    """Return True/False for a `table.<param>[N].Enable=` line, or None if absent.

    N is channel_index, 0 when it is None: a single camera's only channel.

    None is a THIRD value and must stay distinct from False: 'the camera did not tell
    us' is not 'the camera says no'. Collapsing them is how an absent reading gets
    read as a healthy one.
    """
    if not text:
        return None
    index = 0 if channel_index is None else int(channel_index)
    m = re.search(rf"{re.escape(param)}\[{index}\]\.Enable=(\w+)", text)
    if not m:
        return None
    return m.group(1).strip().lower() == "true"


def parse_rules(rule_text, channel_index=None):
    """Return [(rule_type, enabled, name)] from a VideoAnalyseRule config body.

    The first [n] is the channel. channel_index=None takes rules from every
    channel, as before 1.4; a number takes only that channel's.

    Lines look like:
        table.VideoAnalyseRule[0][2].Class=CrossLineDetection   (shape A)
        table.VideoAnalyseRule[0][2].Type=CrossLineDetection    (shape B)
        table.VideoAnalyseRule[0][2].Class=Normal               (shape B)
        table.VideoAnalyseRule[0][2].Enable=true

    TWO SHAPES CONFIRMED ON REAL HARDWARE, and they disagree. Shape A (Left
    Garage Door, captured 01-09-2026) writes the real category straight into
    Class. Shape B (Patio, a 2021-07-07-build camera, captured 06-09-2026
    after genuinely drawing and enabling a tripwire through the camera's own
    web UI) writes Class=Normal for every ordinary IVS rule — Class is only
    ever a real category name for the special FaceDetection entry — and puts
    the actual category in Type instead. Matching on Class alone (the
    original, assumed-not-measured shape) made a real, drawn, enabled rule
    invisible FOR EVER on shape-B firmware: NO_RULE regardless of what was
    actually configured, which is precisely the failure this capability check
    exists to catch. Type is preferred when both are present and disagree,
    because Class=Normal carries no information; falling back to Class covers
    shape A, where Type may be absent altogether.
    Slots are sparse and unordered, so they are gathered by index rather than
    assumed adjacent.
    """
    if not rule_text:
        return []
    slots = {}
    for m in re.finditer(r"VideoAnalyseRule\[(\d+)\]\[(\d+)\]\.(Class|Type|Enable|Name)=(\S*)",
                         rule_text):
        ch, idx, key, val = m.groups()
        slots.setdefault((ch, idx), {})[key] = val
    return [(d.get("Type") or d.get("Class", ""),
             d.get("Enable", "").lower() == "true", d.get("Name", ""))
            for (ch, _), d in sorted(slots.items())
            if channel_index is None or ch == str(int(channel_index))]


def assess_ivs(event_text, rule_text, klass, channel_index=None):
    """Can this camera emit the IVS event for `klass`? Returns (verdict, reason).

    Two separate questions, and conflating them is the whole risk:
      1. does the firmware advertise the event at all
      2. is there an ENABLED rule of that class drawn on the camera
    A camera can pass the first and fail the second for ever, sitting there looking
    perfectly healthy and never firing once.
    """
    code = IVS_CODES.get(klass)
    if code is None:
        return UNSUPPORTED, f"unknown detection class {klass!r}"

    codes = parse_event_list(event_text)
    if not codes:
        return UNREACHABLE, "camera did not return an event list"
    if code not in codes:
        return UNSUPPORTED, f"firmware does not advertise {code}"

    if rule_text is None:
        return UNREACHABLE, "could not read the camera's analytics rules"

    rules = parse_rules(rule_text, channel_index)
    matching = [r for r in rules if r[0] == code]
    where = _on_channel(channel_index)
    if not matching:
        return (NO_RULE,
                f"the firmware supports {code} but no such rule is drawn{where} — "
                f"add one in the camera's own web interface, then restart this device")
    if not any(enabled for _, enabled, _ in matching):
        names = ", ".join(n or "unnamed" for _, _, n in matching)
        # DISABLED, not NO_RULE: the rule EXISTS, the reason text already says
        # so correctly ("a CrossLineDetection rule exists (IVS-1) but is
        # switched off at the camera") — but returning NO_RULE here discarded
        # that distinction downstream. plugin.py's _settle_device branches on
        # the VERDICT, not the reason string, so the device showed the
        # generic "no rule drawn on the camera" instead of "switched off at
        # the camera" — a real, different, actionable diagnosis thrown away.
        # Found 06-09-2026 disabling a real rule on Patio as a live test.
        return (DISABLED,
                f"a {code} rule exists ({names}) but is switched off at the camera{where}")
    return CAPABLE, f"an enabled {code} rule is drawn{where or ' on the camera'}"


def parse_device_type(text):
    """The model from magicBox.cgi?action=getDeviceType ("type=AD110"). "" when unknown."""
    m = re.search(r"type=([^\r\n]+)", text or "")
    return m.group(1).strip() if m else ""


def assess_doorbell(event_text, type_text):
    """Will this device report its button being pressed? Returns (verdict, reason).

    Advertising the code is proof. Not advertising it is NOT proof of absence,
    because a real doorbell was measured sending it unlisted (see DOORBELL_CODE),
    so the device type decides the rest. A camera that is neither is refused: a
    Doorbell Pressed device on an ordinary camera would look healthy and never
    fire, which is the failure this module exists to prevent.
    """
    codes = parse_event_list(event_text)
    if not codes:
        return UNREACHABLE, "camera did not return an event list"
    if DOORBELL_CODE in codes:
        return CAPABLE, f"advertises {DOORBELL_CODE}, the button-press event"
    model = parse_device_type(type_text)
    if model.upper().startswith(DOORBELL_TYPE_PREFIXES):
        return (CAPABLE,
                f"{model} is a doorbell: it sends {DOORBELL_CODE} on a press "
                f"without listing it")
    return (UNSUPPORTED,
            f"not a doorbell (device type {model or 'unknown'}) and it does not "
            f"advertise {DOORBELL_CODE}")


def assess(event_text, smd_text, motion_text, channel_index=None):
    """Decide what a camera can actually do. Returns (verdict, reason).

    Order matters. Capability is judged FIRST and only from the advertised event
    list, because that is the one signal a camera cannot get wrong. Only once a
    camera is known to be capable do the enable flags mean anything.
    """
    codes = parse_event_list(event_text)

    if not codes:
        return UNREACHABLE, "camera did not return an event list"

    missing = [c for c in SMART_CODES if c not in codes]
    if missing:
        return (UNSUPPORTED,
                "firmware does not advertise " + " or ".join(missing) +
                " — it cannot emit smart detections however it is configured")

    smd    = parse_enable_flag(smd_text, "SmartMotionDetect", channel_index)
    motion = parse_enable_flag(motion_text, "MotionDetect", channel_index)
    where  = _on_channel(channel_index)

    if smd is None or motion is None:
        return (UNREACHABLE,
                f"could not read the SmartMotionDetect / MotionDetect settings{where}")

    # SMD filters motion events rather than replacing them, so MotionDetect being off
    # makes SMD a no-op no matter what SMD itself says. The camera even reverts SMD to
    # false when MotionDetect is switched off, so report both rather than just SMD.
    off = []
    if not motion:
        off.append("MotionDetect")
    if not smd:
        off.append("SmartMotionDetect")
    if off:
        return DISABLED, " and ".join(off) + f" switched off{where or ' on the camera'}"

    return CAPABLE, f"advertises and has enabled human and vehicle detection{where}"


def describe(verdict, reason, address):
    """One human-readable line for the log. Kept here so the plugin and the tests
    agree on the wording, and so it can be asserted."""
    prefix = {
        CAPABLE:     "OK",
        DISABLED:    "OFF",
        NO_RULE:     "NO RULE",
        UNSUPPORTED: "UNSUPPORTED",
        UNREACHABLE: "UNREACHABLE",
    }.get(verdict, "?")
    return f"[{prefix}] {address} — {reason}"


# ============================================================
# Network shell — thin, and the only part that needs a camera
# ============================================================

class _OneTryDigestAuthHandler(urllib.request.HTTPDigestAuthHandler):
    """Digest login that sends the password once, and stops if it is refused.

    The standard handler answers a 401 by retrying, and when the retry is
    refused it retries again, until an internal counter passes 5. Every retry
    is a failed login on the camera: measured 01-11-2026 against a local server
    that refuses everything, one request made 7 calls, 6 of them carrying the
    wrong password. Dahua cameras lock an account after a few failed logins, so
    that alone could lock it, and the stream retries for ever.

    Request.get_header() looks at the header the retry adds, so a 401 on a
    request that already carries an Authorization header means "this password
    was refused", and it is reported at once instead of tried again.
    """

    def http_error_401(self, req, fp, code, msg, headers):
        if req.get_header(self.auth_header):
            raise urllib.error.HTTPError(req.full_url, 401, LOGIN_REFUSED_TEXT,
                                         headers, fp)
        return super().http_error_401(req, fp, code, msg, headers)


# The text of the HTTPError raised above. No URL, user or password in it.
LOGIN_REFUSED_TEXT = "login refused"


def is_login_refused(exc):
    """True when an exception means the camera turned the credentials down."""
    return isinstance(exc, urllib.error.HTTPError) and exc.code == 401


def make_opener(url, user, password):
    """An opener that logs in with Digest, or Basic for old cameras, trying the
    password at most once per request. Used by fetch() and by the stream worker."""
    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, url, user, password)
    # Digest first: it is what current firmware wants. Basic is kept for older
    # cameras that never learned digest; urllib's Basic handler already gives up
    # after one refused try.
    return urllib.request.build_opener(
        _OneTryDigestAuthHandler(mgr),
        urllib.request.HTTPBasicAuthHandler(mgr),
    )




# ============================================================
# Login memory - one refusal per camera, not one per request
# ============================================================

# How long a camera that refused a username and password is left alone with
# those same details. Dahua locks an account after a few failed logins, and a
# locked account refuses even the right password until the lock times out, so
# this still tries again rather than giving up. Saving different details in
# Configure, or reloading the plugin, ends the wait at once.
LOGIN_RETRY = 1800

_GOOD    = "good"
_REFUSED = "refused"

_logins_lock = threading.Lock()
_logins      = {}           # address -> (details hash, _GOOD or _REFUSED, monotonic time)
_first_try   = {}           # address -> Lock, held while the details are unproven


def _details_key(user, password):
    """A hash, so this module never holds a second copy of the password."""
    return hashlib.sha256(f"{user}\0{password}".encode("utf-8")).hexdigest()


def _norm(address):
    return str(address or "").strip().lower()


def login_refused_for(address, user, password, now=None):
    """Seconds left before this camera is asked again with these details, or 0.

    0 means go ahead: the details have not been refused, or the refusal was for
    different details, or LOGIN_RETRY has passed since it.
    """
    now = time.monotonic() if now is None else now
    with _logins_lock:
        entry = _logins.get(_norm(address))
    if not entry or entry[0] != _details_key(user, password) or entry[1] != _REFUSED:
        return 0
    return max(0.0, entry[2] + LOGIN_RETRY - now)


def record_login(address, user, password, accepted, now=None):
    """Remember whether this camera accepted these details. Called by fetch() and
    by the stream worker after every answer that says either way."""
    now = time.monotonic() if now is None else now
    with _logins_lock:
        _logins[_norm(address)] = (_details_key(user, password),
                                   _GOOD if accepted else _REFUSED, now)


def forget_logins():
    """Clear the memory. For tests; a plugin reload starts empty anyway."""
    with _logins_lock:
        _logins.clear()
        _first_try.clear()


def _one_at_a_time(address, user, password):
    """Until a camera has accepted these details, its requests take turns, so
    that a refusal is known before the next one sends the password. Once the
    details are accepted, requests run side by side as before."""
    key = _norm(address)
    with _logins_lock:
        entry = _logins.get(key)
        if entry and entry[0] == _details_key(user, password) and entry[1] == _GOOD:
            return contextlib.nullcontext()
        return _first_try.setdefault(key, threading.Lock())


REFUSED_REASON = ("the camera refused the username or password; it will not be "
                  "asked again for up to 30 minutes, or until you save new details "
                  "in Configure or reload the plugin")


def fetch(address, path, user, password, timeout=HTTP_TIMEOUT):
    """GET one CGI path. Returns the body, or None on any failure.

    Returns None rather than raising so one unreachable camera cannot take out a
    sweep of seven. The caller distinguishes None from empty.
    """
    url = f"http://{address}{path}"
    if login_refused_for(address, user, password):
        return None                     # refused lately: do not add a failed login
    with _one_at_a_time(address, user, password):
        if login_refused_for(address, user, password):
            return None                 # refused while this request waited its turn
        try:
            body = make_opener(url, user, password).open(url, timeout=timeout).read().decode(
                "utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if is_login_refused(exc):
                record_login(address, user, password, accepted=False)
            return None
        except (urllib.error.URLError, OSError, ValueError):
            return None
        record_login(address, user, password, accepted=True)
        return body


def probe_ivs(address, user, password, klass, timeout=HTTP_TIMEOUT, channel_index=None):
    """Ask one camera whether an IVS class will actually fire. NEVER raises."""
    try:
        if not address:
            return UNREACHABLE, "no address configured"
        if not user or not password:
            return UNREACHABLE, "no camera credentials — set DAHUA_USER / DAHUA_PASS"
        events = fetch(address, "/cgi-bin/eventManager.cgi?action=getExposureEvents",
                       user, password, timeout)
        if events is None:
            if login_refused_for(address, user, password):
                return UNREACHABLE, REFUSED_REASON
            return UNREACHABLE, "no answer on port 80"
        rules = fetch(address, "/cgi-bin/configManager.cgi?action=getConfig&name=VideoAnalyseRule",
                      user, password, timeout)
        return assess_ivs(events, rules, klass, channel_index)
    except Exception as exc:                    # noqa: BLE001 - the contract is "never raises"
        return UNREACHABLE, f"unexpected error probing this camera: {exc!r}"


def probe_doorbell(address, user, password, timeout=HTTP_TIMEOUT):
    """Ask one device whether it will report its button. NEVER raises."""
    try:
        if not address:
            return UNREACHABLE, "no address configured"
        if not user or not password:
            return UNREACHABLE, "no camera credentials — set DAHUA_USER / DAHUA_PASS"
        events = fetch(address, "/cgi-bin/eventManager.cgi?action=getExposureEvents",
                       user, password, timeout)
        if events is None:
            if login_refused_for(address, user, password):
                return UNREACHABLE, REFUSED_REASON
            return UNREACHABLE, "no answer on port 80"
        dtype = fetch(address, "/cgi-bin/magicBox.cgi?action=getDeviceType",
                      user, password, timeout)
        return assess_doorbell(events, dtype)
    except Exception as exc:                    # noqa: BLE001 - the contract is "never raises"
        return UNREACHABLE, f"unexpected error probing this camera: {exc!r}"


def probe(address, user, password, timeout=HTTP_TIMEOUT, channel_index=None):
    """Ask one camera what it can do. Returns (verdict, reason). NEVER raises.

    fetch() deliberately catches only the expected network errors, so a genuine bug
    in this module still surfaces rather than hiding behind a blanket except. But
    this function is the public entry point and callers sweep seven cameras with it,
    so the whole body is guarded: one camera must cost one camera. The surprise is
    reported in the reason rather than swallowed.
    """
    try:
        return _probe(address, user, password, timeout, channel_index)
    except Exception as exc:                    # noqa: BLE001 - the contract is "never raises"
        return UNREACHABLE, f"unexpected error probing this camera: {exc!r}"


def _probe(address, user, password, timeout=HTTP_TIMEOUT, channel_index=None):
    if not address:
        return UNREACHABLE, "no address configured"
    if not user or not password:
        return UNREACHABLE, "no camera credentials — set DAHUA_USER / DAHUA_PASS"

    events = fetch(address, "/cgi-bin/eventManager.cgi?action=getExposureEvents",
                   user, password, timeout)
    if events is None:
        if login_refused_for(address, user, password):
            return UNREACHABLE, REFUSED_REASON
        return UNREACHABLE, "no answer on port 80 (wrong address, credentials, or camera down)"

    smd = fetch(address, "/cgi-bin/configManager.cgi?action=getConfig&name=SmartMotionDetect",
                user, password, timeout)
    motion = fetch(address, "/cgi-bin/configManager.cgi?action=getConfig&name=MotionDetect",
                   user, password, timeout)
    return assess(events, smd, motion, channel_index)


def firmware(address, user, password, timeout=HTTP_TIMEOUT):
    """Best-effort firmware string, for the diagnostic log. Empty when unknown."""
    body = fetch(address, "/cgi-bin/magicBox.cgi?action=getSoftwareVersion",
                 user, password, timeout)
    if not body:
        return ""
    m = re.search(r"version=([^\s,]+(?:,build:[\d-]+)?)", body)
    return m.group(1) if m else ""


# A dialog cannot wait: Indigo gives a UI callback about thirty seconds before it
# gives up and leaves the dialog broken. Probing five classes one at a time would
# re-fetch the same documents five times over, so this fetches each ONCE and then
# answers every question from them. Five requests at 4 s each is 20 s at worst,
# inside the limit, and an address with nothing at it stops after the first.
DIALOG_TIMEOUT = 4


def capabilities(address, user, password, timeout=DIALOG_TIMEOUT, channel_index=None):
    """What can this camera actually do? Returns {class: (verdict, reason)}.

    Answers for every class the plugin knows, so a dialog can show the whole
    picture at once rather than making the user discover it a device at a time.
    NEVER raises: a dialog that throws is worse than one that reports a problem.
    """
    classes = ["person", "vehicle"] + list(IVS_CODES) + ["doorbell"]
    try:
        if not address:
            return {k: (UNREACHABLE, "no address given") for k in classes}
        if not user or not password:
            return {k: (UNREACHABLE, "no camera credentials") for k in classes}

        events = fetch(address, "/cgi-bin/eventManager.cgi?action=getExposureEvents",
                       user, password, timeout)
        if events is None:
            if login_refused_for(address, user, password):
                return {k: (UNREACHABLE, REFUSED_REASON) for k in classes}
            return {k: (UNREACHABLE, "no answer from the camera") for k in classes}

        smd    = fetch(address, "/cgi-bin/configManager.cgi?action=getConfig&name=SmartMotionDetect",
                       user, password, timeout)
        motion = fetch(address, "/cgi-bin/configManager.cgi?action=getConfig&name=MotionDetect",
                       user, password, timeout)
        rules  = fetch(address, "/cgi-bin/configManager.cgi?action=getConfig&name=VideoAnalyseRule",
                       user, password, timeout)

        dtype  = fetch(address, "/cgi-bin/magicBox.cgi?action=getDeviceType",
                       user, password, timeout)

        smd_verdict = assess(events, smd, motion, channel_index)
        out = {"person": smd_verdict, "vehicle": smd_verdict}
        for klass in IVS_CODES:
            out[klass] = assess_ivs(events, rules, klass, channel_index)
        out["doorbell"] = assess_doorbell(events, dtype)
        return out
    except Exception as exc:                    # noqa: BLE001 - a dialog must not throw
        return {k: (UNREACHABLE, f"unexpected error: {exc!r}") for k in classes}


_XML_RESERVED = "&<>\"'"


def ascii_only(text):
    """Strip anything outside plain ASCII, and Indigo's five reserved XML characters.

    Indigo serialises device names, props and states through XML, and a non-ASCII
    OR XML-reserved character in a value written at RUNTIME can be rejected with
    `LowLevelBadParameterError -- illegal character in XML tag name or value`,
    which names neither the field nor the character. Static UTF-8 in the XML files
    is fine — this is about what we put in at runtime. The house rule already said
    ASCII only; a middle dot in a summary line broke it (01-09-2026). An ampersand
    or a stray quote typed into the Camera Name or Hold field breaks device
    creation the same way — printable ASCII, but still illegal in this position —
    found 02-09-2026 onboarding "Patio" and left unfixed until now (TRIAGE_QUEUE).
    """
    if text is None:
        return ""
    return "".join(c if 32 <= ord(c) < 127 and c not in _XML_RESERVED else "?"
                  for c in str(text))


def summarise(caps):
    """One short line per class, for a dialog field. Ordered, so it reads the same
    every time and a changed answer is noticeable. ASCII ONLY — see ascii_only()."""
    labels = {"person": "People", "vehicle": "Vehicles",
              "crossline": "Tripwire", "crossregion": "Intrusion",
              "doorbell": "Doorbell"}
    marks  = {CAPABLE: "yes", DISABLED: "off at the camera",
              NO_RULE: "no rule drawn", UNSUPPORTED: "not supported",
              UNREACHABLE: "could not tell"}
    return ascii_only(" | ".join(f"{labels[k]}: {marks.get(caps[k][0], '?')}"
                                 for k in ("person", "vehicle", "crossline", "crossregion",
                                           "doorbell")
                                 if k in caps))
