---
title: How it works
nav_order: 5
---

# How it works

You do not need to know any of this to use the plugin. It is here for anyone who likes to know what is going on.

## The camera tells the plugin

The plugin does not look at any pictures. For each camera it opens one connection and leaves it open, and the camera sends a short message down it whenever a detection starts or stops. The camera also sends a small "still here" message every five seconds, so the plugin knows the connection is alive even when nothing is happening.

A camera with several devices — a Person and a Vehicle, say — still has only one connection, shared between them.

## One visit, one switch-on

As a person walks across the picture, the camera often reports many short detections, each starting and stopping within a second or two. If each of those switched the device, a trigger would run a dozen times for one visitor.

So the device switches on at the first report and stays on while the camera keeps reporting. When the reports stop, the device waits for the **hold** time — 20 seconds unless you change it — and only switches off if nothing new has started by then. Any new report during the wait keeps it on.

You can change the hold for every camera in the plugin's settings, or for one camera in its own settings. A hold of 0 switches the device off the moment the camera says the detection has ended.

The camera's message saying a detection has ended can be lost, if the camera reboots or its connection drops at the wrong moment. So when a camera's connection drops or is replaced, any of its devices that are on start the hold just as if that message had arrived. And a device that has heard nothing from the camera for the **Longest detection** time, 10 minutes unless you change it, switches off with a line in the Event Log saying no stop arrived.

## Checking what each detection needs

When a device starts, the plugin asks the camera whether that detection can actually work, and shows the answer on the device:

- **For people and vehicles**, it checks the camera's list of the events it can send, then that motion detection and smart motion detection are both switched on.
- **For a tripwire or intrusion zone**, it checks the camera can send that event, then that a rule of that kind is drawn on the camera and switched on.
- **For a doorbell**, it checks the camera offers the button-press event or has a doorbell's model name.

The answer belongs to that one device, so a camera whose people detection works but which has no tripwire drawn has a working Person device and a Tripwire device saying **no rule drawn on the camera**. The connection coming and going does not change that answer — only **Send Status Request**, or the device or plugin restarting, checks again.

## When a camera drops out

If the camera closes the connection, or goes 20 seconds without even a "still here" message, the plugin sets the camera's devices to **Reconnecting**, writes a warning to the Event Log, and tries again after one second. Each failed try doubles the wait, up to one minute, so a camera that is off for a while is asked once a minute until it comes back. When it does, the devices show **Connected** again.

A camera whose firmware cannot send any of the detections its devices need is not asked again at all, because asking more often will not change its firmware. Its devices say so instead.

While a connection stays open and quiet, the plugin still marks its devices as heard from every five minutes. That way Indigo, and anything that watches for devices that have gone silent, can tell a quiet camera from a lost one.

## Detections today

Each time the camera reports a detection starting, the plugin adds one to **Detections today** and records the time in **Last detection**. The count goes back to 0 at midnight, and it is still right after the plugin restarts, because it keeps the date of the last detection alongside it.

## What goes in the log

Every detection is written to the plugin's own log file as a line such as `Drive Person: DETECTED` and `Drive Person: clear`. The file is `Logs/com.clives.indigoplugin.dahuaevents/plugin.log` inside Indigo's folder.

The Indigo Event Log only has what you might need to act on: a camera that drops out, a detection that cannot work and why, the devices created, and the results of the checks you run from the menu. Tick **Log detections to the Indigo event log** in the plugin's settings to see every detection there as well.
