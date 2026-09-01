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
# Author:      CliveS & Claude Opus 5
# Date:        01-09-2026
# Version:     1.0

import re
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


def parse_enable_flag(text, param):
    """Return True/False for a `table.<param>[0].Enable=` line, or None if absent.

    None is a THIRD value and must stay distinct from False: 'the camera did not tell
    us' is not 'the camera says no'. Collapsing them is how an absent reading gets
    read as a healthy one.
    """
    if not text:
        return None
    m = re.search(rf"{re.escape(param)}\[0\]\.Enable=(\w+)", text)
    if not m:
        return None
    return m.group(1).strip().lower() == "true"


def parse_rules(rule_text):
    """Return [(class, enabled, name)] from a VideoAnalyseRule config body.

    Lines look like:
        table.VideoAnalyseRule[0][2].Class=CrossLineDetection
        table.VideoAnalyseRule[0][2].Enable=true
    Slots are sparse and unordered, so they are gathered by index rather than
    assumed adjacent.
    """
    if not rule_text:
        return []
    slots = {}
    for m in re.finditer(r"VideoAnalyseRule\[(\d+)\]\[(\d+)\]\.(Class|Enable|Name)=(\S*)",
                         rule_text):
        ch, idx, key, val = m.groups()
        slots.setdefault((ch, idx), {})[key] = val
    return [(d.get("Class", ""), d.get("Enable", "").lower() == "true", d.get("Name", ""))
            for _, d in sorted(slots.items())]


def assess_ivs(event_text, rule_text, klass):
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

    rules = parse_rules(rule_text)
    matching = [r for r in rules if r[0] == code]
    if not matching:
        return (NO_RULE,
                f"the firmware supports {code} but no such rule is drawn on the camera — "
                f"add one in the camera's own web interface, then restart this device")
    if not any(enabled for _, enabled, _ in matching):
        names = ", ".join(n or "unnamed" for _, _, n in matching)
        return (NO_RULE,
                f"a {code} rule exists ({names}) but is switched off at the camera")
    return CAPABLE, f"an enabled {code} rule is drawn on the camera"


def assess(event_text, smd_text, motion_text):
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

    smd    = parse_enable_flag(smd_text, "SmartMotionDetect")
    motion = parse_enable_flag(motion_text, "MotionDetect")

    if smd is None or motion is None:
        return UNREACHABLE, "could not read the SmartMotionDetect / MotionDetect settings"

    # SMD filters motion events rather than replacing them, so MotionDetect being off
    # makes SMD a no-op no matter what SMD itself says. The camera even reverts SMD to
    # false when MotionDetect is switched off, so report both rather than just SMD.
    off = []
    if not motion:
        off.append("MotionDetect")
    if not smd:
        off.append("SmartMotionDetect")
    if off:
        return DISABLED, " and ".join(off) + " switched off on the camera"

    return CAPABLE, "advertises and has enabled human and vehicle detection"


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

def _opener(url, user, password):
    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, url, user, password)
    # Digest first: it is what current firmware wants. Basic is kept for older
    # cameras that never learned digest.
    return urllib.request.build_opener(
        urllib.request.HTTPDigestAuthHandler(mgr),
        urllib.request.HTTPBasicAuthHandler(mgr),
    )


