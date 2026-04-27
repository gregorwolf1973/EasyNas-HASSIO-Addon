#!/usr/bin/with-contenv bashio

set -e

bashio::log.info "Starting Simple NAS..."

export WORKGROUP
WORKGROUP=$(bashio::config 'workgroup')
bashio::log.info "Workgroup: ${WORKGROUP}"

export NAS_NAME
NAS_NAME=$(bashio::config 'nas_name')
bashio::log.info "NAS Name: ${NAS_NAME}"

export WEB_PORT
WEB_PORT=$(bashio::config 'web_port')
bashio::log.info "Web GUI Port: ${WEB_PORT}"

export ADMIN_PASSWORD_ENABLED
ADMIN_PASSWORD_ENABLED=$(bashio::config 'admin_password_enabled')

export ADMIN_USERNAME
ADMIN_USERNAME=$(bashio::config 'admin_username')

export ADMIN_PASSWORD
ADMIN_PASSWORD=$(bashio::config 'admin_password')

export SMB_PORT
SMB_PORT=$(bashio::config 'smb_port')
bashio::log.info "SMB Port: ${SMB_PORT}"

export WEB_GUI_ENABLED
WEB_GUI_ENABLED=$(bashio::config 'web_gui_enabled')
bashio::log.info "Web GUI enabled: ${WEB_GUI_ENABLED}"

# Auto-restore settings from /config/.simplenas/auto (reinstall-safe backup)
python3 - << 'PYEOF'
import os, shutil, json

AUTO_DIR = "/config/.simplenas/auto"
DATA_DIR = "/data"

meta   = os.path.join(AUTO_DIR, "meta.json")
shares = os.path.join(DATA_DIR, "shares.json")

# Restore only when auto-backup exists AND /data is empty/missing
if os.path.exists(meta) and (not os.path.exists(shares) or os.path.getsize(shares) <= 5):
    print("[RESTORE] Frische Installation erkannt – stelle Einstellungen aus /config/.simplenas/auto wieder her ...")
    os.makedirs(DATA_DIR, exist_ok=True)
    for fname in ("shares.json", "users.json", "groups.json", "mounts.json", "backups.json", "admin_auth.json"):
        src = os.path.join(AUTO_DIR, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(DATA_DIR, fname))
            print(f"[RESTORE] {fname} wiederhergestellt")
    samba_src = os.path.join(AUTO_DIR, "samba")
    if os.path.isdir(samba_src):
        samba_dst = os.path.join(DATA_DIR, "samba")
        os.makedirs(samba_dst, exist_ok=True)
        for f in os.listdir(samba_src):
            shutil.copy2(os.path.join(samba_src, f), os.path.join(samba_dst, f))
    print("[RESTORE] Fertig.")
PYEOF

# Initialize persistent data files (only if still missing after restore)
for f in shares users mounts groups backups; do
    if [ ! -f "/data/${f}.json" ]; then
        echo '[]' > "/data/${f}.json"
    fi
done

# Generate smb.conf
python3 /app/generate_smb_conf.py "$WORKGROUP" "$NAS_NAME" "$SMB_PORT"

# Restore system users and groups from persistent JSON files
# (Container is ephemeral - Linux users are lost on restart)
bashio::log.info "Restoring users and groups from persistent storage..."
python3 - << 'PYEOF'
import json, subprocess, os, shutil

# Restore groups
try:
    groups = json.load(open("/data/groups.json"))
    for g in groups:
        name = g.get("name", "")
        if name:
            subprocess.run(["addgroup", name], capture_output=True)
            for m in g.get("members", []):
                # Create user first if needed (for group membership)
                subprocess.run(["adduser", "-D", "-H", "-s", "/sbin/nologin", m], capture_output=True)
                subprocess.run(["addgroup", m, name], capture_output=True)
            print(f"  Group restored: {name} ({len(g.get('members',[]))} members)")
except Exception as e:
    print(f"  Groups: {e}")

# Restore system users
try:
    users = json.load(open("/data/users.json"))
    for u in users:
        username = u.get("username", "")
        if username:
            subprocess.run(["adduser", "-D", "-H", "-s", "/sbin/nologin", username], capture_output=True)
            print(f"  User restored: {username}")
except Exception as e:
    print(f"  Users: {e}")

# Restore Samba password database from persistent storage
samba_dir = "/var/lib/samba/private"
os.makedirs(samba_dir, exist_ok=True)
restored = 0
for fname in ["passdb.tdb", "secrets.tdb", "smbpasswd"]:
    src = f"/data/samba/{fname}"
    dst = f"{samba_dir}/{fname}"
    if os.path.exists(src):
        shutil.copy2(src, dst)
        restored += 1
