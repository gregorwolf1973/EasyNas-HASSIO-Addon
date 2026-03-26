#!/usr/bin/with-contenv bashio

set -e

bashio::log.info "Starting Simple NAS..."

export WORKGROUP
WORKGROUP=$(bashio::config 'workgroup')
bashio::log.info "Workgroup: ${WORKGROUP}"

export WEB_PORT
WEB_PORT=$(bashio::config 'web_port')
bashio::log.info "Web GUI Port: ${WEB_PORT}"

# Initialize persistent data files
for f in shares users mounts groups; do
    if [ ! -f "/data/${f}.json" ]; then
        echo '[]' > "/data/${f}.json"
    fi
done

# Generate smb.conf
python3 /app/generate_smb_conf.py "$WORKGROUP"

# Restore system users and groups from persistent JSON files
# (Container is ephemeral - Linux users are lost on restart)
bashio::log.info "Restoring users and groups from persistent storage..."
python3 - << 'PYEOF'
import json, subprocess, os

# Restore groups
try:
    groups = json.load(open("/data/groups.json"))
    for g in groups:
        name = g.get("name", "")
        if name:
            subprocess.run(["addgroup", name], capture_output=True)
            for m in g.get("members", []):
                subprocess.run(["addgroup", m, name], capture_output=True)
            print(f"  Group restored: {name} ({len(g.get('members',[]))} members)")
except Exception as e:
    print(f"  Groups: {e}")

# Restore system users (without passwords - user must reset via GUI)
try:
    users = json.load(open("/data/users.json"))
    for u in users:
        username = u.get("username", "")
        if username:
            subprocess.run(["adduser", "-D", "-H", "-s", "/sbin/nologin", username], capture_output=True)
            # If we have a stored password hash, restore samba entry
            pwd = u.get("smb_hash", "")
            if pwd:
                proc = subprocess.run(["smbpasswd", "-a", "-s", username],
                    input=f"{pwd}\n{pwd}\n", text=True, capture_output=True)
            print(f"  User restored: {username}")
except Exception as e:
    print(f"  Users: {e}")

print("  Done.")
PYEOF

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
smbd --foreground --no-process-group &
nmbd --foreground --no-process-group &

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
    python3 /usr/local/bin/wsdd --workgroup "${WORKGROUP}" &
    bashio::log.info "WSDD gestartet (WS-Discovery für Windows 10/11)"
else
    bashio::log.warning "wsdd nicht installiert"
fi

bashio::log.info "Network Discovery aktiv"

# ── Web GUI ────────────────────────────────────────────────────
bashio::log.info "Starting Web GUI on port ${WEB_PORT}..."
exec python3 /app/app.py
