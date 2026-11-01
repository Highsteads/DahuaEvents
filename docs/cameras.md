---
title: Your cameras
nav_order: 3
---

# Your cameras

Dahua cameras have recognised people and vehicles in more than one way over the years, and a doorbell reports its button in a way of its own. This page explains each, and what the camera needs switched on before the plugin can hear it.

The plugin talks to each camera on its ordinary web port, the one its own web page uses.

## People and vehicles — smart motion detection

Most Dahua cameras from about 2022 onward have **Smart Motion Detection**, which works out on the camera whether movement is a person or a vehicle. The plugin turns that into a **Person** device and a **Vehicle** device.

In the camera's own settings, switch on both:

- **Motion Detection**, and
- **Smart Motion Detection**, with people and vehicles ticked.

Both are needed, because smart motion detection sorts the camera's ordinary motion events rather than replacing them. With motion detection off, the camera sends nothing however smart motion detection is set, and it switches smart motion detection off again by itself.

The plugin checks this from the list of events the camera says it can send, not from the setting alone, because a camera that cannot do smart motion detection will still accept the setting and report it as on.

## Tripwire and intrusion — for older cameras

Cameras from before about 2022 have no smart motion detection, and the check reports people and vehicles as **not supported**. Most of them offer two older rules instead, which can also be set to react only to people or vehicles:

- **A tripwire** — a line drawn across the picture, which reacts when something crosses it.
- **An intrusion zone** — an area drawn on the picture, which reacts when something enters it.

These report nothing until you draw one. In the camera's own web page, under **Smart Plan** or **IVS**, draw a tripwire or an intrusion zone, set it to react to people or vehicles, and switch it on. Then tick **Tripwire (line crossed)** or **Intrusion (zone entered)** when you add the camera.

The plugin checks that a rule of that kind is actually drawn and switched on, not only that the camera could have one, and says so if it is not.

## A video doorbell's button

A Dahua or Amcrest video doorbell can have a **Pressed** device, which switches on when somebody presses the button. Tick **Doorbell button** when you add the doorbell. I use it on an Amcrest AD110.

The doorbell reports a press as a call nobody has answered. At least the AD110 sends that without listing it among the events it offers, so the plugin also goes by the doorbell's model name — any model whose name begins AD1, AD4, AD5, DB or VTO counts as a doorbell. An ordinary camera given a doorbell device is reported as **not supported** rather than being left to wait for a press that will never come.

## Cameras on an NVR

A Dahua NVR can report the detections of all the cameras plugged into it, using the NVR's address. It says which channel each detection came from, so fill in **NVR channel** with that camera's channel number, as the NVR numbers it (1 is the first). Add the camera once per channel, with a different **Camera name** each time, such as `Drive` for channel 1 and `Garden` for channel 2.

- **With a channel**, the device hears only that channel, and **Check This Camera** and **Send Status Request** look at that channel's settings and drawn rules.
- **Blank**, the device hears every channel. It switches on when any channel sees something, and off only after every channel that started has finished.
- Two devices for the same address and detection cannot hear the same channel, so a device for every channel cannot sit alongside one for channel 2.

I have not been able to test this on an NVR. It follows the way a single camera numbers its one channel: the first channel is 0 on the event connection and in the camera's settings. If a device for channel 1 reacts to the second camera, please report it. In the meantime, connecting to each camera's own address, with **NVR channel** blank, works as it always has.

## What Check This Camera tells you

The **Check This Camera** button in the camera dialog asks the camera about all five detections at once and shows one line, such as:

`People: yes | Vehicles: yes | Tripwire: no rule drawn | Intrusion: no rule drawn | Doorbell: not supported`

| Answer | What it means |
|---|---|
| **yes** | This detection will work. The plugin ticks it for you. |
| **off at the camera** | The camera can do it, but it is switched off in the camera's settings — motion detection or smart motion detection for people and vehicles, or the rule itself for a tripwire or zone. |
| **no rule drawn** | The camera can do tripwires or intrusion zones, but none of that kind is drawn. |
| **not supported** | The camera's firmware cannot send this detection, whatever its settings. |
| **could not tell** | The camera did not answer, or would not accept the username and password. |

The Event Log also has one line per detection giving the camera's own reason.

The check never unticks a box you have ticked yourself, so you can add a device ahead of changing the camera. That device shows why it cannot work yet, and starts working once the camera is set up.
