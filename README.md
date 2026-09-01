# DahuaEvents

**Version:** 1.9

Turns a Dahua camera's own onboard smart-motion detection into native Indigo devices, so
person and vehicle detections can drive triggers, notifications and control pages.

The camera does the thinking. This plugin listens.

## Why

Most Dahua cameras made in the last few years classify what they see — a person, a vehicle —
on the camera itself, using hardware you have already paid for. Nothing else is needed: no
video analytics server, no subscription, no AI accelerator, and no video leaving your network.
The catch is that nothing in Indigo listens to those events. This plugin is the missing piece.

You get one Indigo sensor per camera per class — `Drive Person`, `Drive Vehicle` — that turns
on when the camera sees one, and off again shortly after it stops.

## Requirements

- A Dahua camera whose firmware supports Smart Motion Detection. Roughly speaking, models from
  2022 onward; older cameras advertise the older IVS rules instead and are not yet supported.
- Smart Motion Detection switched on in the camera, along with ordinary motion detection —
  SMD filters motion events rather than replacing them, so both are needed.
- A camera user with access to the event and configuration API.

Use **Plugins -> DahuaEvents -> Probe a Camera...** to find out where a camera stands. It says
plainly whether the camera can do smart detection, whether it is switched on, and if not, why
not.

## Credentials

Read from `IndigoSecrets.py` first, then from the plugin's own configuration:

| Key | Meaning |
|---|---|
| `DAHUA_USER` | camera username |
| `DAHUA_PASS` | camera password |

If you do not use `IndigoSecrets.py`, leave it out entirely and fill the same two fields in
**Plugins -> DahuaEvents -> Configure**. Anything found in `IndigoSecrets.py` wins.

## Installation

