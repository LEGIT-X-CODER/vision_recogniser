# IoT Inventory Detection Device — Raspberry Pi 4 & 5

Automated product inventory detection using an **ultrasonic sensor**, **Pi Camera**, **Google Gemini Vision API**, and **Firebase**. Designed for headless operation on **Raspberry Pi 4 or 5** running **Raspberry Pi OS Lite (64-bit, Bookworm)**.

> [!IMPORTANT]
> **Pi 4 and Pi 5 use different WiFi stacks.** Pi 4 uses `wpa_supplicant + dhcpcd`, Pi 5 uses `NetworkManager`. Use the correct setup script for your hardware or WiFi power management fixes will silently fail and cause dropouts.

---

## Hardware

| Component | Connection | GPIO (BCM) |
|---|---|---|
| Pi Camera Module V1/V2 | CSI ribbon cable | — |
| HC-SR04 TRIG | GPIO pin | 23 |
| HC-SR04 ECHO | GPIO pin (via voltage divider) | 24 |
| Status LED | GPIO + 1kΩ resistor | 17 |
| Capture LED | GPIO + 1kΩ resistor | 27 |

> [!IMPORTANT]
> HC-SR04 Echo pin outputs 5V — use a voltage divider (1kΩ + 2kΩ) to bring it down to 3.3V for the Pi GPIO. LEDs need a 1kΩ resistor in series.

---

## OS Setup (Flash SD Card) — Same for Pi 4 & Pi 5

