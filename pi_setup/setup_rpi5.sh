#!/bin/bash
# ============================================================
# setup_rpi5.sh  —  One-shot Raspberry Pi 5 setup
# Run as: sudo bash setup_rpi5.sh
#
# Pi 5 differences vs Pi 4:
#   • Uses NetworkManager (not wpa_supplicant + dhcpcd)
#   • No dhcpcd hooks — WiFi power save disabled via nmcli
#   • rc.local is deprecated — not used here
#   • Same Python stack, same systemd services
#
# What this does:
#   1. System update
#   2. Install system deps (NetworkManager, picamera2, lgpio …)
#   3. Create Python venv + install pip requirements
#   4. Configure WiFi via NetworkManager (nmcli)
#   5. Disable WiFi power management permanently (nmcli + systemd)
#   6. Install wifi_watchdog_rpi5 as a systemd service
#   7. Install vision-recogniser as a systemd service
#   8. Enable SSH & mDNS (so Pi is always raspberrypi.local)
#   9. Fix permissions & GPIO group access
# ============================================================

set -euo pipefail

# ── Colours ─────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

step()  { echo -e "\n${CYAN}${BOLD}▶ $1${NC}"; }
ok()    { echo -e "${GREEN}✔ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠ $1${NC}"; }
fatal() { echo -e "${RED}✖ $1${NC}"; exit 1; }

# ── Must be root ─────────────────────────────────────────────
[ "$EUID" -eq 0 ] || fatal "Run with sudo: sudo bash setup_rpi5.sh"

PI_USER="pi"
PI_HOME="/home/${PI_USER}"
APP_DIR="${PI_HOME}/vision_recogniser"
VENV_DIR="${APP_DIR}/venv"

# ════════════════════════════════════════════════════════════
# STEP 1 – System update
# ════════════════════════════════════════════════════════════
step "[1/9] Updating system packages …"
apt-get update -qq
apt-get upgrade -y -qq
ok "System updated"

# ════════════════════════════════════════════════════════════
# STEP 2 – Install system dependencies
# ════════════════════════════════════════════════════════════
step "[2/9] Installing system dependencies …"
apt-get install -y -qq \
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

# NOTE: On Pi 5 Bookworm:
#   • wpasupplicant is managed by NetworkManager — do NOT run it standalone
#   • dhcpcd is NOT the default DHCP client — NetworkManager handles DHCP
#   • libgpiod2t64 is not needed — python3-lgpio covers GPIO access
ok "System deps installed"

# ════════════════════════════════════════════════════════════
# STEP 3 – Python virtualenv + pip requirements
# ════════════════════════════════════════════════════════════
step "[3/9] Setting up Python virtual environment …"
sudo -u "${PI_USER}" python3 -m venv --system-site-packages "${VENV_DIR}"
sudo -u "${PI_USER}" "${VENV_DIR}/bin/pip" install --upgrade pip -q
sudo -u "${PI_USER}" "${VENV_DIR}/bin/pip" install -r "${APP_DIR}/requirements.txt" -q
ok "Python venv ready at ${VENV_DIR}"

# ════════════════════════════════════════════════════════════
# STEP 4 – WiFi: Configure via NetworkManager
# ════════════════════════════════════════════════════════════
step "[4/9] Configuring WiFi via NetworkManager …"

# Make sure NetworkManager is running
systemctl enable NetworkManager
systemctl start NetworkManager
sleep 2

# Check if a connection for "IOTDEVICE" already exists
if nmcli connection show "IOTDEVICE" >/dev/null 2>&1; then
    warn "Connection 'IOTDEVICE' already exists — updating password …"
    nmcli connection modify "IOTDEVICE" wifi-sec.psk "12345678"
else
    nmcli connection add \
        type wifi \
        ifname wlan0 \
        con-name "IOTDEVICE" \
        ssid "IOTDEVICE" \
        -- \
        wifi-sec.key-mgmt wpa-psk \
        wifi-sec.psk "12345678" \
        connection.autoconnect yes \
        connection.autoconnect-priority 10
    ok "NetworkManager connection 'IOTDEVICE' created"
fi

ok "WiFi configured via NetworkManager"

# ════════════════════════════════════════════════════════════
# STEP 5 – Disable WiFi power management permanently
# ════════════════════════════════════════════════════════════
step "[5/9] Disabling WiFi power management permanently …"

# Method A: Disable immediately (survives until reboot)
iwconfig wlan0 power off 2>/dev/null || true

# Method B: nmcli — set power save off on the connection profile
#   802-11-wireless.powersave: 2 = disabled
nmcli connection modify "IOTDEVICE" 802-11-wireless.powersave 2 2>/dev/null || \
    warn "nmcli powersave: connection 'IOTDEVICE' not found yet — watchdog will handle it"

# Method C: udev rule — disables power save whenever wlan0 comes up
#   This is the MOST reliable method on Pi 5 Bookworm
cat > /etc/udev/rules.d/70-wifi-powersave.rules << 'UDEV_EOF'
# Disable WiFi power management on wlan0 every time it comes up
ACTION=="add", SUBSYSTEM=="net", KERNEL=="wlan0", \
    RUN+="/usr/sbin/iwconfig wlan0 power off"