# Also check /etc/samba/smbpasswd
if os.path.exists("/data/samba/smbpasswd"):
    shutil.copy2("/data/samba/smbpasswd", "/etc/samba/smbpasswd")

if restored > 0:
    print(f"  Samba password DB restored ({restored} files)")
else:
    print("  No Samba password DB backup found (passwords need to be set via GUI)")

print("  Done.")
PYEOF

# Ensure Samba runtime directories
mkdir -p /var/log/samba /var/run/samba
ln -sf /config /homeassistant

# Start privileged mount helper daemon (communicates via FIFO)
bashio::log.info "Starting mount helper..."
/app/mount_helper.sh &

# Wait for FIFO to be ready
sleep 1

# Migrate mounts.json: replace raw /dev/sdX paths with stable /dev/disk/by-id/
python3 - << 'PYEOF'
import json, os, re

MOUNTS_FILE = "/data/mounts.json"
BY_ID_DIR   = "/dev/disk/by-id"

def get_by_id_path(dev_path):
    """Resolve a /dev/sdX (or similar) path to a stable /dev/disk/by-id/ symlink."""
    if not os.path.isdir(BY_ID_DIR):
        return None
    try:
        real_dev = os.path.realpath(dev_path)
    except Exception:
        return None
    candidates = []
    try:
        for link in os.listdir(BY_ID_DIR):
            link_path = os.path.join(BY_ID_DIR, link)
            try:
                if os.path.realpath(link_path) == real_dev:
                    candidates.append(link)
            except Exception:
                pass
    except Exception:
        return None
    if not candidates:
        return None
    for prefix in ("usb-", "ata-", "nvme-", "mmc-", "scsi-"):
        for c in sorted(candidates):
            if c.startswith(prefix):
                return os.path.join(BY_ID_DIR, c)
    return os.path.join(BY_ID_DIR, sorted(candidates)[0])

try:
    mounts = json.load(open(MOUNTS_FILE))
except Exception:
    mounts = []

changed = 0
for m in mounts:
    dev = m.get("device", "")
    # Only migrate raw /dev/sd*, /dev/hd*, /dev/vd* paths — leave by-id and others alone
    if re.match(r"^/dev/[shv]d[a-z]\d*$", dev):
        by_id = get_by_id_path(dev)
        if by_id:
            print(f"[MIGRATE] {dev} → {by_id}")
            m["dev_path"] = dev     # keep original for reference
            m["device"]   = by_id
            changed += 1
        else:
            print(f"[MIGRATE] {dev}: kein by-id gefunden (Gerät nicht angeschlossen?)")

if changed:
    with open(MOUNTS_FILE, "w") as f:
        json.dump(mounts, f, indent=2)
    print(f"[MIGRATE] {changed} Eintrag/Einträge auf by-id migriert.")
else:
    print("[MIGRATE] Keine Migration nötig.")
PYEOF

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

    AVAHI_ERR=$(avahi-daemon --daemonize --no-chroot 2>&1)
    if [ $? -eq 0 ]; then
        bashio::log.info "Avahi daemon gestartet"
        sleep 1
        # Explicitly publish SMB service with friendly name (container hostname is ugly)
        avahi-publish -s "${NAS_NAME}" _smb._tcp "${SMB_PORT}" &
        bashio::log.info "Avahi mDNS: _smb._tcp auf Port ${SMB_PORT} als '${NAS_NAME}' published"
    else
        bashio::log.warning "Avahi daemon Fehler: ${AVAHI_ERR}"
        # Try avahi-publish directly (works if HA OS already runs avahi)
        avahi-publish -s "${NAS_NAME}" _smb._tcp "${SMB_PORT}" &
        bashio::log.info "Avahi: service via avahi-publish published (nutzt System-Avahi)"
    fi
else
    bashio::log.warning "avahi-daemon nicht installiert"
fi

# 3) WSDD – WS-Discovery (Windows 10/11 Netzwerk-Browser)
if command -v wsdd > /dev/null 2>&1; then
    python3 /usr/local/bin/wsdd --workgroup "${WORKGROUP}" --hostname "${NAS_NAME}" &
    bashio::log.info "WSDD gestartet (WS-Discovery für Windows 10/11, hostname=${NAS_NAME})"
else
    bashio::log.warning "wsdd nicht installiert"
fi

bashio::log.info "Network Discovery aktiv"

# ── Web GUI ────────────────────────────────────────────────────
if [ "${WEB_GUI_ENABLED}" = "true" ]; then
    bashio::log.info "Starting Web GUI on port ${WEB_PORT}..."
    exec python3 /app/app.py
else
    bashio::log.info "Web GUI disabled (web_gui_enabled: false) – Samba-only mode."
    exec tail -f /dev/null
fi
