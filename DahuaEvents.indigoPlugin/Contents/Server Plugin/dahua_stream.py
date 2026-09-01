#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    dahua_stream.py
# Description: The two pieces of logic that turn a camera's event stream into a
#              device that behaves itself:
#
#                StreamParser  bytes off a socket -> discrete events, coping with
#                              reads that split a line in half
#                HoldTimer     events -> on/off, with a hold so one person walking
#                              past fires a trigger once rather than a dozen times
#
#              Both are PURE. No socket, no clock, no indigo import — time arrives
#              as a parameter, never read from the wall. That is what lets the whole
#              of the interesting behaviour be tested with no camera and no waiting,
#              including the once-a-year cases a live test would never reach.
# Author:      CliveS & Claude Opus 5
# Date:        01-09-2026
# Version:     1.0

import re

# Event lines look like:
#   Code=SmartMotionHuman;action=Start;index=0;data={
# Anchored at the start of the line so a JSON body containing the word Code cannot
# be mistaken for an event. The data={ tail opens a JSON block spanning later lines,
# which simply will not match and is therefore ignored — everything needed is here.
_EVENT_RE = re.compile(r"^Code=(?P<code>\w+);\s*action=(?P<action>\w+);\s*index=(?P<index>\d+)")

START = "Start"
STOP  = "Stop"

# A camera that streams junk without newlines must not grow the buffer for ever.
# 64 KB is far beyond any real event line; past that the partial line is discarded
# and the parser resynchronises on the next newline.
MAX_BUFFER = 65536


class Event:
    """One decoded event. Deliberately tiny and comparable, so tests read well."""

    __slots__ = ("code", "action", "index")

    def __init__(self, code, action, index):
        self.code = code
        self.action = action
        self.index = index

    def __eq__(self, other):
        return (isinstance(other, Event)
                and (self.code, self.action, self.index)
                == (other.code, other.action, other.index))

    def __hash__(self):
        return hash((self.code, self.action, self.index))

    def __repr__(self):
        return f"Event({self.code!r}, {self.action!r}, {self.index})"


def parse_event_line(line):
    """Decode one line. Returns an Event, or None for anything else.

    Everything that is not an event line — multipart boundaries, HTTP headers,
    JSON body lines, heartbeats, blanks — returns None rather than raising. The
    stream is full of them by design.
    """
    if not line:
        return None
    # BOTH the ^ in the pattern and .match() anchor to position 0, and either alone
    # is enough. That redundancy is deliberate: with .search() and no ^, a JSON body
    # containing the text of an event would forge one. Sabotage-tested 01-09-2026 —
    # removing either is a no-op, removing both is caught by the tests. Do not
    # "tidy away" one of them on the grounds that it looks redundant; it is, and
    # that is the point.
    m = _EVENT_RE.match(line.strip())
    if not m:
        return None
    return Event(m.group("code"), m.group("action"), int(m.group("index")))


class StreamParser:
    """Accumulates chunks off the socket and yields whole events.

    A read can end anywhere, including the middle of an event line, so the tail is
    held back until its newline arrives. Without this a detection is silently lost
    whenever the network happens to split a packet in the wrong place — which is
    rare, unreproducible, and would be blamed on the camera.
    """

    def __init__(self):
        self._buffer = ""
        self.dropped_bytes = 0      # surfaced so a misbehaving camera is visible

    def feed(self, chunk):
        """Add a chunk of decoded text; return the list of complete events in it."""
        if not chunk:
            return []
        self._buffer += chunk

        if len(self._buffer) > MAX_BUFFER:
            # Keep the tail, not the head: the newline we need is ahead of us.
            self.dropped_bytes += len(self._buffer) - MAX_BUFFER
            self._buffer = self._buffer[-MAX_BUFFER:]

        events = []
        while "\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition("\n")
            event = parse_event_line(line.rstrip("\r"))
            if event is not None:
                events.append(event)
        return events

    def pending(self):
        """Bytes held back awaiting a newline. For diagnostics only."""
        return len(self._buffer)


class HoldTimer:
    """On/off for one detection class on one camera, with a trailing hold.

    Start switches on. Stop does NOT switch off — it schedules the off for
    `hold_seconds` later, and any further Start cancels that. So one person moving
    through frame, which produces a burst of Start/Stop pairs, is a single
    continuous "on" rather than a dozen trigger firings.

    Every method takes `now` and returns whether the on/off state CHANGED, so the
    caller writes to Indigo only on a real transition. Re-asserting an unchanged
    state would churn the state table and the SQL logger for nothing.
    """

    def __init__(self, hold_seconds):
        self.hold_seconds = max(0, int(hold_seconds))
        self._on = False
        self._expires_at = None

    @property
    def is_on(self):
        return self._on

    @property
    def expires_at(self):
        return self._expires_at

    def start(self, now):
        """A detection began, or continues. Returns True if this switched it on."""
        self._expires_at = None          # actively detecting: nothing pending
        if self._on:
            return False
        self._on = True
        return True

    def stop(self, now):
        """A detection ended. Schedules the off; never switches off immediately.

        Returns True only in the degenerate hold=0 case, where 'later' is now.
        """
        if not self._on:
            return False
        if self.hold_seconds == 0:
            self._on = False
            self._expires_at = None
            return True
        self._expires_at = now + self.hold_seconds
        return False

    def tick(self, now):
        """Expire a pending off if its moment has come. Returns True if it did."""
        if self._on and self._expires_at is not None and now >= self._expires_at:
            self._on = False
            self._expires_at = None
            return True
        return False

    def next_deadline(self):
        """When tick() next has something to do, or None. Lets the caller sleep
        sensibly instead of spinning."""
        return self._expires_at
