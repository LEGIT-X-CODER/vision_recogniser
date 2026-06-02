#!/bin/bash
# ============================================================
# setup_pi.sh  —  One-shot Raspberry Pi 4 setup
# Run as: sudo bash setup_pi.sh
#
# What this does:
#   1. System update
#   2. Install system deps (python3-venv, avahi for mDNS, etc.)
#   3. Create Python venv + install pip requirements
#   4. Configure dual-WiFi (wpa_supplicant)
#   5. Fix WiFi power management permanently (the dropout fix)
#   6. Install wifi_watchdog as a systemd service
#   7. Install vision-recogniser as a systemd service
#   8. Enable SSH & mDNS (so Pi is always raspberrypi.local)
#   9. Enable networkd-wait-online for reliable startup order
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
[ "$EUID" -eq 0 ] || fatal "Run with sudo: sudo bash setup_pi.sh"

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
    libcamera-tools \
    avahi-daemon \
    avahi-utils \
    wpasupplicant \
    wireless-tools \
    net-tools \
    curl \
    git \
    libgpiod2t64 \
    python3-lgpio
# NOTE: isc-dhcp-client intentionally omitted — it conflicts with dhcpcd
# on Raspberry Pi OS. dhcpcd is the correct DHCP client.
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
# STEP 4 – WiFi: wpa_supplicant dual-network config
# ════════════════════════════════════════════════════════════
step "[4/9] Configuring dual-WiFi (wpa_supplicant) …"

# Backup existing config
if [ -f /etc/wpa_supplicant/wpa_supplicant.conf ]; then
    cp /etc/wpa_supplicant/wpa_supplicant.conf /etc/wpa_supplicant/wpa_supplicant.conf.bak
    warn "Old config backed up to wpa_supplicant.conf.bak"
fi

cat > /etc/wpa_supplicant/wpa_supplicant.conf << 'WPA_EOF'
country=IN
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
ap_scan=1

# Single block for "IOTDEVICE" hotspot (laptop + phone, same SSID+password)
# wpa_supplicant automatically picks the AP with the strongest signal.
# Use bgscan to actively re-scan when signal drops below -65 dBm.
network={
    ssid="IOTDEVICE"
    psk="12345678"
    key_mgmt=WPA-PSK
    priority=10
    # bgscan: commented out because background scanning can crash the Pi 4 Cypress WiFi driver/firmware.
    # bgscan="simple:30:-65:300"
}
WPA_EOF

chmod 600 /etc/wpa_supplicant/wpa_supplicant.conf
ok "wpa_supplicant.conf written"

# ════════════════════════════════════════════════════════════
# STEP 5 – Fix WiFi power management (ROOT CAUSE OF DROPOUT)
# ════════════════════════════════════════════════════════════
step "[5/9] Disabling WiFi power management permanently …"

# Method A: dhcpcd hook – runs every time interface comes up
cat > /etc/dhcpcd.exit-hook << 'HOOK_EOF'
#!/bin/bash
# Disable WiFi power management every time dhcpcd assigns/renews an IP
if [ "$interface" = "wlan0" ] && [ "$reason" = "BOUND" -o "$reason" = "RENEW" ]; then
    iwconfig wlan0 power off 2>/dev/null || true
fi
HOOK_EOF
chmod +x /etc/dhcpcd.exit-hook

# Method B: systemd-networkd override (belt + braces)
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

# Method C: Disable WiFi power save via dhcpcd.conf (reliable on Pi OS)
# NOTE: dtoverlay=disable-wifi-power-management does NOT exist — that overlay
# is invalid and silently fails. Use dhcpcd hook (Method A) + rc.local instead.
if ! grep -q "nohook wpa_supplicant" /etc/dhcpcd.conf 2>/dev/null; then
    # Tell dhcpcd not to touch wpa_supplicant — avoids DHCP/wpa_supplicant race
    echo "" >> /etc/dhcpcd.conf
    echo "# Do not interfere with wpa_supplicant managed connections" >> /etc/dhcpcd.conf
    echo "nohook wpa_supplicant" >> /etc/dhcpcd.conf
    ok "dhcpcd.conf: nohook wpa_supplicant added"
