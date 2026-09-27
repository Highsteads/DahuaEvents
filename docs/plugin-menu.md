---
title: The plugin menu
nav_order: 8
---

# The plugin menu

These are under **Plugins → DahuaEvents**.

| Menu item | What it does |
|---|---|
| **Probe a Camera...** | Asks one camera, by its address, whether it can send people and vehicle detections and has them switched on. It needs no device, so it is the quickest check after swapping a camera or updating its firmware. The answer goes to the Event Log, with the camera's firmware version. |
| **Test All Cameras** | Asks every camera the plugin has devices for the same question, one after another, and writes a line for each to the Event Log, then a total such as `Probe complete: 4 capable, 1 unsupported`. It runs in the background, so the menu does not wait for it. |
| **Show Plugin Info** | Writes the plugin's version, details of your Mac and Indigo, where the camera username and password came from, and the hold time to the Event Log. This is useful to include if you ask for help on the Indigo forum. |

**Probe a Camera...** and **Test All Cameras** also write the same details as **Show Plugin Info** first, so one copy from the Event Log has everything needed when asking for help.

## Reading the answers

Each camera gets one line in the Event Log, starting with one of these:

| Starts with | What it means |
|---|---|
| **[OK]** | The camera can send people and vehicle detections, and both motion detection and smart motion detection are on. |
| **[OFF]** | The camera can do it, but motion detection or smart motion detection is switched off in its settings. The line says which. |
| **[UNSUPPORTED]** | The camera's firmware cannot send people and vehicle detections, however it is set up. |
| **[UNREACHABLE]** | The camera did not answer — a wrong address, a wrong username or password, or the camera is off. |

These two items only ask about people and vehicles. An older camera used for a tripwire or intrusion zone, or a doorbell, shows **[UNSUPPORTED]** here even when its own devices work. To check those, use **Check This Camera** in the camera dialog, or look at the device's **Stream state**.
