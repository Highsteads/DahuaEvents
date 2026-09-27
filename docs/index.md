---
title: Home
nav_order: 1
---

# Dahua Events for Indigo

This plugin lets [Indigo](https://www.indigodomo.com) know when one of your Dahua cameras sees a person or a vehicle, using the recognition the camera already does on its own. There is no video analysis server, no subscription, nothing running on the Indigo Mac to study the pictures, and no video leaves your network — the camera decides what it saw and the plugin listens for it.

Each thing you want to know about becomes an ordinary Indigo sensor that switches on when the camera sees it and off again shortly after, so you can use it in triggers, notifications and control pages like any other device. I use it on the cameras round the house, so that someone walking up the drive switches **Drive Person** on.

It can listen for five things:

- **People** and **vehicles**, on cameras with the newer smart motion detection, which most Dahua cameras from about 2022 onward have.
- **A tripwire** (a line someone crosses) and **an intrusion zone** (an area someone enters), on older cameras that do not have smart motion detection, once you have drawn the line or zone in the camera's own settings.
- **The button on a video doorbell**, on a Dahua or Amcrest doorbell.

## What it does for you

- **Turns each detection into an Indigo sensor,** named after the camera, such as `Drive Person` and `Drive Vehicle`.
- **Fires once per visit, not a dozen times.** A camera reports many short detections as someone moves about, and the plugin keeps the sensor on until 20 seconds after the last one, so a trigger runs once.
- **Asks the camera what it can do** before you add it, and ticks the detections that will work.
- **Tells you plainly when a detection cannot work** — the firmware cannot do it, it is switched off in the camera, or no tripwire or zone has been drawn — rather than leaving a sensor that looks healthy and never switches on.
- **Counts the day's detections** and records the time of the last one.
- **Keeps the Event Log quiet.** Each detection goes to the plugin's own log file, and only warnings and errors go to the Event Log unless you ask for more.

## Where to go next

| If you want to... | Read |
|---|---|
| Install the plugin and add your first camera | [Getting started](getting-started.md) |
| Know which cameras work and what to switch on in them | [Your cameras](cameras.md) |
| Know what each device shows in Indigo | [Your devices](devices.md) |
| Understand what the plugin is doing behind the scenes | [How it works](how-it-works.md) |
| Run something when a camera sees someone | [Triggers and actions](triggers-and-actions.md) |
| Know what every setting does | [Settings](settings.md) |
| Know what each item in the Plugins menu does | [The plugin menu](plugin-menu.md) |
| Sort out a problem | [When something goes wrong](troubleshooting.md) |
| See what changed in each version | [Version history](changelog.md) |

## Download

The latest version is always on the [Releases page](https://github.com/Highsteads/DahuaEvents/releases/latest).
