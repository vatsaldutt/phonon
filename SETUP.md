# Ceiling Fan Automation — macOS Setup Guide

## Overview

Since Docker on macOS can't pass through Bluetooth, the setup uses:
- **ESP32 board** (~$8) — plugged into any USB charger near the fan; handles all BLE
- **Home Assistant Core** — installed natively on your Mac (no Docker); the API layer
- **fan_api.py + gui.py** — your personal control software on the Mac

```
gui.py / AI assistant
        ↓
    fan_api.py
        ↓  (HTTP REST)
Home Assistant Core (Mac, localhost:8123)
        ↓  (ESPHome API over WiFi)
    ESP32 board
        ↓  (BLE advertisements)
    Ceiling Fan
```

---

## What to Buy

One ESP32-WROOM-32 DevKit V1 board. Search Amazon or AliExpress for
**"ESP32 DevKit V1"** or **"ESP32 38-pin"**. Any of these work:
- Espressif ESP32-DevKitC (~$10 on Amazon, arrives fast)
- Generic 38-pin ESP32 board (~$5–8)
- AZ-Delivery ESP32 Dev Board

You also need a **micro-USB cable** that carries data (not just power) — most
phone charger cables work but some cheap ones are charge-only.

---

## Step 1 — Install Homebrew and Python

Open Terminal and paste:

```bash
# Install Homebrew if you don't have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python 3.12+
brew install python@3.12

# Confirm version (should say 3.12 or higher)
python3 --version
```

---

## Step 2 — Generate an API Encryption Key

```bash
python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
```

Copy the output. Open **secrets.yaml** and fill in:
- `wifi_ssid` — your WiFi network name
- `wifi_password` — your WiFi password
- `api_encryption_key` — paste the key you just generated