1. Download **Raspberry Pi Imager**: [raspberrypi.com/software](https://www.raspberrypi.com/software/)
2. Select OS: `Raspberry Pi OS (other)` → `Raspberry Pi OS Lite (64-bit)`
3. Click the **gear icon** and set:
   - **Hostname**: `raspberrypi`
   - **Username**: `pi` / set a password
   - **WiFi**: your SSID + password
   - **Enable SSH**: ✅ (password auth)
4. Flash → insert SD card → boot Pi

---

## Setup — Choose Your Hardware

| | Raspberry Pi 4 | Raspberry Pi 5 |
|---|---|---|
| WiFi stack | `wpa_supplicant + dhcpcd` | `NetworkManager` |
| Setup script | `pi_setup/setup_pi.sh` | `pi_setup/setup_rpi5.sh` |
| Watchdog script | `pi_setup/wifi_watchdog.sh` | `pi_setup/wifi_watchdog_rpi5.sh` |
| Power mgmt fix | `dhcpcd` hooks + `rc.local` | `nmcli` + `udev` rule |

---

## ── RASPBERRY PI 4 SETUP ───────────────────────────────────

### Connect from Windows

```powershell
ssh pi@<PI_IP_ADDRESS>
# e.g. ssh pi@192.168.137.107
```

---

### STEP 1 — Transfer Codebase (Windows PowerShell)

```powershell
scp -r "C:\Users\<you>\Desktop\vision_recogniser" pi@<PI_IP>:/home/pi/
```

---

### STEP 2 — Run One-Shot Setup Script

SSH into the Pi and run:

```bash
cd /home/pi/vision_recogniser
sudo bash pi_setup/setup_pi.sh
```

This handles everything: packages, venv, WiFi config, power management, services, SSH, permissions, and reboots.

---

### Manual Steps (Pi 4) — if you prefer step-by-step

<details>
<summary>Click to expand Pi 4 manual steps</summary>

#### STEP 2a — System Update

```bash
sudo apt-get update && sudo apt-get upgrade -y
```

#### STEP 2b — Install System Packages

> [!NOTE]
> On **Raspberry Pi OS Bookworm/Trixie** (latest):
> - Camera command is `rpicam-hello` (not `libcamera-hello`)
> - `libgpiod2` does not exist — use `python3-lgpio` instead
> - `libcamera-apps` → not needed, `rpicam-apps` comes with `python3-picamera2`

```bash
sudo apt-get install -y \
    python3-venv \
    python3-pip \
    python3-dev \
    python3-picamera2 \
    avahi-daemon \
    avahi-utils \
    wpasupplicant \
    wireless-tools \
    net-tools \
    curl \
    git \
    python3-lgpio
```

#### STEP 2c — Python Virtual Environment

```bash
# MUST use --system-site-packages so picamera2 is accessible inside venv
python3 -m venv --system-site-packages /home/pi/vision_recogniser/venv

/home/pi/vision_recogniser/venv/bin/pip install --upgrade pip
/home/pi/vision_recogniser/venv/bin/pip install -r /home/pi/vision_recogniser/requirements.txt
```

Verify:
```bash
/home/pi/vision_recogniser/venv/bin/python3 -c "
import firebase_admin, requests
print('✅ firebase_admin OK')
print('✅ requests OK')
"
```

#### STEP 2d — Verify Camera

```bash
rpicam-hello --list-cameras
# Expected: Camera 0 : ov5647 [2592x1944 ...]
```

#### STEP 2e — WiFi Dropout Fix (Pi 4)

The Pi's WiFi radio goes to sleep and stops reconnecting. Apply all 3 methods:

**Method A — Disable now:**
```bash
sudo iwconfig wlan0 power off
iwconfig wlan0 | grep "Power Management"   # should say: off
```

**Method B — Persistent via dhcpcd hook:**
```bash
sudo tee /etc/dhcpcd.exit-hook << 'EOF'
#!/bin/bash
if [ "$interface" = "wlan0" ] && [ "$reason" = "BOUND" -o "$reason" = "RENEW" ]; then
    iwconfig wlan0 power off 2>/dev/null || true
fi
EOF
sudo chmod +x /etc/dhcpcd.exit-hook
```

**Method C — Fast DHCP + prevent race condition:**
```bash
grep -q "nohook wpa_supplicant" /etc/dhcpcd.conf || sudo bash -c 'printf "\n# Vision fixes\nnohook wpa_supplicant\noption rapid_commit\nnoipv6\n" >> /etc/dhcpcd.conf'
```

#### STEP 2f — WiFi Watchdog Service (Pi 4)

```bash
sudo cp /home/pi/vision_recogniser/pi_setup/wifi_watchdog.sh /usr/local/bin/wifi_watchdog.sh
sudo chmod +x /usr/local/bin/wifi_watchdog.sh
```

```bash
sudo tee /etc/systemd/system/wifi-watchdog.service << 'EOF'
[Unit]
Description=WiFi Watchdog - keeps wlan0 alive
After=network.target
Wants=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/wifi_watchdog.sh
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
User=root

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable wifi-watchdog.service
sudo systemctl start wifi-watchdog.service
sudo systemctl status wifi-watchdog.service --no-pager
```

#### STEP 2g — Vision Recogniser Autostart

```bash
sudo cp /home/pi/vision_recogniser/pi_setup/vision-recogniser.service /etc/systemd/system/vision-recogniser.service
sudo systemctl daemon-reload
sudo systemctl enable vision-recogniser.service
```

#### STEP 2h — SSH Keep-Alive + mDNS

```bash
grep -q "ClientAliveInterval" /etc/ssh/sshd_config || \
    printf "\nClientAliveInterval 60\nClientAliveCountMax 10\n" | sudo tee -a /etc/ssh/sshd_config
sudo systemctl restart ssh

sudo systemctl enable avahi-daemon
sudo systemctl start avahi-daemon
```

#### STEP 2i — Permissions

```bash
mkdir -p /home/pi/vision_recogniser/image_buffer /home/pi/vision_recogniser/logs
sudo chown -R pi:pi /home/pi/vision_recogniser
sudo usermod -aG dialout,gpio,i2c,spi,video,input pi
```

#### STEP 2j — Final Check & Reboot

```bash
echo "=== Services ===" && \
systemctl is-enabled wifi-watchdog vision-recogniser ssh avahi-daemon && \
echo "" && echo "=== WiFi Power ===" && \
iwconfig wlan0 | grep "Power Management" && \
echo "" && echo "=== Internet ===" && \
ping -c 2 8.8.8.8 && echo "✅ Ready to reboot!"

sudo reboot
```

**After reboot (~30s), reconnect and verify:**
```bash
ssh pi@<PI_IP>
systemctl status wifi-watchdog vision-recogniser --no-pager
journalctl -u vision-recogniser -n 20 --no-pager
```

</details>

---

## ── RASPBERRY PI 5 SETUP ───────────────────────────────────

> [!IMPORTANT]
> Pi 5 uses **NetworkManager** by default. Do NOT use `wpa_cli`, `dhcpcd` hooks, or `rc.local` — they are inactive on Pi 5 Bookworm and will silently do nothing.

### Connect from Windows

```powershell
ssh pi@<PI_IP_ADDRESS>
```

---

### STEP 1 — Transfer Codebase (Windows PowerShell)

```powershell
scp -r "C:\Users\<you>\Desktop\vision_recogniser" pi@<PI_IP>:/home/pi/
```

---

### STEP 2 — Edit WiFi credentials in setup script

Before running, update your SSID and password in `setup_rpi5.sh`:

```bash
nano /home/pi/vision_recogniser/pi_setup/setup_rpi5.sh
# Find the line: ssid "Nitro" and psk "password"
# Replace with your actual WiFi SSID and password
```

---

### STEP 3 — Run One-Shot Setup Script

```bash
cd /home/pi/vision_recogniser
sudo bash pi_setup/setup_rpi5.sh
```

This handles everything: packages, NetworkManager WiFi config, power management (nmcli + udev), Pi-5-aware watchdog, services, SSH, permissions, and reboots.

---

### Manual Steps (Pi 5) — if you prefer step-by-step

<details>
<summary>Click to expand Pi 5 manual steps</summary>

#### STEP 3a — System Update

```bash
sudo apt-get update && sudo apt-get upgrade -y
```

#### STEP 3b — Install System Packages

```bash
sudo apt-get install -y \
    python3-venv \
    python3-pip \
    python3-dev \
    python3-picamera2 \
    avahi-daemon \
    avahi-utils \
    net-tools \
    wireless-tools \
    curl \
    git \
    python3-lgpio \
    network-manager \
    iw
```

> [!NOTE]
> On Pi 5 Bookworm:
> - **Do NOT install** `wpasupplicant` standalone — NetworkManager manages it internally
> - **Do NOT use** `dhcpcd` — NetworkManager handles DHCP
> - `python3-lgpio` covers all GPIO access (no `libgpiod2t64` needed)
> - Add user to `render` group for camera access

#### STEP 3c — Python Virtual Environment

```bash
python3 -m venv --system-site-packages /home/pi/vision_recogniser/venv

/home/pi/vision_recogniser/venv/bin/pip install --upgrade pip
/home/pi/vision_recogniser/venv/bin/pip install -r /home/pi/vision_recogniser/requirements.txt
```

Verify:
```bash
/home/pi/vision_recogniser/venv/bin/python3 -c "
import firebase_admin, requests
print('✅ firebase_admin OK')
print('✅ requests OK')
"
```

#### STEP 3d — Verify Camera

```bash
rpicam-hello --list-cameras
# Expected: Camera 0 : ov5647 [2592x1944 ...]
```

#### STEP 3e — Configure WiFi via NetworkManager

```bash
# Add your WiFi network (replace SSID and password)
sudo nmcli connection add \
    type wifi \
    ifname wlan0 \
    con-name "MyWiFi" \
    ssid "YOUR_SSID" \
    -- \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "YOUR_PASSWORD" \
    connection.autoconnect yes \
    connection.autoconnect-priority 10

# Verify connection
nmcli device status
nmcli connection show
```

#### STEP 3f — WiFi Power Management Fix (Pi 5)

> [!WARNING]
> On Pi 5, `dhcpcd` hooks and `rc.local` do NOT work. Use `nmcli` + `udev` instead.

```bash
# Method A: Disable immediately
sudo iwconfig wlan0 power off
iwconfig wlan0 | grep "Power Management"   # should say: off

# Method B: nmcli — permanent disable on connection profile
sudo nmcli connection modify "MyWiFi" 802-11-wireless.powersave 2

# Method C: udev rule — fires every time wlan0 comes up (MOST reliable)
sudo tee /etc/udev/rules.d/70-wifi-powersave.rules << 'EOF'
ACTION=="add", SUBSYSTEM=="net", KERNEL=="wlan0", \
    RUN+="/usr/sbin/iwconfig wlan0 power off"
EOF
sudo udevadm control --reload-rules

# Verify
iwconfig wlan0 | grep "Power Management"
# Should say: Power Management:off
```

#### STEP 3g — WiFi Watchdog Service (Pi 5 version)

```bash
sudo cp /home/pi/vision_recogniser/pi_setup/wifi_watchdog_rpi5.sh /usr/local/bin/wifi_watchdog.sh
sudo chmod +x /usr/local/bin/wifi_watchdog.sh

sudo tee /etc/systemd/system/wifi-watchdog.service << 'EOF'
[Unit]
Description=WiFi Watchdog - keeps wlan0 alive and reconnects on dropout
After=NetworkManager.service network-online.target
Wants=NetworkManager.service network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/wifi_watchdog.sh
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
User=root

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable wifi-watchdog.service
sudo systemctl start wifi-watchdog.service
sudo systemctl status wifi-watchdog.service --no-pager
```

#### STEP 3h — Vision Recogniser Autostart

```bash
sudo cp /home/pi/vision_recogniser/pi_setup/vision-recogniser.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable vision-recogniser.service
```

#### STEP 3i — SSH Keep-Alive + mDNS

```bash
grep -q "ClientAliveInterval" /etc/ssh/sshd_config || \
    printf "\nClientAliveInterval 60\nClientAliveCountMax 10\n" | sudo tee -a /etc/ssh/sshd_config
sudo systemctl restart ssh

sudo systemctl enable avahi-daemon
sudo systemctl start avahi-daemon
```

#### STEP 3j — Permissions

```bash
mkdir -p /home/pi/vision_recogniser/image_buffer /home/pi/vision_recogniser/logs
sudo chown -R pi:pi /home/pi/vision_recogniser
# Note: 'render' group added for Pi 5 camera access
sudo usermod -aG dialout,gpio,i2c,spi,video,input,render pi
```

#### STEP 3k — Final Check & Reboot

```bash
echo "=== Services ===" && \
systemctl is-enabled wifi-watchdog vision-recogniser ssh avahi-daemon && \
echo "" && echo "=== WiFi (NetworkManager) ===" && \
nmcli device status && \
echo "" && echo "=== WiFi Power ===" && \
iwconfig wlan0 | grep "Power Management" && \
echo "" && echo "=== Internet ===" && \
ping -c 2 8.8.8.8 && echo "✅ Ready to reboot!"

sudo reboot
```

**After reboot (~30s), reconnect and verify:**
```bash
ssh pi@<PI_IP>
systemctl status wifi-watchdog vision-recogniser --no-pager
journalctl -u vision-recogniser -n 20 --no-pager
```

</details>

---

## Configuration (Same for Pi 4 & Pi 5)

Edit `config.py` to set your credentials:

```python
GEMINI_API_KEY = "YOUR_NEW_GEMINI_API_KEY"   # get from aistudio.google.com/app/apikey
RTDB_URL       = "https://your-project-default-rtdb.firebaseio.com/"
DEVICE_ID      = "device_001"
```

Place your Firebase service account file at:
```
/home/pi/vision_recogniser/service-account.json
```

> [!CAUTION]
> Never commit `service-account.json` or your `GEMINI_API_KEY` to Git.
> If your API key is exposed publicly, Google will automatically revoke it.

---

## Daily Use Commands

```bash
# Live app logs
journalctl -u vision-recogniser -f

# Live WiFi watchdog logs
journalctl -u wifi-watchdog -f

# Restart app
sudo systemctl restart vision-recogniser

# Check all service status
systemctl status wifi-watchdog vision-recogniser --no-pager

# Check WiFi power (should always say "off")
iwconfig wlan0 | grep "Power Management"

# Check WiFi connection (Pi 5 — NetworkManager)
nmcli device status

# Check internet
ping -c 3 8.8.8.8

# Test camera
rpicam-hello --list-cameras
```

---

## Troubleshooting

### Sensor calibration failed — 0 valid readings
**Cause:** HC-SR04 ultrasonic sensor not connected or wired incorrectly.
**Fix:** Check TRIG → GPIO23, ECHO → GPIO24 (via voltage divider). The app will crash without a working sensor.

### WiFi keeps dropping
**Cause:** WiFi power management re-enabled after interface restart.
**Fix (Pi 4):** Run `sudo iwconfig wlan0 power off`. The `wifi-watchdog` reconnects within 90s.
**Fix (Pi 5):** Run `sudo nmcli connection modify "MyWiFi" 802-11-wireless.powersave 2` and check udev rule is in place.

### `libcamera-hello: command not found`
**Fix:** Use `rpicam-hello` instead — this is the correct command on Raspberry Pi OS Bookworm.

### `E: Unable to locate package libgpiod2`
**Fix:** Package renamed on Bookworm. Use `python3-lgpio` instead (already in setup scripts).

### API key error — 403 PERMISSION_DENIED
**Cause:** Key was exposed publicly and auto-revoked by Google.
**Fix:** Generate a new key at [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) and update `config.py`.

### Pi's IP changes every reboot
**Fix:** Use `ssh pi@raspberrypi.local` (avahi-daemon handles mDNS). Or set a static IP in your router/hotspot DHCP settings.

### SSH times out
**Cause:** Pi is rebooting or lost network. Wait 30–60s and retry.

### Camera not detected on Pi 5
**Fix:** Ensure user is in `render` group: `sudo usermod -aG render pi` then reboot.

### `nmcli: command not found` (Pi 4)
**Cause:** Pi 4 Bookworm Lite may not have NetworkManager by default.
**Fix:** Use `wpa_cli` commands instead — the Pi 4 setup script handles this correctly.

---

## Architecture

```
main.py
├── sensor.py          ← HC-SR04 ultrasonic (trigger detection)
├── capture.py         ← picamera2 (image capture)
├── gemini_api.py      ← Gemini Vision API (product detection)
├── firebase_upload.py ← Firebase RTDB + Firestore (data upload)
├── heartbeat.py       ← Device online/offline status in RTDB
└── config.py          ← All constants, pins, API keys

pi_setup/
├── setup_pi.sh            ← One-shot setup for Raspberry Pi 4
├── setup_rpi5.sh          ← One-shot setup for Raspberry Pi 5
├── wifi_watchdog.sh       ← WiFi reconnect daemon (Pi 4 / wpa_cli)
├── wifi_watchdog_rpi5.sh  ← WiFi reconnect daemon (Pi 5 / nmcli)
├── wifi-watchdog.service  ← systemd service for watchdog
└── vision-recogniser.service ← systemd service for main app
```

---

## Hardware

| Component | Connection | GPIO (BCM) |
|---|---|---|
| Pi Camera Module V1/V2 | CSI ribbon cable | — |
| HC-SR04 TRIG | GPIO pin | 23 |
| HC-SR04 ECHO | GPIO pin (via voltage divider) | 24 |
| Status LED | GPIO + 1kΩ resistor | 17 |
| Capture LED | GPIO + 1kΩ resistor | 27 |

> [!IMPORTANT]
> HC-SR04 Echo pin outputs 5V — use a voltage divider (1kΩ + 2kΩ) to bring it down to 3.3V for the Pi GPIO. LEDs need a 1kΩ resistor in series.

---

## OS Setup (Flash SD Card)

1. Download **Raspberry Pi Imager**: [raspberrypi.com/software](https://www.raspberrypi.com/software/)
2. Select OS: `Raspberry Pi OS (other)` → `Raspberry Pi OS Lite (64-bit)`
3. Click the **gear icon** and set:
   - **Hostname**: `pi`
   - **Username**: `pi` / set a password
   - **WiFi**: your SSID + password
   - **Enable SSH**: ✅ (password auth)
4. Flash → insert SD card → boot Pi

---

## Full Setup Guide (Run on Pi via SSH)

### Connect from Windows

```powershell
ssh pi@<PI_IP_ADDRESS>
# e.g. ssh pi@192.168.137.107
```

---

### STEP 1 — Transfer Codebase (Windows PowerShell)

```powershell
scp -r "C:\Users\<you>\Desktop\vision_recogniser" pi@<PI_IP>:/home/pi/
```

---

### STEP 2 — System Update

```bash
sudo apt-get update && sudo apt-get upgrade -y
```

---

### STEP 3 — Install System Packages

> [!NOTE]
> On **Raspberry Pi OS Bookworm/Trixie** (latest):
> - Camera command is `rpicam-hello` (not `libcamera-hello`)
> - `libgpiod2` does not exist — use `python3-lgpio` instead
> - `libcamera-apps` → not needed, `rpicam-apps` comes with `python3-picamera2`

```bash
sudo apt-get install -y \
    python3-venv \
    python3-pip \
    python3-dev \
    python3-picamera2 \
    avahi-daemon \
    avahi-utils \
    wpasupplicant \
    wireless-tools \
    net-tools \
    curl \
    git \
    python3-lgpio
```

---

### STEP 4 — Python Virtual Environment

```bash
# MUST use --system-site-packages so picamera2 is accessible inside venv
python3 -m venv --system-site-packages /home/pi/vision_recogniser/venv

/home/pi/vision_recogniser/venv/bin/pip install --upgrade pip
/home/pi/vision_recogniser/venv/bin/pip install -r /home/pi/vision_recogniser/requirements.txt
```

Verify:
```bash
/home/pi/vision_recogniser/venv/bin/python3 -c "
import firebase_admin, requests
print('✅ firebase_admin OK')
print('✅ requests OK')
"
```

---

### STEP 5 — Verify Camera

```bash
rpicam-hello --list-cameras
# Expected: Camera 0 : ov5647 [2592x1944 ...]
```

---

### STEP 6 — WiFi Dropout Fix (Root Cause: Power Management)

The Pi's WiFi radio goes to sleep and stops reconnecting. Apply all 3 methods:

**Method A — Disable now:**
```bash
sudo iwconfig wlan0 power off
iwconfig wlan0 | grep "Power Management"   # should say: off
```

**Method B — Persistent via dhcpcd hook:**
```bash
sudo tee /etc/dhcpcd.exit-hook << 'EOF'
#!/bin/bash
if [ "$interface" = "wlan0" ] && [ "$reason" = "BOUND" -o "$reason" = "RENEW" ]; then
    iwconfig wlan0 power off 2>/dev/null || true
fi
EOF
sudo chmod +x /etc/dhcpcd.exit-hook
```

**Method C — Fast DHCP + prevent race condition:**
```bash
grep -q "nohook wpa_supplicant" /etc/dhcpcd.conf || sudo bash -c 'printf "\n# Vision fixes\nnohook wpa_supplicant\noption rapid_commit\nnoipv6\n" >> /etc/dhcpcd.conf'
```

---

### STEP 7 — WiFi Watchdog Service

Pings 8.8.8.8 every 30s. After 3 failures (~90s), forces full WiFi reconnect.

```bash
sudo cp /home/pi/vision_recogniser/pi_setup/wifi_watchdog.sh /usr/local/bin/wifi_watchdog.sh
sudo chmod +x /usr/local/bin/wifi_watchdog.sh
```

```bash
sudo tee /etc/systemd/system/wifi-watchdog.service << 'EOF'
[Unit]
Description=WiFi Watchdog - keeps wlan0 alive
After=network.target
Wants=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/wifi_watchdog.sh
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
User=root

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable wifi-watchdog.service
sudo systemctl start wifi-watchdog.service
sudo systemctl status wifi-watchdog.service --no-pager
```

---

### STEP 8 — Vision Recogniser Autostart Service

```bash
sudo cp /home/pi/vision_recogniser/pi_setup/vision-recogniser.service /etc/systemd/system/vision-recogniser.service
sudo systemctl daemon-reload
sudo systemctl enable vision-recogniser.service
```

The service starts `main.py` automatically on boot, after the WiFi watchdog is up.

---

### STEP 9 — SSH Keep-Alive + mDNS

```bash
# SSH keep-alive (prevents disconnection)
grep -q "ClientAliveInterval" /etc/ssh/sshd_config || \
    printf "\nClientAliveInterval 60\nClientAliveCountMax 10\n" | sudo tee -a /etc/ssh/sshd_config
sudo systemctl restart ssh

# mDNS — access Pi as pi.local
sudo systemctl enable avahi-daemon
sudo systemctl start avahi-daemon
```

---

### STEP 10 — Permissions

```bash
mkdir -p /home/pi/vision_recogniser/image_buffer /home/pi/vision_recogniser/logs
sudo chown -R pi:pi /home/pi/vision_recogniser
sudo usermod -aG dialout,gpio,i2c,spi,video,input pi
```

---

### STEP 11 — Final Check & Reboot

```bash
echo "=== Services ===" && \
systemctl is-enabled wifi-watchdog vision-recogniser ssh avahi-daemon && \
echo "" && echo "=== WiFi Power ===" && \
iwconfig wlan0 | grep "Power Management" && \
echo "" && echo "=== Internet ===" && \
ping -c 2 8.8.8.8 && echo "✅ Ready to reboot!"
```

```bash
sudo reboot
```

**After reboot (~30s), reconnect and verify:**
```bash
ssh pi@<PI_IP>
systemctl status wifi-watchdog vision-recogniser --no-pager
journalctl -u vision-recogniser -n 20 --no-pager
```

---

## Configuration

Edit `config.py` to set your credentials:

```python
GEMINI_API_KEY = "YOUR_NEW_GEMINI_API_KEY"   # get from aistudio.google.com/app/apikey
RTDB_URL       = "https://your-project-default-rtdb.firebaseio.com/"
DEVICE_ID      = "device_001"
```

Place your Firebase service account file at:
```
/home/pi/vision_recogniser/service-account.json
```

> [!CAUTION]
> Never commit `service-account.json` or your `GEMINI_API_KEY` to Git.
> If your API key is exposed publicly, Google will automatically revoke it.

---

## Daily Use Commands

```bash
# Live app logs
journalctl -u vision-recogniser -f

# Live WiFi watchdog logs
journalctl -u wifi-watchdog -f

# Restart app
sudo systemctl restart vision-recogniser

# Check all service status
systemctl status wifi-watchdog vision-recogniser --no-pager

# Check WiFi power (should always say "off")
iwconfig wlan0 | grep "Power Management"

# Check internet
ping -c 3 8.8.8.8

# Test camera
rpicam-hello --list-cameras
```

---

## Troubleshooting

### Sensor calibration failed — 0 valid readings
**Cause:** HC-SR04 ultrasonic sensor not connected or wired incorrectly.
**Fix:** Check TRIG → GPIO23, ECHO → GPIO24 (via voltage divider). The app will crash without a working sensor.

### WiFi keeps dropping
**Cause:** WiFi power management re-enabled after interface restart.
**Fix:** Run `sudo iwconfig wlan0 power off`. The `wifi-watchdog` service automatically reconnects within 90s of any dropout.

### `libcamera-hello: command not found`
**Fix:** Use `rpicam-hello` instead — this is the correct command on Raspberry Pi OS Bookworm/Trixie.

### `E: Unable to locate package libgpiod2`
**Fix:** Package renamed on Bookworm. Use `python3-lgpio` instead (already in STEP 3).

### API key error — 403 PERMISSION_DENIED
**Cause:** Key was exposed publicly and auto-revoked by Google.
**Fix:** Generate a new key at [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) and update `config.py`.

### Pi's IP changes every reboot
**Fix:** Use `ssh pi@pi.local` (avahi-daemon handles mDNS). Or set a static IP in your router/hotspot DHCP settings.

### SSH times out
**Cause:** Pi is rebooting or lost network. Wait 30–60s and retry.

---

## Architecture

```
main.py
├── sensor.py          ← HC-SR04 ultrasonic (trigger detection)
├── capture.py         ← picamera2 (image capture)
├── gemini_api.py      ← Gemini Vision API (product detection)
├── firebase_upload.py ← Firebase RTDB + Firestore (data upload)
├── heartbeat.py       ← Device online/offline status in RTDB
└── config.py          ← All constants, pins, API keys

pi_setup/
├── setup_pi.sh            ← Legacy one-shot setup script (see README instead)
├── wifi_watchdog.sh       ← WiFi reconnect daemon
├── wifi-watchdog.service  ← systemd service for watchdog
└── vision-recogniser.service ← systemd service for main app
```