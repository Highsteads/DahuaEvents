---
title: Settings
nav_order: 7
---

# Settings

## The plugin's settings

Open these with **Plugins → DahuaEvents → Configure**. They apply to every camera.

| Setting | What it does |
|---|---|
| **Camera username** | The username the plugin uses to sign in to every camera. The account needs to be allowed to read the camera's events and settings. |
| **Camera password** | The password for that account. The box hides it while you type, but Indigo does not store it securely. If you would rather keep it out of Indigo's settings, use the shared file described below. |
| **Detection hold (seconds)** | How long a device stays on after the camera stops reporting, 20 to start with. This is what makes one person walking past run a trigger once rather than a dozen times. Each camera can have its own, set in its devices. |
| **Log detections to the Indigo event log** | Every detection is always written to the plugin's own log file. Tick this to see each one in the Indigo Event Log as well. It is off to start with, because two cameras put about fifty lines a day there saying what the devices already show. Warnings and errors always appear in the Event Log whichever way this is set. |

A change here takes effect as soon as you click **Save**. A new username or password reconnects every camera with it and checks each device again, and a new hold reaches every device that does not have its own.

### Keeping the username and password in one file

If you run several of my plugins, you can keep the camera username and password in one shared file instead of typing them into the Configure dialog. The file is called `IndigoSecrets.py` and lives in `/Library/Application Support/Perceptive Automation/`.

The plugin comes with a template for it, `IndigoSecrets_example.py`. To find it, Control-click `DahuaEvents.indigoPlugin` in Indigo's `Plugins` folder, choose **Show Package Contents**, and open `Contents/Server Plugin`. It is also [on GitHub](https://github.com/Highsteads/DahuaEvents/blob/main/DahuaEvents.indigoPlugin/Contents/Server%20Plugin/IndigoSecrets_example.py).

If you do not have the file yet, copy the template into that folder and rename the copy `IndigoSecrets.py`. If you already have the file, add the two lines to it instead. Either way, put your own username and password between the quotes:

```python
DAHUA_USER = "your camera username"
DAHUA_PASS = "your camera password"
```

When the file has them, they are used, whatever the Configure dialog says. The plugin reads the file only when it starts, so after changing it choose **Plugins → DahuaEvents → Reload**. This plugin needs nothing else from the file.

## Each camera's settings

These are in the camera dialog you fill in when you add a camera, which opens when you create a **New Device** with **Type** set to **DahuaEvents**.

| Setting | What it does |
|---|---|
| **Camera name** | The name the devices are given, followed by what each one detects — `Drive` gives `Drive Person` and `Drive Vehicle`. Accented letters and the characters `&`, `<`, `>`, `"` and `'` become question marks. |
| **Camera address** | The camera's network address, such as `192.168.1.64`, or a name your network knows it by. |
| **Hold override (seconds)** | A hold for this camera's devices only. Leave it blank to use the plugin's **Detection hold**. |
| **Check This Camera** | Asks the camera what it can do, shows the answer under **Camera reports**, and ticks each detection that will work. The [Your cameras](cameras.md) page explains the answers. |
| **People**, **Vehicles**, **Tripwire (line crossed)**, **Intrusion (zone entered)**, **Doorbell button** | One device is created for each one ticked. People and Vehicles are ticked to start with. |

If you open the camera dialog again from one of the camera's devices, its name and address are already filled in, and ticking another detection adds a device for it. Unticking one does not delete its device — delete that in Indigo if you no longer want it.

## Each device's settings

Each device also has its own settings, which you reach by editing the device.

| Setting | What it does |
|---|---|
| **Camera address** | The address of the camera this device listens to. |
| **Detects** | What this device listens for: **People**, **Vehicles**, **Tripwire (line crossed)**, **Intrusion (zone entered)** or **Doorbell button pressed**. |
| **Hold override (seconds)** | A hold for this device only. Leave it blank to use the plugin's **Detection hold**. A change here takes effect when you click **Save**. |

Changing **Camera address** or **Detects** restarts the device, and the plugin checks the camera again straight away.
