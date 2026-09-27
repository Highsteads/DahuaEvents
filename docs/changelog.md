---
title: Version history
nav_order: 10
---

# Version history

The newest version is at the top.

## 1.20 — 27 September 2026

**A red message now stays until the problem is fixed.** When a detection cannot work, for instance because no tripwire is drawn on the camera, the device shows a short red message saying why. Indigo wipes that message whenever a plugin updates anything else on the device, and this plugin updates its devices every night at midnight, when the count of detections goes back to 0, and whenever the camera's connection drops. So the message quietly disappeared and the device looked healthy, including to any plugin that watches for devices in trouble. Now the message only goes when the plugin sees the problem is gone: the camera checks out when you choose **Send Status Request**, the connection comes back for a device that can work, or the device restarts.

**Reconnecting only while the camera is really away.** A device that cannot work showed **Reconnecting** when the camera's connection dropped, and then stayed on it after the connection came back, until you chose **Send Status Request**. It now goes back to what it said before, such as **No rule drawn on the camera**, as soon as the camera is back.

## 1.19.1 — 27 September 2026

Since 1.18 the plugin swaps a camera's connection while it runs, when a second device on the camera needs something the first did not. The old connection's last word, "stopped", was being written to every device on that camera, and Indigo refused it and logged an error for each one. It is now ignored, and the new connection reports for itself.

## 1.19 — 27 September 2026

**Settings take effect when you click Save.** Changing the hold, the camera username or the camera password in **Configure** used to do nothing until the plugin was reloaded. Now a new hold reaches every device straight away, apart from any device with its own hold, which keeps it. A change to a device's own hold, in that device's settings, also works as soon as you click Save. A new username or password reconnects every camera with it and checks each device again, so a camera that turned the old login away comes right without a reload. The shared `IndigoSecrets.py` file still needs a reload after a change, because the plugin reads it only when it starts.

**A template for the shared file.** The plugin now comes with `IndigoSecrets_example.py`, holding the two lines it reads with empty values, ready to copy and fill in. The Settings page says where to find it.

## 1.18 — 27 September 2026

**Vehicle devices now switch on.** On a camera with both a Person and a Vehicle device, the plugin opened the camera's connection when the first device started and asked only for that device's detection. The second device joined the same connection without its detection being added, so it never switched on. On the house this was written for, the Drive camera's Person device switched over a thousand times in September and its Vehicle device not once. The plugin now reopens the connection whenever a device needs a detection it is not asking for, so every device on a camera works, and a device added later works without a restart. Show Plugin Info now says how many cameras are in use, in place of an old line about the plugin's early stages.

## 1.17 — 22 September 2026

**Doorbells.** A Dahua or Amcrest video doorbell can have a **Pressed** device, which switches on when somebody presses the button and off again once the hold runs out, so a press can run a trigger like any other detection. Tick **Doorbell button** when you add the doorbell, or choose **Doorbell button pressed** in an existing device's settings. The button reports itself as an unanswered call, and at least the Amcrest AD110 sends that without listing it among the events it offers, so the plugin also goes by the doorbell's model name. An ordinary camera given a doorbell device says it is not a doorbell.

## 1.16 — 11 September 2026

The plugin carries a note of where its code lives on GitHub, the same way other Indigo plugins do. Nothing else changed.

## 1.15 — 6 September 2026

A tripwire or intrusion rule that is drawn but switched off at the camera now shows **switched off at the camera**. Before, it showed **no rule drawn on the camera**, as if it had never been drawn.

## 1.14 — 6 September 2026

A tripwire or intrusion zone that was drawn and switched on could still show **no rule drawn on the camera**. Cameras record which kind of rule each one is in one of two places, depending on their firmware, and the plugin only looked in the first. It now looks in both.

## 1.13 — 6 September 2026

A device that could never work — no rule drawn, switched off, or not supported — went back to showing **Connected** whenever the camera's connection came back, because the devices on one camera share it. Each device now keeps its own answer until **Send Status Request** or a restart checks again.

## 1.12 — 6 September 2026

- A camera name or hold containing `&`, `<`, `>`, `"` or `'` stopped the devices being created. Those characters now become question marks.
- A Tripwire or Intrusion device with no rule drawn could not show that, because Indigo refused the value. It now shows **No rule drawn on the camera**.

## 1.11 — 6 September 2026

Each detection now goes to the plugin's own log file rather than the Indigo Event Log, which it had been filling with about fifty lines a day. The devices change exactly as before, and a new setting, **Log detections to the Indigo event log**, puts the lines back. Problems with a camera's connection still go to the Event Log.

## 1.10 — 3 September 2026

A camera with nothing to report for hours no longer looks as if it has stopped. The plugin now marks its devices as heard from every five minutes while the connection stays open, so anything that watches for silent devices can tell a quiet camera from a lost one. A connection that really has gone quiet is still caught as quickly as before.

## 1.9 — 1 September 2026

Adding a camera failed with a message about an illegal character, because of a symbol in the summary 1.8 added. Everything the plugin writes to Indigo is now plain letters, numbers and punctuation.

## 1.8 — 1 September 2026

The camera dialog has a **Check This Camera** button, which asks the camera what it can do and ticks the detections that will work.

## 1.7 — 1 September 2026

- **Send Status Request** checks the camera again, and **Turn On** and **Turn Off** say the device is read-only. Before, Indigo dropped them with an error.
- **Detections today** goes back to 0 at midnight. Before, it only reset at the next detection, so it showed yesterday's count all morning.

## 1.6 — 1 September 2026

- **Tripwires and intrusion zones**, for older cameras without smart motion detection.
- The plugin checks that a rule is actually drawn and switched on, and says so plainly when it is not.
- The camera dialog asks which detections you want, so you only get the devices you use.

## 1.5 — 1 September 2026

The plugin stops in under a second when Indigo restarts it or you install a new version. With five cameras it used to take about 26 seconds, long enough for Indigo to force it to quit.

## 1.4 — 1 September 2026

A first fix for the plugin having to be forced to quit when Indigo restarted it or a new version was installed. Version 1.5 finished the job.

## 1.3 — 1 September 2026

A camera sending detections faster than the plugin could handle them could have left every device switched on, including those on other cameras. The plugin now handles a limited number at a time, so the others carry on as normal.

## 1.2 — 1 September 2026

Every log line starts with the time to the thousandth of a second, the same as my other plugins.

## 1.1 — 1 September 2026

- **Live detections.** Devices switch on and off as the cameras report people and vehicles.
- One dialog per camera creates its Person and Vehicle devices together.
- The **hold**, set for every camera or for one, so one person walking past runs a trigger once.
- **Test All Cameras** runs in the background, so a slow camera cannot leave the menu waiting.

## 1.0 — 1 September 2026

The first version. It checks whether a camera can really send people and vehicle detections from the list of events the camera offers, rather than trusting its settings, and adds **Probe a Camera...** and **Test All Cameras** to the Plugins menu.
