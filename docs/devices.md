---
title: Your devices
nav_order: 4
---

# Your devices

Each detection you tick for a camera becomes one Indigo sensor device, and the devices for one camera are created together as a group.

| Device name | Switches on when |
|---|---|
| *camera name* **Person** | the camera sees a person |
| *camera name* **Vehicle** | the camera sees a vehicle |
| *camera name* **Tripwire** | something crosses the tripwire drawn on the camera |
| *camera name* **Intrusion** | something enters the intrusion zone drawn on the camera |
| *camera name* **Pressed** | somebody presses the doorbell button — a camera named `Doorbell` gives `Doorbell Pressed` |

You can rename the devices freely. If you delete one and create it again, Indigo treats it as a new device, so any trigger, control page or script that used the old one has to be pointed at the new one.

## What each device shows

| Shown as | What it means |
|---|---|
| **On / Off** | On while the camera is seeing what the device is for, and for the hold time afterwards, 20 seconds unless you change it. This is what the device list shows. |
| **Last detection** | The date and time the camera last reported a detection starting, such as `2026-09-27 14:05:31`. |
| **Detections today** | How many times today the camera has reported a detection starting. A person moving about in front of the camera can count more than once. It goes back to 0 at midnight. |
| **Last detection day** | The date of the last detection. |
| **Stream state** | Whether the plugin is hearing from the camera, and if not, why not — see below. |

## Stream state

| Stream state | What it means |
|---|---|
| **Connected** | The plugin is listening to the camera, and this detection will work. |
| **Reconnecting** | The plugin has lost the camera, or could not reach it when the device started, and is trying again. |
| **Unsupported** | The camera's firmware cannot send this detection, or the device has no camera address. |
| **No rule drawn on the camera** | A tripwire or intrusion device, and no rule of that kind is drawn on the camera. |
| **Disabled on camera** | The camera can do this, but it is switched off in the camera's settings. |

When a detection cannot work, the device also shows a short red message in the device list saying why:

| Red message | What to do |
|---|---|
| **no rule drawn on the camera** | Draw a tripwire or intrusion zone in the camera's own settings. |
| **switched off at the camera** | Switch on motion detection and smart motion detection, or the tripwire or zone rule, in the camera's settings. |
| **camera cannot emit this detection** | This camera cannot do it. For people and vehicles on an older camera, try a tripwire or intrusion zone instead. |
| **no camera address configured** | Open the device's settings and fill in **Camera address**. |

The message stays until the plugin sees the problem is fixed, so it does not disappear overnight or when the camera's connection drops. Once you have changed the camera, select the device and choose **Send Status Request**, and the plugin checks again. The [Your cameras](cameras.md) page explains what each camera needs.

## The devices are read-only

These devices report what the camera saw, so they cannot be switched on or off by hand. **Turn On**, **Turn Off** and **Toggle** do nothing except write a line to the Event Log saying so. **Send Status Request** asks the camera again whether the detection can work.