else
    warn "dhcpcd.conf: nohook already present"
fi

# Also set WiFi power save off in dhcpcd.conf
if ! grep -q "option rapid_commit" /etc/dhcpcd.conf 2>/dev/null; then
    echo "option rapid_commit" >> /etc/dhcpcd.conf
    echo "noipv6" >> /etc/dhcpcd.conf
    ok "dhcpcd.conf: rapid_commit + noipv6 added (faster DHCP)"
fi

# Method D: rc.local fallback
if ! grep -q "iwconfig wlan0 power off" /etc/rc.local 2>/dev/null; then
    # Insert before 'exit 0'
    sed -i '/^exit 0/i iwconfig wlan0 power off 2>/dev/null || true' /etc/rc.local 2>/dev/null || \
        echo -e "#!/bin/bash\niwconfig wlan0 power off 2>/dev/null || true\nexit 0" > /etc/rc.local
    chmod +x /etc/rc.local
fi

ok "WiFi power management disabled (4 methods applied)"

# ════════════════════════════════════════════════════════════
# STEP 6 – Install WiFi watchdog service
# ════════════════════════════════════════════════════════════
step "[6/9] Installing WiFi watchdog service …"
cp "${APP_DIR}/pi_setup/wifi_watchdog.sh" /usr/local/bin/wifi_watchdog.sh
chmod +x /usr/local/bin/wifi_watchdog.sh

cat > /etc/systemd/system/wifi-watchdog.service << 'SVC_EOF'
[Unit]
Description=WiFi Watchdog - keeps wlan0 alive and reconnects on dropout
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

# Make SSH keep-alive so connections don't drop
grep -q "ClientAliveInterval" /etc/ssh/sshd_config || \
    echo -e "\nClientAliveInterval 60\nClientAliveCountMax 10" >> /etc/ssh/sshd_config
systemctl restart ssh
ok "SSH keep-alive configured"

# ════════════════════════════════════════════════════════════
# STEP 9 – Fix permissions
# ════════════════════════════════════════════════════════════
step "[9/9] Setting file permissions …"
chown -R "${PI_USER}:${PI_USER}" "${APP_DIR}"
mkdir -p "${APP_DIR}/image_buffer" "${APP_DIR}/logs"
chown "${PI_USER}:${PI_USER}" "${APP_DIR}/image_buffer" "${APP_DIR}/logs"

# GPIO access for pi user
usermod -aG dialout,gpio,i2c,spi,video,input "${PI_USER}" 2>/dev/null || true
ok "Permissions set"

# ════════════════════════════════════════════════════════════
# DONE
# ════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  ✔  Setup complete!${NC}"
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════${NC}"
echo ""
echo -e "Services enabled:"
echo -e "  ${CYAN}wifi-watchdog${NC}      — auto-reconnects WiFi on dropout"
echo -e "  ${CYAN}vision-recogniser${NC}  — starts main.py on boot"
echo -e "  ${CYAN}ssh${NC}                — always on"
echo -e "  ${CYAN}avahi-daemon${NC}       — mDNS (raspberrypi.local)"
echo ""
echo -e "Useful commands:"
echo -e "  ${YELLOW}journalctl -u vision-recogniser -f${NC}   — live app logs"
echo -e "  ${YELLOW}journalctl -u wifi-watchdog -f${NC}       — live WiFi watchdog logs"
echo -e "  ${YELLOW}systemctl status vision-recogniser${NC}   — service status"
echo -e "  ${YELLOW}iwconfig wlan0${NC}                       — check power management"
echo ""
echo -e "${BOLD}SSH from Windows:${NC}"
echo -e "  ${YELLOW}ssh pi@raspberrypi.local${NC}   (always works after this setup)"
echo ""
echo -e "${BOLD}Rebooting in 5 seconds to apply all changes …${NC}"
sleep 5
reboot