1. Go to the [Releases](https://github.com/Highsteads/DahuaEvents/releases) page and download
   `DahuaEvents.indigoPlugin.zip`.
2. Unzip it — you will get `DahuaEvents.indigoPlugin`.
3. Double-click `DahuaEvents.indigoPlugin` and Indigo will install it.

## Setting it up

1. Install the plugin and, if you do not use `IndigoSecrets.py`, put the camera username and
   password into **Plugins -> DahuaEvents -> Configure**.
2. **Plugins -> DahuaEvents -> Dahua Camera...** Give the camera a name and its address, then
   press **Check This Camera**. It reports what that camera can do and ticks the detections it
   supports. Close the dialog and you get a device per ticked detection, named
   `<camera> Person`, `<camera> Vehicle` and so on.
3. Repeat for each camera.

Use **Probe a Camera...** first if you are unsure whether a camera can do this. It reports one of
four answers, and says why:

| | Meaning |
|---|---|
| capable | advertises human and vehicle detection, and it is switched on |
| disabled | it can, but Smart Motion Detection or ordinary motion detection is off at the camera |
| unsupported | the firmware cannot emit these events, whatever the settings say |
| no rule | (tripwire and intrusion only) the firmware can, but no rule is drawn on the camera |
| unreachable | no answer — wrong address, wrong credentials, or the camera is down |

A camera that cannot do it still gets devices. They sit in an error state explaining why, so
nothing disappears silently, and if you update the firmware or replace the camera they simply
start working.

## Older cameras — tripwire and intrusion

Cameras from before roughly 2022 have no Smart Motion Detection and will report **unsupported**
for people and vehicles. Most of them do offer the older IVS rules instead, and those can be
filtered to people or vehicles, so the camera is not necessarily a lost cause.

The difference is that IVS reports nothing until you tell it where to look. In the camera's own
web interface, under Smart Plan or IVS, draw a **tripwire** (a line) or an **intrusion zone** (an
area), set it to trigger on Human or Vehicle, and enable it. Then tick Tripwire or Intrusion when
you add the camera here.

The plugin checks whether a rule is actually drawn, not merely whether the firmware supports the
idea. A camera with no rule says so — it does not sit there looking healthy and never firing.

## How it behaves

The camera reports the start and end of a detection, often many times as somebody moves through
frame. The plugin turns that into one clean detection: the device switches on at the first
report and stays on until the hold expires after the last one. Twenty seconds by default,
adjustable globally and per camera.

Each camera gets one connection, shared by its two devices. If it drops, the plugin reconnects
with a backoff that caps at a minute. A camera whose firmware cannot emit these events is not
retried at all — it is marked and left alone, because it will never start working by being
asked more often.

## A note on grouped devices

The two devices for a camera are created as a group. Deleting one in Indigo will offer to delete
the whole group, and recreating them gives them **new device IDs** — so any trigger, control page
or script pointing at the old ones will quietly stop working. Rename them freely; just be careful
about deleting.

## Changelog

### 1.9
- **Fixes device creation failing in the camera dialog** with "illegal character in XML tag name
  or value". The capability summary added in 1.8 used a non-ASCII separator, and Indigo refuses
  runtime text containing one — naming neither the field nor the character. Everything the plugin
  writes at runtime is now plain ASCII, and the summary is a display field that no longer travels
  into device creation at all.

### 1.8
- **The camera dialog now has a Check This Camera button.** It asks the camera what it can do and
  ticks the right boxes for you, instead of leaving you to pick, create devices and only then
  discover which of them work.

### 1.7
- Sensor actions are handled. Sending on, off or a status request to one of these devices used
  to be dropped by Indigo with an error and no explanation, because the plugin never implemented
  the callback that declaring a sensor device obliges. A status request now re-checks the camera;
  on and off say plainly that the device is read-only.
- **Detections today** now resets at midnight rather than on the next detection, so a camera
  that saw twelve yesterday no longer reads twelve all morning.

### 1.6
- **Support for older cameras via IVS rules** — tripwire (line crossed) and intrusion (zone
  entered), both of which can be filtered to people or vehicles. These are the pre-SMD
  generation's equivalent, so cameras that reported "unsupported" may still be useful.
- IVS needs a line or zone drawn in the camera's own web interface first. The plugin checks for
  one and says so plainly when it is missing or switched off, rather than sitting there looking
  healthy and never firing.
- The camera dialog now asks which detections you want, so you only get the devices you use.

### 1.5
- **Actually fixes the force-kill on restart that 1.4 only half-fixed.** The worker threads
  blocked inside a socket read, and closing the connection from another thread waits for that
  read rather than cancelling it — so shutting down waited out the socket timeout. Measured
  against five cameras: **25.8 seconds before, 0.79 seconds after.**

### 1.4
- **Fixes the plugin having to be force-killed on upgrade or restart.** Indigo raises its stop
  signal only from inside `self.sleep()`, and the event loop did its own waiting on the queue,
  so it never learned it had been asked to stop. Shutdown is now also bounded in total rather
  than per camera, so adding cameras cannot push it past Indigo's patience.

### 1.3
- The event and status drains are bounded. Draining until empty had no upper limit, so a
  camera producing detections faster than they could be applied would have stopped hold
  expiry running — leaving every device stuck on, including the cameras behaving perfectly.

### 1.2
- Log lines now carry the `[HH:MM:SS.mmm]` prefix every other plugin here uses.

### 1.1
- Live detections. A worker thread per camera holds the event stream open; the plugin's main
  thread is the only thing that writes a device state, so no locks are needed anywhere.
- **Dahua Camera...** device factory — one dialog per camera creates its Person and Vehicle
  sensors together, so the pair can never be half-configured or inconsistently named.
- Detection hold, configurable globally and per camera, so one person walking past fires a
  trigger once rather than a dozen times.
- **Test All Cameras** now runs in the background. Indigo's UI callbacks time out after about
  30 seconds and a sweep of several cameras can exceed that, leaving the dialog broken.

### 1.0
- Capability probing that trusts the camera's advertised event list rather than its
  configuration flags, because a camera will happily report smart detection as enabled while
  being incapable of emitting it.
- **Probe a Camera...** and **Test All Cameras** diagnostic menu items.

## Authors & licence

Vibed into existence by **CliveS**, who knew what he wanted, argued until he got it, and tested it on a real house. Typed at inhuman speed by **Claude** (Anthropic), who mostly did as it was told.

© 2026 CliveS · [MIT licence](LICENSE) — copy it, fork it, bend it, break it, fix it, ship it. If it breaks, you get to keep both pieces.