Keep secrets.yaml private (don't commit it to git).

---

## Step 3 — Install ESPHome and Flash the ESP32

```bash
pip3 install esphome
```

Plug the ESP32 into your Mac via micro-USB. Then from your project folder:

```bash
esphome run esp32-proxy.yaml
```

ESPHome will:
1. Download dependencies and compile the firmware (~3–5 min first time)
2. Detect the ESP32's serial port and ask you to confirm — press Enter
3. Flash the firmware
4. Open a live log stream

You'll see the ESP32 connect to your WiFi and print its IP address. Once you
see `[I][ble_adv_proxy:...]` in the logs, it's working. Press Ctrl+C to exit
the log view (the ESP32 keeps running).

**If the serial port isn't detected:** You may need the CP2102 or CH340 USB
driver. Download from:
- CP2102: https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers
- CH340: https://github.com/adrianmihalko/ch340g-ch34g-ch34x-mac-os-x-driver

After the first USB flash, all future updates can be done over WiFi (OTA):
```bash
esphome run esp32-proxy.yaml --device fan-ble-proxy.local
```

---

## Step 4 — Install Home Assistant Core on macOS

HA Core runs as a Python process directly on your Mac — no VM, no Docker.

```bash
# Create a dedicated Python virtual environment
python3 -m venv ~/homeassistant
source ~/homeassistant/bin/activate

# Install Home Assistant Core
pip3 install homeassistant

# Start it (this also creates the config directory at ~/.homeassistant)
hass --open-ui
```

Wait about **2 minutes** for first-time startup (it downloads integrations).
Then open **http://localhost:8123** in your browser.

Complete the onboarding wizard: create your account, set your timezone and
home location.

**To start HA in future sessions:**
```bash
source ~/homeassistant/bin/activate
hass
```

---

## Step 5 — Add the ESP32 to Home Assistant

HA should auto-discover the ESP32 via mDNS. Check **Settings → Devices &
Services** — you should see a notification about a new ESPHome device.
Click **Configure**, enter the API encryption key from your secrets.yaml,
and confirm.

If it doesn't appear automatically:
- Go to **Add Integration**, search **ESPHome**
- Enter the host: `fan-ble-proxy.local` (or the IP address from Step 3)
- Enter the encryption key from secrets.yaml

---

## Step 6 — Install HACS

HACS is the community store that makes installing ha-ble-adv one click.

**6a.** Stop Home Assistant (Ctrl+C in the terminal where `hass` is running).

**6b.** With the venv still active:
```bash
source ~/homeassistant/bin/activate
cd ~/.homeassistant
wget -O - https://get.hacs.xyz | bash -
```

**6c.** Restart Home Assistant:
```bash
hass
```

**6d.** Go to **Settings → Devices & Services → Add Integration**, search
**HACS**, and click it.

**6e.** HACS shows a short code (like `ABCD-1234`) and a link to
`github.com/login/device`. Open that link, paste the code, and authorize.
Come back to HA and click through the confirmation.

HACS now appears in the left sidebar.

---

## Step 7 — Install ha-ble-adv

**7a.** Click **HACS** in the sidebar.

**7b.** Click the **⋮** menu (top-right) → **Custom repositories**.

**7c.** Paste:
```
https://github.com/NicoIIT/ha-ble-adv
```
Set category to **Integration** → **Add**.

**7d.** Search **BLE ADV** inside HACS, click the result, then **Download**.

**7e.** Restart Home Assistant (Ctrl+C, then `hass` again).

---

## Step 8 — Configure the Fan

This is the discovery step: HA listens for the BLE signal your
ApplianceSmart app sends and figures out the protocol.

> Your ApplianceSmart app must already be paired with the fan.
> You do NOT need to redo the "turn on within 5 seconds" step here —
> that was a one-time hardware pairing. If the app has lost connection
> to the fan, redo that pairing first, then come back here.

**8a.** Go to **Settings → Devices & Services → Add Integration**.
Search **BLE ADV Ceiling Fan / Lamps** and click it.

**8b.** When asked for the Bluetooth adapter, select your **ESP32 proxy**
(it will appear in the list by its name "Fan BLE Proxy").

**8c.** Choose **Duplicate Config from App** — HA shows a "Listening…"
screen.

**8d.** Open **ApplianceSmart** on your phone and press any fan control
button. HA detects the BLE advertisement in a few seconds.

**8e.** HA runs a blink test — the fan/light blinks briefly. Confirm it,
name the device "Ceiling Fan", and finish.

---

## Step 9 — Find Your Entity IDs

Go to **Settings → Devices & Services → Entities**, search your fan name.
Note the exact IDs — something like:

| Entity | Example ID |
|--------|-----------|
| Fan    | `fan.ceiling_fan` |
| Light  | `light.ceiling_fan_light` |

---

## Step 10 — Create a Long-Lived Access Token

**10a.** Click your **profile icon** (bottom-left of the HA sidebar).

**10b.** Scroll to **Long-Lived Access Tokens** → **Create Token**.

**10c.** Name it `fan-api` and copy the token — **it's only shown once**.

---

## Step 11 — Install Python Dependencies and Run the GUI

Open a **new** terminal tab (separate from the one running `hass`):

```bash
cd /path/to/your/project/folder
pip3 install -r requirements.txt

export HA_TOKEN="eyJhbGciOiJIUzI1NiIs..."   # your token from step 10
export FAN_ENTITY="fan.ceiling_fan"           # from step 9
export LIGHT_ENTITY="light.ceiling_fan_light" # from step 9
```

Test the API connection:
```bash
python3 fan_api.py
```
This should print the fan and light state as JSON.

Then launch the GUI:
```bash
python3 gui.py
```

---

## Making Environment Variables Permanent

Add these to your `~/.zshrc` (or `~/.bashrc`) so you don't re-export every time:

```bash
export HA_TOKEN="eyJhbGciOiJIUzI1NiIs..."
export FAN_ENTITY="fan.ceiling_fan"
export LIGHT_ENTITY="light.ceiling_fan_light"
```

Then `source ~/.zshrc`.

---

## Placement Tips for the ESP32

- Keep the ESP32 within ~10 metres of the fan with no thick concrete walls between them
- If the ESP32 is marginal range, `use_max_tx_power: true` in esp32-proxy.yaml
  (already enabled) helps; you can also just move the USB power brick closer
- The ESP32 can run from any USB-A charger, phone charger, or USB hub; it doesn't
  need to stay plugged into your Mac after the initial flash

---

## Troubleshooting

**ESP32 not detected at flash time**
Install the correct USB-serial driver (CP2102 or CH340, linked in Step 3).

**ESP32 connects to WiFi but doesn't appear in HA**
Make sure your Mac and ESP32 are on the same WiFi network (not guest vs main).
Try using the ESP32's IP address directly instead of `fan-ble-proxy.local`.

**"error" shown in the GUI**
The status bar shows the actual error. Usually a wrong token or entity ID.

**Fan doesn't respond to HA commands**
In **Developer Tools → Services**, try calling `fan.turn_on` with your entity
ID manually. If the physical fan reacts, the rest of the stack works and the
issue is in fan_api.py (check entity IDs in your env vars). If it doesn't
react, re-run Step 8 (discovery).

**HACS download fails**
Make sure HA is fully restarted after the `wget` install. HACS sometimes needs
two restarts to appear in Add Integration.

---

## Using fan_api.py in Your AI Assistant

```python
import fan_api

fan_api.turn_on()
fan_api.set_speed(60)           # 0–100 %
fan_api.turn_off()

fan_api.light_on()
fan_api.light_on(brightness_pct=80)
fan_api.light_on(color_temp_kelvin=2700)   # warm
fan_api.light_on(color_temp_kelvin=6500)   # cool
fan_api.light_on(brightness_pct=50, color_temp_kelvin=4000)
fan_api.light_off()

state = fan_api.fan_state()    # returns dict with 'state', 'attributes'
state = fan_api.light_state()
```

The ESP32, ESPHome, HACS, ha-ble-adv, and BLE are all invisible to the caller.
