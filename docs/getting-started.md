---
title: Getting started
nav_order: 2
---

# Getting started

This takes a few minutes per camera.

## What you need

- Indigo 2022.1 or later, on a Mac on the same home network as your cameras.
- One or more Dahua cameras, or a Dahua or Amcrest video doorbell. The [Your cameras](cameras.md) page says which kinds work and what to switch on in each.
- The **network address** of each camera. This is the set of four numbers separated by dots, such as `192.168.1.64`, that the camera's own web page, its app or your router's list of connected devices shows. A name your network knows the camera by works too.
- A **username and password** for the cameras. The plugin uses one username and password for every camera, so each camera needs an account with the same details. The account needs to be allowed to read the camera's events and settings, which an administrator account is.

It helps to ask your router to keep giving each camera the same address, which most routers call a **reserved address** or **DHCP reservation**. The plugin finds a camera by its address and has no way to follow it to a new one.

## 1. Install the plugin

1. Go to the [Releases page](https://github.com/Highsteads/DahuaEvents/releases/latest) and download `DahuaEvents.indigoPlugin.zip`
2. Unzip the downloaded file — you will get `DahuaEvents.indigoPlugin`
3. Double-click `DahuaEvents.indigoPlugin` — Indigo will install it automatically

Indigo asks whether to enable the plugin. Say yes.

## 2. Give it the camera username and password

Open **Plugins → DahuaEvents → Configure**, fill in **Camera username** and **Camera password**, and click **Save**.

If you run several of my plugins you can keep these in one shared file instead, and the [Settings](settings.md) page explains how. Leave everything else as it is to start with.

## 3. Add a camera

1. In Indigo, create a **New Device** and set its **Type** to **DahuaEvents**. The camera dialog opens.
2. Fill in **Camera name** — a short name such as `Drive`. The devices are named after it, so `Drive` gives `Drive Person` and `Drive Vehicle`. Keep to plain letters, numbers and spaces, because the plugin changes accented letters and the characters `&`, `<`, `>`, `"` and `'` into question marks.
3. Fill in **Camera address** with the camera's network address.
4. Click **Check This Camera**. The plugin asks the camera what it can do, shows the answer under **Camera reports**, and ticks each detection that will work. The [Your cameras](cameras.md) page explains the answers.
5. Tick the detections you want, if the check did not already tick them: **People**, **Vehicles**, **Tripwire (line crossed)**, **Intrusion (zone entered)** or **Doorbell button**. People and Vehicles are ticked to start with.
6. Leave **Hold override (seconds)** blank to use the plugin's 20 seconds.
7. Click **Close**. The plugin creates one device for each ticked detection, and the Event Log has a line for each one it created.

Repeat this for each camera.

## 4. Check it works

Within a few seconds each new device shows **Connected** as its stream state. If a device shows a red message instead, such as **no rule drawn on the camera**, the [When something goes wrong](troubleshooting.md) page explains it.

Now walk in front of the camera. The **Person** device should switch on, and switch off again about 20 seconds after the camera stops seeing you.

If you want to check a camera before adding it, or after a firmware update, **Plugins → DahuaEvents → Probe a Camera...** asks it about people and vehicles without creating anything. The [plugin menu](plugin-menu.md) page explains it.