UDEV_EOF
udevadm control --reload-rules
ok "udev rule: WiFi power save disabled on every interface-up"

# Method D: systemd-networkd config (belt + braces, harmless if unused)
mkdir -p /etc/systemd/network
cat > /etc/systemd/network/10-wlan0.network << 'NET_EOF'
[Match]
Name=wlan0

[Link]
RequiredForOnline=yes
PowerManagement=off

[Network]
DHCP=yes
NET_EOF

ok "WiFi power management disabled (4 methods applied)"

# ════════════════════════════════════════════════════════════
# STEP 6 – Install WiFi watchdog service (Pi 5 version)
# ════════════════════════════════════════════════════════════
step "[6/9] Installing WiFi watchdog service (Pi 5 / NetworkManager) …"

cp "${APP_DIR}/pi_setup/wifi_watchdog_rpi5.sh" /usr/local/bin/wifi_watchdog.sh
chmod +x /usr/local/bin/wifi_watchdog.sh

cat > /etc/systemd/system/wifi-watchdog.service << 'SVC_EOF'
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
SVC_EOF

systemctl daemon-reload
systemctl enable wifi-watchdog.service
systemctl restart wifi-watchdog.service
ok "wifi-watchdog.service enabled and started"

# ════════════════════════════════════════════════════════════
# STEP 7 – Install vision-recogniser autostart service
# ════════════════════════════════════════════════════════════
step "[7/9] Installing vision-recogniser autostart service …"

cat > /etc/systemd/system/vision-recogniser.service << SVC2_EOF
[Unit]
Description=Vision Recogniser - IoT Inventory Detection
After=network-online.target wifi-watchdog.service
Wants=network-online.target
Requires=wifi-watchdog.service

[Service]
Type=simple
User=${PI_USER}
Group=${PI_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${VENV_DIR}/bin/python3 main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=vision-recogniser
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=${APP_DIR}

[Install]
WantedBy=multi-user.target
SVC2_EOF

systemctl daemon-reload
systemctl enable vision-recogniser.service
ok "vision-recogniser.service enabled (will start on next boot)"

# ════════════════════════════════════════════════════════════
# STEP 8 – Enable SSH & mDNS (raspberrypi.local)
# ════════════════════════════════════════════════════════════
step "[8/9] Enabling SSH and mDNS …"

systemctl enable ssh
systemctl start ssh
systemctl enable avahi-daemon
systemctl start avahi-daemon
ok "SSH enabled. Pi is accessible as: raspberrypi.local"

# SSH keep-alive so connections don't drop
grep -q "ClientAliveInterval" /etc/ssh/sshd_config || \
    echo -e "\nClientAliveInterval 60\nClientAliveCountMax 10" >> /etc/ssh/sshd_config
systemctl restart ssh
ok "SSH keep-alive configured"

# ════════════════════════════════════════════════════════════
# STEP 9 – Fix permissions & GPIO group access
# ════════════════════════════════════════════════════════════
step "[9/9] Setting file permissions …"

chown -R "${PI_USER}:${PI_USER}" "${APP_DIR}"
mkdir -p "${APP_DIR}/image_buffer" "${APP_DIR}/logs"
chown "${PI_USER}:${PI_USER}" "${APP_DIR}/image_buffer" "${APP_DIR}/logs"

# GPIO, camera, SPI, I2C access for pi user
usermod -aG dialout,gpio,i2c,spi,video,input,render "${PI_USER}" 2>/dev/null || true
# render group needed for camera on Pi 5 Bookworm
ok "Permissions set"

# ════════════════════════════════════════════════════════════
# DONE
# ════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  ✔  Raspberry Pi 5 Setup complete!${NC}"
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════${NC}"
echo ""
echo -e "Services enabled:"
echo -e "  ${CYAN}wifi-watchdog${NC}      — auto-reconnects WiFi on dropout (NM-aware)"
echo -e "  ${CYAN}vision-recogniser${NC}  — starts main.py on boot"
echo -e "  ${CYAN}ssh${NC}                — always on"
echo -e "  ${CYAN}avahi-daemon${NC}       — mDNS (raspberrypi.local)"
echo ""
echo -e "Useful commands:"
echo -e "  ${YELLOW}journalctl -u vision-recogniser -f${NC}   — live app logs"
echo -e "  ${YELLOW}journalctl -u wifi-watchdog -f${NC}       — live WiFi watchdog logs"
echo -e "  ${YELLOW}systemctl status vision-recogniser${NC}   — service status"
echo -e "  ${YELLOW}nmcli device status${NC}                  — check WiFi connection"
echo -e "  ${YELLOW}iwconfig wlan0${NC}                       — check power management"
echo ""
echo -e "${BOLD}SSH from Windows:${NC}"
echo -e "  ${YELLOW}ssh pi@raspberrypi.local${NC}   (always works after this setup)"
echo ""
echo -e "${BOLD}Rebooting in 5 seconds to apply all changes …${NC}"
sleep 5
reboot
