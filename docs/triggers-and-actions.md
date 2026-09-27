---
title: Triggers and actions
nav_order: 6
---

# Triggers and actions

The plugin adds no actions or triggers of its own. Its devices are ordinary Indigo sensors, so you use Indigo's own **Device State Changed** trigger with them, the same as any other sensor.

## Running something when a camera sees someone

1. Create a new trigger and set its type to **Device State Changed**.
2. Choose the device, such as **Drive Person**.
3. Set it to run when the device turns on.
4. Add whatever you want to happen — a notification, a light, an announcement.

Because the device stays on until the hold runs out, one person walking up the drive runs the trigger once, not once for every report the camera sends.

To run something when the drive has been clear for a while, set it to run when the device turns off instead. The device switches off once the camera has seen nothing for the hold time.

## Running something when the doorbell is pressed

Do the same with the doorbell's **Pressed** device, set to run when it turns on.

## Knowing when a camera stops working

Each device also has a **Stream state**, which you can pick in a Device State Changed trigger. A trigger on the stream state changing to **Reconnecting**, for example, tells you when a camera has dropped off the network. The [Your devices](devices.md) page lists every stream state.

## Control pages

On a control page, each device can show its on or off state, **Last detection**, **Detections today** and **Stream state**.

## Actions on the devices

These devices are read-only — they report what the camera saw. **Turn On**, **Turn Off** and **Toggle** write a line to the Event Log saying so and change nothing. **Send Status Request** asks the camera again whether the detection can work, which is useful after changing something in the camera's settings.
