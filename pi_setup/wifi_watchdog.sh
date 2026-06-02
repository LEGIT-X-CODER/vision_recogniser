#!/bin/bash
# ============================================================
# wifi_watchdog.sh (Pi 4 Version)
# Bulletproof WiFi watchdog for Raspberry Pi 4.
#
# Fixes the "stops connecting after 3-4 times" bug.
#
# Root causes resolved:
# 1. WiFi power management putting the Cypress chip to sleep.
# 2. wpa_supplicant internally disabling/blacklisting the network block 
#    after a few failed connections or timeouts, requiring explicit enablement.
# 3. Cypress driver/firmware hanging during background scans (bgscan).
# 4. dhcpcd holding a stale lease or refusing to renew when hotspot cycles.
# ============================================================

IFACE="wlan0"
PING_TARGET="8.8.8.8"        # Google DNS — always reachable if WiFi is up
PING_COUNT=2
PING_TIMEOUT=5
CHECK_INTERVAL=30            # seconds between checks
MAX_FAILURES=3               # consecutive failures before restart
LOG_TAG="wifi-watchdog"

failures=0

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [$LOG_TAG] $1" | tee /proc/1/fd/1 2>/dev/null || true
    logger -t "$LOG_TAG" "$1"
}

restart_wifi() {
    log "Ping failed $MAX_FAILURES times. Starting bulletproof WiFi reconnect sequence..."

    # 1. Force disable power management immediately
    iwconfig "$IFACE" power off 2>/dev/null || true

    # 2. Stop networking services cleanly to release resources
    log "Stopping dhcpcd and wpa_supplicant services..."
    systemctl stop dhcpcd 2>/dev/null || true
    systemctl stop wpa_supplicant 2>/dev/null || true
    killall wpa_supplicant 2>/dev/null || true
    sleep 2

    # 3. Bounce the hardware interface to completely reset the driver and Cypress firmware
    log "Bouncing interface $IFACE..."
    ip link set "$IFACE" down
    sleep 2
    ip link set "$IFACE" up
    sleep 3

    # 4. Start wpa_supplicant service fresh
    log "Starting wpa_supplicant..."
    systemctl start wpa_supplicant 2>/dev/null || true
    sleep 3

    # 5. CRITICAL FIX: Explicitly enable all configured network profiles
    # wpa_supplicant silently disables blocks internally after failed connections/handshakes.
    # Without this, reassociate / reconfigure will ignore the block.
    log "Enabling all network profiles and triggering reassociation..."
    wpa_cli -i "$IFACE" enable_network all 2>/dev/null || true
    wpa_cli -i "$IFACE" reassociate 2>/dev/null || true
    sleep 5

    # 6. Start dhcpcd service fresh to obtain a new DHCP lease
    log "Starting dhcpcd service..."
    systemctl start dhcpcd 2>/dev/null || true
    sleep 2

    # Force a fresh lease by killing any existing wlan0 leases and requesting new one
    log "Renewing DHCP lease on wlan0..."
    dhcpcd -k "$IFACE" 2>/dev/null || true
    sleep 1
    dhcpcd -n "$IFACE" 2>/dev/null || true

    # 7. Re-disable power management (some drivers turn it back on when interface goes up)
    iwconfig "$IFACE" power off 2>/dev/null || true

    log "WiFi reconnect sequence complete. Waiting 15 s for connection..."
    sleep 15
}

log "WiFi watchdog started (interface=$IFACE, target=$PING_TARGET)"

# Ensure power management is disabled from the start
iwconfig "$IFACE" power off 2>/dev/null || true

while true; do
    if ping -I "$IFACE" -c "$PING_COUNT" -W "$PING_TIMEOUT" "$PING_TARGET" > /dev/null 2>&1; then
        # Connection healthy — reset failure counter
        if [ "$failures" -gt 0 ]; then
            log "Connection restored after $failures failure(s)."
        fi
        failures=0
        # Double check power management is still off
        iwconfig "$IFACE" power off 2>/dev/null || true
    else
        failures=$((failures + 1))
        ACTIVE_SSID=$(iwgetid -r 2>/dev/null || echo 'none')
        log "Ping failed (${failures}/${MAX_FAILURES}). Current SSID: $ACTIVE_SSID"

        if [ "$failures" -ge "$MAX_FAILURES" ]; then
            log "Threshold reached — forcing bulletproof reconnect..."
            restart_wifi
            failures=0
        fi
    fi

    sleep "$CHECK_INTERVAL"
done