def fetch(address, path, user, password, timeout=HTTP_TIMEOUT):
    """GET one CGI path. Returns the body, or None on any failure.

    Returns None rather than raising so one unreachable camera cannot take out a
    sweep of seven. The caller distinguishes None from empty.
    """
    url = f"http://{address}{path}"
    try:
        return _opener(url, user, password).open(url, timeout=timeout).read().decode(
            "utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def probe_ivs(address, user, password, klass, timeout=HTTP_TIMEOUT):
    """Ask one camera whether an IVS class will actually fire. NEVER raises."""
    try:
        if not address:
            return UNREACHABLE, "no address configured"
        if not user or not password:
            return UNREACHABLE, "no camera credentials — set DAHUA_USER / DAHUA_PASS"
        events = fetch(address, "/cgi-bin/eventManager.cgi?action=getExposureEvents",
                       user, password, timeout)
        if events is None:
            return UNREACHABLE, "no answer on port 80"
        rules = fetch(address, "/cgi-bin/configManager.cgi?action=getConfig&name=VideoAnalyseRule",
                      user, password, timeout)
        return assess_ivs(events, rules, klass)
    except Exception as exc:                    # noqa: BLE001 - the contract is "never raises"
        return UNREACHABLE, f"unexpected error probing this camera: {exc!r}"


def probe(address, user, password, timeout=HTTP_TIMEOUT):
    """Ask one camera what it can do. Returns (verdict, reason). NEVER raises.

    fetch() deliberately catches only the expected network errors, so a genuine bug
    in this module still surfaces rather than hiding behind a blanket except. But
    this function is the public entry point and callers sweep seven cameras with it,
    so the whole body is guarded: one camera must cost one camera. The surprise is
    reported in the reason rather than swallowed.
    """
    try:
        return _probe(address, user, password, timeout)
    except Exception as exc:                    # noqa: BLE001 - the contract is "never raises"
        return UNREACHABLE, f"unexpected error probing this camera: {exc!r}"


def _probe(address, user, password, timeout=HTTP_TIMEOUT):
    if not address:
        return UNREACHABLE, "no address configured"
    if not user or not password:
        return UNREACHABLE, "no camera credentials — set DAHUA_USER / DAHUA_PASS"

    events = fetch(address, "/cgi-bin/eventManager.cgi?action=getExposureEvents",
                   user, password, timeout)
    if events is None:
        return UNREACHABLE, "no answer on port 80 (wrong address, credentials, or camera down)"

    smd = fetch(address, "/cgi-bin/configManager.cgi?action=getConfig&name=SmartMotionDetect",
                user, password, timeout)
    motion = fetch(address, "/cgi-bin/configManager.cgi?action=getConfig&name=MotionDetect",
                   user, password, timeout)
    return assess(events, smd, motion)


def firmware(address, user, password, timeout=HTTP_TIMEOUT):
    """Best-effort firmware string, for the diagnostic log. Empty when unknown."""
    body = fetch(address, "/cgi-bin/magicBox.cgi?action=getSoftwareVersion",
                 user, password, timeout)
    if not body:
        return ""
    m = re.search(r"version=([^\s,]+(?:,build:[\d-]+)?)", body)
    return m.group(1) if m else ""


# A dialog cannot wait: Indigo gives a UI callback about thirty seconds before it
# gives up and leaves the dialog broken. Probing four classes one at a time would
# re-fetch the same three documents four times over, so this fetches each ONCE and
# then answers every question from them. Four requests, bounded, whatever the
# camera does.
DIALOG_TIMEOUT = 4


def capabilities(address, user, password, timeout=DIALOG_TIMEOUT):
    """What can this camera actually do? Returns {class: (verdict, reason)}.

    Answers for every class the plugin knows, so a dialog can show the whole
    picture at once rather than making the user discover it a device at a time.
    NEVER raises: a dialog that throws is worse than one that reports a problem.
    """
    classes = ["person", "vehicle"] + list(IVS_CODES)
    try:
        if not address:
            return {k: (UNREACHABLE, "no address given") for k in classes}
        if not user or not password:
            return {k: (UNREACHABLE, "no camera credentials") for k in classes}

        events = fetch(address, "/cgi-bin/eventManager.cgi?action=getExposureEvents",
                       user, password, timeout)
        if events is None:
            return {k: (UNREACHABLE, "no answer from the camera") for k in classes}

        smd    = fetch(address, "/cgi-bin/configManager.cgi?action=getConfig&name=SmartMotionDetect",
                       user, password, timeout)
        motion = fetch(address, "/cgi-bin/configManager.cgi?action=getConfig&name=MotionDetect",
                       user, password, timeout)
        rules  = fetch(address, "/cgi-bin/configManager.cgi?action=getConfig&name=VideoAnalyseRule",
                       user, password, timeout)

        smd_verdict = assess(events, smd, motion)
        out = {"person": smd_verdict, "vehicle": smd_verdict}
        for klass in IVS_CODES:
            out[klass] = assess_ivs(events, rules, klass)
        return out
    except Exception as exc:                    # noqa: BLE001 - a dialog must not throw
        return {k: (UNREACHABLE, f"unexpected error: {exc!r}") for k in classes}


def ascii_only(text):
    """Strip anything outside plain ASCII.

    Indigo serialises device names, props and states through XML, and a non-ASCII
    character in a value written at RUNTIME can be rejected with
    `LowLevelBadParameterError -- illegal character in XML tag name or value`,
    which names neither the field nor the character. Static UTF-8 in the XML files
    is fine — this is about what we put in at runtime. The house rule already said
    ASCII only; a middle dot in a summary line broke it (01-09-2026).
    """
    if text is None:
        return ""
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in str(text))


def summarise(caps):
    """One short line per class, for a dialog field. Ordered, so it reads the same
    every time and a changed answer is noticeable. ASCII ONLY — see ascii_only()."""
    labels = {"person": "People", "vehicle": "Vehicles",
              "crossline": "Tripwire", "crossregion": "Intrusion"}
    marks  = {CAPABLE: "yes", DISABLED: "off at the camera",
              NO_RULE: "no rule drawn", UNSUPPORTED: "not supported",
              UNREACHABLE: "could not tell"}
    return ascii_only(" | ".join(f"{labels[k]}: {marks.get(caps[k][0], '?')}"
                                 for k in ("person", "vehicle", "crossline", "crossregion")
                                 if k in caps))
