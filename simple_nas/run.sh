#!/usr/bin/with-contenv bashio

set -e

bashio::log.info "Starting Simple NAS..."

export WORKGROUP
WORKGROUP=$(bashio::config 'workgroup')
bashio::log.info "Workgroup: ${WORKGROUP}"

# Initialize persistent data files
for f in shares users mounts; do
    if [ ! -f "/data/${f}.json" ]; then
        echo '[]' > "/data/${f}.json"
    fi
done

# Generate smb.conf
python3 /app/generate_smb_conf.py "$WORKGROUP"

# Ensure Samba runtime directories
mkdir -p /var/log/samba /var/run/samba

# Start privileged mount helper daemon (communicates via FIFO)
bashio::log.info "Starting mount helper..."
/app/mount_helper.sh &

# Wait for FIFO to be ready
sleep 1

# Restore saved mounts via mount helper
python3 /app/restore_mounts.py

# Start Samba daemons
bashio::log.info "Starting Samba daemons (smbd + nmbd)..."
smbd --no-process-group --foreground --debug-stdout &
nmbd --no-process-group --foreground --debug-stdout &

sleep 2

# ── Network Discovery ──────────────────────────────────────────
bashio::log.info "Starting network discovery..."

# 1) DBus (required by avahi)
mkdir -p /var/run/dbus
if [ ! -S /var/run/dbus/system_bus_socket ]; then
    dbus-daemon --system --nofork --nopidfile &
    sleep 1
    bashio::log.info "DBus gestartet"
fi

# 2) Avahi – mDNS/DNS-SD (Linux Nautilus/Dolphin, macOS Finder)
if command -v avahi-daemon > /dev/null 2>&1; then
    # Fix avahi config for container environment
    sed -i 's/^#*enable-dbus=.*/enable-dbus=yes/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true
    sed -i 's/^#*use-ipv6=.*/use-ipv6=no/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true
    sed -i 's/^rlimit-nproc=.*/#rlimit-nproc=3/' /etc/avahi/avahi-daemon.conf 2>/dev/null || true

    avahi-daemon --daemonize --no-chroot 2>/dev/null && \
        bashio::log.info "Avahi gestartet (mDNS discovery für Linux/macOS)" || \
        bashio::log.warning "Avahi konnte nicht gestartet werden"
else
    bashio::log.warning "avahi-daemon nicht installiert"
fi

# 3) WSDD – WS-Discovery (Windows 10/11 Netzwerk-Browser)
if command -v wsdd > /dev/null 2>&1; then
    wsdd --workgroup "${WORKGROUP}" &
    bashio::log.info "WSDD gestartet (WS-Discovery für Windows 10/11)"
else
    bashio::log.warning "wsdd nicht installiert"
fi

bashio::log.info "Network Discovery aktiv"

# ── Web GUI ────────────────────────────────────────────────────
bashio::log.info "Starting Web GUI on port 8099..."
exec python3 /app/app.py
