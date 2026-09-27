# Dahua Events for Indigo

**Turn a Dahua camera's own person and vehicle detection, and a video doorbell's button, into Indigo sensors.**

**Version:** 1.19 | **Author:** CliveS & Claude | **Needs:** Indigo 2022.1 or later

**[Read the full guide](https://highsteads.github.io/DahuaEvents/)** — setting up, what everything means, and what to do when something goes wrong.

---

## What it does

Most Dahua cameras from the last few years work out for themselves whether they are looking at a person or a vehicle. This plugin lets [Indigo](https://www.indigodomo.com) hear those detections straight from the camera, over your home network, so there is no video analysis server, no subscription, nothing studying pictures on the Indigo Mac, and no video leaving the house.

- **Makes a sensor for each detection,** named after the camera, such as `Drive Person` and `Drive Vehicle`, which switches on when the camera sees one. Use it in triggers, notifications and control pages like any other device.
- **Runs a trigger once per visit.** A camera reports many short detections as someone moves about, so the sensor stays on until 20 seconds after the last one. You can change that for every camera or for one.
- **Works with older cameras too,** through a tripwire or intrusion zone drawn in the camera's own settings.
- **Tells you when a doorbell is pressed,** on a Dahua or Amcrest video doorbell.
- **Asks the camera what it can do** before you add it, and ticks the detections that will work.
- **Says plainly when a detection cannot work,** and why — the firmware cannot do it, it is switched off in the camera, or no tripwire or zone is drawn.
- **Keeps the Event Log quiet.** Each detection goes to the plugin's own log file unless you ask for it in the Event Log as well.

## What it works with

| In Indigo | Your camera |
|---|---|
| **Person** and **Vehicle** | A Dahua camera with Smart Motion Detection, which most have from about 2022 onward |
| **Tripwire** and **Intrusion** | An older Dahua camera, with a tripwire or intrusion zone drawn in its own settings |
| **Pressed** | A Dahua or Amcrest video doorbell. I use it on an Amcrest AD110 |

The plugin uses one camera username and password for every camera.

## Installing

1. Go to the [Releases page](https://github.com/Highsteads/DahuaEvents/releases/latest) and download `DahuaEvents.indigoPlugin.zip`
2. Unzip the downloaded file — you will get `DahuaEvents.indigoPlugin`
3. Double-click `DahuaEvents.indigoPlugin` — Indigo will install it automatically

## Setting it up

1. Open **Plugins → DahuaEvents → Configure**, fill in the **Camera username** and **Camera password**, and click **Save**.
2. Create a **New Device** with its **Type** set to **DahuaEvents**. In the camera dialog, give the camera a name such as `Drive` and type in its network address — the four numbers, such as `192.168.1.64`, that its own web page or your router shows.
3. Click **Check This Camera**, which ticks the detections that will work, then click **Close**. You get one device for each ticked detection.
4. Walk in front of the camera, and the **Person** device should switch on.

The [full guide](https://highsteads.github.io/DahuaEvents/) goes through each step, explains what to switch on in the camera, and covers what to do if something does not work.

## What's new

**v1.19** — A new hold, camera username or password typed into **Configure** now takes effect when you click **Save**, with no reload, and so does a device's own hold. The plugin also comes with a template for the shared `IndigoSecrets.py` file.

**v1.18** — On a camera with both a Person and a Vehicle device, the second one to start never switched on, because the camera was only ever asked for the first one's detections. The plugin now asks for every device's detection, so Vehicle devices work. Adding a device to a camera later works straight away too, without a restart.

**v1.17** — Doorbells. A Dahua or Amcrest video doorbell can have a **Pressed** device, which switches on when somebody presses the button, so a press can run a trigger like any other detection. Tick **Doorbell button** when you add the doorbell.

Every version is listed in the [version history](https://highsteads.github.io/DahuaEvents/changelog.html).

## Authors & licence

Vibed into existence by **CliveS**, who knew what he wanted, argued until he got it, and tested it on a real house. Typed at inhuman speed by **Claude** (Anthropic), who mostly did as it was told.

© 2026 CliveS · [MIT licence](LICENSE) — copy it, fork it, bend it, break it, fix it, ship it. If it breaks, you get to keep both pieces.
