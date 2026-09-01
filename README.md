# DahuaEvents

**Version:** 1.0

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

## Status

Stage 1 of 4. Capability probing and the diagnostic menus work. The event stream, the device
model and the detection hold follow — see `SPEC.md` for the full plan.

## Changelog

### 1.0 (in progress)
- Capability probing that trusts the camera's advertised event list rather than its
  configuration flags, because a camera will happily report smart detection as enabled while
  being incapable of emitting it.
- **Probe a Camera...** and **Test All Cameras** diagnostic menu items.

## Authors & licence

Vibed into existence by **CliveS**, who knew what he wanted, argued until he got it, and tested it on a real house. Typed at inhuman speed by **Claude** (Anthropic), who mostly did as it was told.

© 2026 CliveS · [MIT licence](LICENSE) — copy it, fork it, bend it, break it, fix it, ship it. If it breaks, you get to keep both pieces.
