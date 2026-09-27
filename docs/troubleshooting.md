---
title: When something goes wrong
nav_order: 9
---

# When something goes wrong

Each section starts with what you see, then what it means and what to do.

## The Event Log says "No camera credentials yet"

The plugin has no camera username and password. Fill them in under **Plugins → DahuaEvents → Configure**, or in the shared file the [Settings](settings.md) page describes, then choose **Plugins → DahuaEvents → Reload**.

## A device shows "no rule drawn on the camera"

It is a Tripwire or Intrusion device, and the camera has no rule of that kind drawn.

- Open the camera's own web page, and under **Smart Plan** or **IVS** draw a tripwire or intrusion zone, set it to react to people or vehicles, and switch it on.
- Then select the device in Indigo and choose **Send Status Request**, and the plugin checks again.

## A device shows "switched off at the camera"

The camera can do this detection, but it is off in the camera's settings.

- For a Person or Vehicle device, switch on both **Motion Detection** and **Smart Motion Detection** in the camera. Smart motion detection does nothing while motion detection is off.
- For a Tripwire or Intrusion device, the rule is drawn but switched off. Switch it on in the camera.
- Then choose **Send Status Request** on the device.

The Event Log has a warning naming exactly what is off.

## A device shows "camera cannot emit this detection"

The camera's firmware cannot send this detection, whatever its settings.

- For people and vehicles on an older camera, add a tripwire or intrusion zone instead — the [Your cameras](cameras.md) page explains how.
- If you update the camera's firmware, choose **Plugins → DahuaEvents → Reload** so the plugin checks it again.
- For a doorbell device on an ordinary camera, delete the device — only a doorbell has a button to press.

## A device shows "Reconnecting"

The plugin cannot reach the camera, or has lost it, and is trying again, waiting no more than a minute between tries. The Event Log has a line saying why.

- Check the camera has power and is on the network — open its own web page from a browser.
- Check the address in the device matches the camera's current address. If the router has given the camera a new address, change it in the device's settings.
- Check the username and password work on the camera's own web page. Remember the plugin uses the same ones for every camera.

When the camera answers again, the device shows **Connected** by itself.

## A device shows Connected but never switches on

The plugin is hearing from the camera, but the camera is not reporting anything for this device.

- Walk in front of the camera, then have a look at the plugin's own log file, `Logs/com.clives.indigoplugin.dahuaevents/plugin.log` inside Indigo's folder. A `DETECTED` line means the camera reported you and the device did switch on.
- For people and vehicles, check smart motion detection has people or vehicles ticked, and that its detection area covers where you walked.
- For a tripwire or zone, check the rule reacts to people or vehicles and that you crossed the line or entered the area.

## Detections today is higher than the number of visitors

That is expected. The camera often reports several detections starting as one person moves about, and each one counts. The device itself still switches on only once per visit.

## Turning a device on by hand does nothing

These devices report what the camera saw, so they cannot be switched by hand, and the Event Log says so. **Send Status Request** is the one command they take.

## A change to the hold made no difference

The plugin takes up a new hold when it restarts. Choose **Plugins → DahuaEvents → Reload**.

## Probe a Camera says UNSUPPORTED, but the device works

**Probe a Camera...** and **Test All Cameras** only ask about people and vehicles. A camera used for a tripwire, an intrusion zone or a doorbell shows **[UNSUPPORTED]** there even when those devices work. The [plugin menu](plugin-menu.md) page explains more.

## Still stuck?

Choose **Plugins → DahuaEvents → Test All Cameras**, copy the lines it writes to the Event Log, and post them on the [Indigo forum](https://forums.indigodomo.com) with a description of what you see. You can also [raise an issue on GitHub](https://github.com/Highsteads/DahuaEvents/issues).
