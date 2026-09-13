#!/usr/bin/env python3
"""Simple NAS - Flask Web GUI v2.0"""
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import re
import shutil
import time
import socket
from datetime import timedelta
from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

import safepath
import sharing_store
import share_web
import accesslog
import zipstream
import clamav
import sharesandbox
import share_worker
from ratelimit import LIMITER, AUTHFAIL_IP, AUTHFAIL_LINK

app = Flask(__name__)
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB per request; bodies are buffered to TMPDIR first
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES

DATA_DIR    = "/data"
CONFIG_BACKUP_DIR  = "/config/.simplenas"
CONFIG_AUTO_DIR    = f"{CONFIG_BACKUP_DIR}/auto"      # auto-backup (reinstall-safe, always overwritten)
CONFIG_BACKUPS_DIR = f"{CONFIG_BACKUP_DIR}/backups"   # manual snapshots
MAX_MANUAL_BACKUPS = 10
SHARES_FILE = f"{DATA_DIR}/shares.json"
USERS_FILE  = f"{DATA_DIR}/users.json"
MOUNTS_FILE = f"{DATA_DIR}/mounts.json"
GROUPS_FILE = f"{DATA_DIR}/groups.json"
FSTYPE_MEMORY_FILE = f"{DATA_DIR}/fstype_memory.json"
ADMIN_AUTH_FILE = f"{DATA_DIR}/admin_auth.json"
WORKGROUP   = os.environ.get("WORKGROUP", "WORKGROUP")
NAS_NAME    = os.environ.get("NAS_NAME", "SimpleNAS")
SMB_PORT    = os.environ.get("SMB_PORT", "445")

# ─────────────────────────── admin auth ─────────────────────────

_admin_auth = {"enabled": False}

def _session_key():
    """Persistent Flask secret, so logins survive an add-on restart."""
    os.makedirs(DATA_DIR, exist_ok=True)
    auth = load_json(ADMIN_AUTH_FILE, {})
    if not auth.get("secret_key"):
        auth["secret_key"] = secrets.token_hex(32)
        save_json(ADMIN_AUTH_FILE, auth)
    return auth["secret_key"], auth


def _auth_epoch(password_hash):
    """Fingerprint of the current password. Stored in the session so that
    changing the password in the add-on options logs everyone out."""
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:16]


def _setup_admin_auth():
    """Read admin auth settings from env vars (set by HA from config.yaml) and store hashed password."""
    global _admin_auth

    # Cookie hardening. Note: SESSION_COOKIE_SECURE stays off on purpose - HA
    # Ingress talks plain HTTP to the add-on, and a Secure cookie would never
    # be sent back, locking the user out of the panel.
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )

    enabled = os.environ.get("ADMIN_PASSWORD_ENABLED", "false").lower() in ("true", "1", "yes")
    if not enabled:
        # Still needs a key: the CSRF token lives in the session either way.
        app.secret_key, _ = _session_key()
        _admin_auth = {"enabled": False}
        return

    username = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
    password = os.environ.get("ADMIN_PASSWORD", "").strip()
    key, auth = _session_key()
    app.secret_key = key

    if not password:
        # Protection was switched on but no password was given. Previously the
        # add-on silently disabled the protection here and served everything to
        # the LAN. Now it locks itself down until a password is configured.
        _admin_auth = {"enabled": True, "setup_required": True, "username": username}
        print("[AUTH] Passwortschutz ist aktiv, aber kein Passwort gesetzt - "
              "Oberflaeche gesperrt bis eines konfiguriert ist.", flush=True)
        return

    auth["enabled"] = True
    auth["username"] = username
    auth["password_hash"] = generate_password_hash(password)
    save_json(ADMIN_AUTH_FILE, auth)
    _admin_auth = auth


def _base():
    """Return the HA Ingress base path (e.g. /api/hassio_ingress/TOKEN) or '' for direct access."""
    return request.headers.get("X-Ingress-Path", "").rstrip("/")

def _client_ip():
    """Return the real client IP, looking through proxy headers."""
    return request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()


SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def csrf_token():
    """Per-session token. Issued on the first request, then reused."""
    tok = session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["csrf"] = tok
    return tok


@app.before_request
def check_auth():
    # 1. Locked down: protection is on but no password is configured.
    if _admin_auth.get("setup_required"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Kein Admin-Passwort gesetzt. Bitte in der "
                                     "Addon-Konfiguration eines eintragen."}), 503
        if request.endpoint != "setup_required_page":
            return redirect(_base() + "/setup-required")
        return

    if not _admin_auth.get("enabled"):
        csrf_token()
        return _csrf_check()

    if request.endpoint in ("login", "logout"):
        return
    if not session.get("authenticated"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Unauthorized"}), 401
        return redirect(_base() + "/login")

    # 2. The password may have changed since this session was created.
    if session.get("auth_epoch") != _auth_epoch(_admin_auth.get("password_hash", "")):
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify({"error": "Sitzung abgelaufen"}), 401
        return redirect(_base() + "/login")

    csrf_token()
    return _csrf_check()


def _csrf_check():
    """Reject state-changing API calls without a matching token.

    SameSite=Lax already blocks cross-site form posts; this additionally covers
    same-site sources such as another add-on on the same host.
    """
    if request.method in SAFE_METHODS or not request.path.startswith("/api/"):
        return
    sent = request.headers.get("X-CSRF-Token", "")
    if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
        print(f"[CSRF] abgewiesen: {request.method} {request.path} ip={_client_ip()}", flush=True)
        return jsonify({"error": "Sitzung ungültig, bitte Seite neu laden"}), 403


@app.route("/setup-required")
def setup_required_page():
    return render_template("setup_required.html",
                           username=_admin_auth.get("username", "admin")), 503


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if (username == _admin_auth.get("username") and
                check_password_hash(_admin_auth.get("password_hash", ""), password)):
            session.clear()                      # fresh session id after login
            session.permanent = True
            session["authenticated"] = True
            session["user"] = username
            session["auth_epoch"] = _auth_epoch(_admin_auth.get("password_hash", ""))
            csrf_token()
            print(f"[AUTH] Login successful: user='{username}' ip={_client_ip()}", flush=True)
            return redirect(_base() + "/")
        print(f"[AUTH FAIL] Login failed: user='{username}' ip={_client_ip()}", flush=True)
        error = "Ungültige Anmeldedaten / Invalid credentials"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    print(f"[AUTH] Logout: ip={_client_ip()}", flush=True)
    session.clear()
    return redirect(_base() + "/login")

# ─────────────────────────── helpers ────────────────────────────

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

_backup_lock = False

def _copy_data_to(dest_dir):
    """Copy all settings from /data to dest_dir."""
    import time
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, "meta.json"), "w") as f:
        json.dump({"timestamp": time.time()}, f)
    for fname in ("shares.json", "users.json", "groups.json",
                  "mounts.json", "backups.json", "admin_auth.json",
                  "file_access.json", "share_links.json", "share_accounts.json", "share_auth.json",
                  "crowdsec_setup.json"):
        src = os.path.join(DATA_DIR, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest_dir, fname))
    samba_src = os.path.join(DATA_DIR, "samba")
    if os.path.isdir(samba_src):
        samba_dst = os.path.join(dest_dir, "samba")
        os.makedirs(samba_dst, exist_ok=True)
        for f in os.listdir(samba_src):
            shutil.copy2(os.path.join(samba_src, f), os.path.join(samba_dst, f))

def _restore_from(src_dir):
    """Restore settings from src_dir to /data."""
    for fname in ("shares.json", "users.json", "groups.json",
                  "mounts.json", "backups.json", "admin_auth.json",
                  "file_access.json", "share_links.json", "share_accounts.json", "share_auth.json",
                  "crowdsec_setup.json"):
        src = os.path.join(src_dir, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(DATA_DIR, fname))
    samba_src = os.path.join(src_dir, "samba")
    if os.path.isdir(samba_src):
        samba_dst = os.path.join(DATA_DIR, "samba")
        os.makedirs(samba_dst, exist_ok=True)
        for f in os.listdir(samba_src):
            shutil.copy2(os.path.join(samba_src, f), os.path.join(samba_dst, f))

def _mirroring_enabled():
    """The auto-backup writes into the add-on's /config mapping. Off unless we
    really run from the add-on's data directory - a test run or a one-off
    start on a workstation must not scribble into whatever /config is there."""
    return os.path.abspath(DATA_DIR) == os.path.abspath("/data")


def _auto_backup():
    """Sync /data to auto-backup slot (reinstall-safe, always overwritten)."""
    global _backup_lock
    if _backup_lock:
        return
    _backup_lock = True
    try:
        _copy_data_to(CONFIG_AUTO_DIR)
    except Exception as e:
        print(f"[SETTINGS-BACKUP] Auto-backup error: {e}", flush=True)
    finally:
        _backup_lock = False

def save_json(path, data, mirror=True):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    # Auto-sync to reinstall-safe location after every settings change.
    # mirror=False for things written per request (download counters).
    if mirror and _mirroring_enabled() and \
            os.path.dirname(os.path.abspath(path)) == os.path.abspath(DATA_DIR):
        _auto_backup()


sharing_store.init(DATA_DIR, load_json, save_json)

# Run setup at import time (covers both __main__ and WSGI server invocations)
_setup_admin_auth()

def backup_samba_passwords():
    """Copy Samba password database to /data/ for persistence across restarts."""
    import glob
    os.makedirs("/data/samba", exist_ok=True)
    for src in ["/var/lib/samba/private/passdb.tdb",
                "/var/lib/samba/private/secrets.tdb",
                "/etc/samba/smbpasswd"]:
        if os.path.exists(src):
            dst = f"/data/samba/{os.path.basename(src)}"
            try:
                shutil.copy2(src, dst)
            except Exception:
                pass

def reload_samba():
    """Regenerate smb.conf and signal smbd to reload. Restart if needed."""
    subprocess.run(["python3", "/app/generate_smb_conf.py", WORKGROUP, NAS_NAME, SMB_PORT], check=False)
    # Try smbcontrol first
    try:
        rc = subprocess.run(["smbcontrol", "smbd", "reload-config"],
                            capture_output=True, timeout=5).returncode
    except Exception:
        rc = 1
    if rc != 0:
        # smbcontrol failed - try HUP signal
        rc2 = subprocess.run(["pkill", "-HUP", "smbd"], capture_output=True).returncode
        if rc2 != 0:
            # smbd not running at all - restart it
            subprocess.Popen(["smbd", "--foreground", "--no-process-group"],
                             stdout=open("/var/log/samba/smbd.log", "a"),
                             stderr=subprocess.STDOUT)
            subprocess.Popen(["nmbd", "--foreground", "--no-process-group"],
                             stdout=open("/var/log/samba/nmbd.log", "a"),
                             stderr=subprocess.STDOUT)

def get_by_id_path(dev_name):
    """Return the most readable stable /dev/disk/by-id/ symlink for dev_name (e.g. 'sdb1').
    Returns the full by-id path string, or None if not available."""
    real_dev = os.path.realpath(f"/dev/{dev_name}")
    by_id_dir = "/dev/disk/by-id"
    if not os.path.isdir(by_id_dir):
        return None
    candidates = []
    try:
        for link in os.listdir(by_id_dir):
            link_path = os.path.join(by_id_dir, link)
            try:
                if os.path.realpath(link_path) == real_dev:
                    candidates.append(link)
            except Exception:
                pass
    except Exception:
        return None
    if not candidates:
        return None
    # Prefer human-readable prefixes; avoid wwn- / dm- / md-
    for prefix in ("usb-", "ata-", "nvme-", "mmc-", "scsi-"):
        for c in sorted(candidates):
            if c.startswith(prefix):
                return os.path.join(by_id_dir, c)
    return os.path.join(by_id_dir, sorted(candidates)[0])


def get_system_devices():
    """Return a set of device names (e.g. 'sda', 'sda1', 'mmcblk0p1') that back
    critical system mount points so the UI can warn before touching them."""
    critical = {'/', '/boot', '/boot/efi', '/usr', '/var', '/homeassistant', '/mnt/data'}
    system_devs = set()
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 2:
                    continue
                dev_raw, mp = parts[0], parts[1]
                if mp not in critical:
                    continue
                real = os.path.realpath(dev_raw)
                name = os.path.basename(real)
                system_devs.add(name)
                # Also flag the parent disk (strip partition suffix p?N)
                parent = re.sub(r'p?\d+$', '', name)
                if parent != name:
                    system_devs.add(parent)
    except Exception:
        pass
    return system_devs


def get_disk_usage(path):
    try:
        usage = shutil.disk_usage(path)
        return {
            "total": usage.total, "used": usage.used, "free": usage.free,
            "percent": round(usage.used / usage.total * 100, 1) if usage.total else 0,
        }
    except Exception:
        return None

# ─────────────────────────── drives API ─────────────────────────

def update_fstype_memory(updates):
    """Persist freshly-known FS types so unmounted-but-known drives keep
    their tag across restarts. Updates only — never deletes entries."""
    if not updates:
        return
    try:
        mem = load_json(FSTYPE_MEMORY_FILE, {}) or {}
    except Exception:
        mem = {}
    changed = False
    for name, fs in updates.items():
        if name and fs and fs != "auto" and mem.get(name) != fs:
            mem[name] = fs
            changed = True
    if changed:
        try: save_json(FSTYPE_MEMORY_FILE, mem)
        except Exception: pass

def get_proc_mounts_fstypes():
    """Return {device_basename: fstype} for everything currently mounted.
    Uses /proc/mounts where the kernel always knows the resolved FS type.
    Resolves symlinks (e.g. /dev/disk/by-id/...) to /dev/sdXN."""
    out = {}
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                dev_raw, _, fs = parts[0], parts[1], parts[2]
                if not dev_raw.startswith("/dev/"):
                    continue
                try:
                    real = os.path.realpath(dev_raw)
                except Exception:
                    real = dev_raw
                out[os.path.basename(real)] = fs
    except Exception:
        pass
    return out

def probe_fstype(dev_path):
    """Detect filesystem type when lsblk's FSTYPE column is empty.
    Tries blkid → blkid -p → file -sL. Note: HA-OS often blocks raw block
    reads with EPERM, so all of these may return nothing for unmounted
    USB devices — that's why we also fall back to /proc/mounts elsewhere."""
    if not dev_path or not os.path.exists(dev_path):
        return None
    for cmd in (["blkid", "-o", "value", "-s", "TYPE", dev_path],
                ["blkid", "-p", "-o", "value", "-s", "TYPE", dev_path]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            t = (r.stdout or "").strip().splitlines()[0:1]
            if t and t[0]:
                return t[0]
        except Exception:
            pass
    try:
        r = subprocess.run(["file", "-sL", dev_path],
                           capture_output=True, text=True, timeout=3)
        s = (r.stdout or "").lower()
        for needle, fs in (("ext4", "ext4"), ("ext3", "ext3"), ("ext2", "ext2"),
                           ("ntfs", "ntfs"), ("exfat", "exfat"),
                           ("fat (", "vfat"), ("dos/mbr", "vfat"),
                           ("btrfs", "btrfs"), ("xfs", "xfs")):
            if needle in s:
                return fs
    except Exception:
        pass
    return None

def list_block_devices():
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-o",
             "NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINT,UUID,MODEL,VENDOR,HOTPLUG,RM"],
            capture_output=True, text=True, check=True)
        return json.loads(result.stdout).get("blockdevices", [])
    except Exception:
        return []

def flatten_devices(devices, parent=None, mounted_fs=None, mem_fs=None):
    if mounted_fs is None:
        mounted_fs = get_proc_mounts_fstypes()
        # Seed the persistent memory with whatever the kernel currently
        # reports — so once a drive has been mounted, the tag keeps
        # showing after unmount and across restarts.
        update_fstype_memory(mounted_fs)
    if mem_fs is None:
        # Also pick up fstypes saved in mounts.json (backwards-compat) and
        # the new fstype_memory.json
        mem_fs = {}
        try:
            for m in load_json(MOUNTS_FILE, []):
                resolved = m.get("resolved_fstype") or m.get("fstype")
                dev_real = os.path.basename(os.path.realpath(m.get("device", "")))
                if resolved and resolved != "auto" and dev_real:
                    mem_fs[dev_real] = resolved
        except Exception: pass
        try:
            mem_fs.update(load_json(FSTYPE_MEMORY_FILE, {}) or {})
        except Exception: pass
    result = []
    for dev in devices:
        name = dev.get("name")
        # FS type sources, in order: lsblk → /proc/mounts → memory file/mounts.json → probe
        fstype = dev.get("fstype")
        if not fstype: fstype = mounted_fs.get(name)
        if not fstype: fstype = mem_fs.get(name)
        if not fstype and dev.get("type") == "part":
            fstype = probe_fstype(f"/dev/{name}")
        d = {
            "name": name, "path": f"/dev/{name}",
            "by_id": get_by_id_path(name),
            "size": dev.get("size", "?"), "type": dev.get("type"),
            "fstype": fstype, "label": dev.get("label"),
            "mountpoint": dev.get("mountpoint"), "uuid": dev.get("uuid"),
            "model": dev.get("model") or (parent.get("model") if parent else None),
            "vendor": dev.get("vendor") or (parent.get("vendor") if parent else None),
            "removable": dev.get("rm") == "1" or dev.get("hotplug") == "1",
            "parent": parent.get("name") if parent else None,
        }
        result.append(d)
        for child in dev.get("children", []):
            result.extend(flatten_devices([child], dev, mounted_fs, mem_fs))
    return result

def _has_medium(name):
    """Return True if the disk has an actual medium inserted.
    Reads /sys/block/<name>/size — 0 means empty card-reader slot,
    USB hub port without device, etc."""
    try:
        with open(f"/sys/block/{name}/size") as f:
            return int(f.read().strip()) > 0
    except Exception:
        return True  # if we can't tell, don't hide it


@app.route("/api/drives")
def api_drives():
    system_devs = get_system_devices()
    devices = flatten_devices(list_block_devices())
    drives = [d for d in devices if d["type"] in ("disk", "part")]
    # Hide phantom disks (card-reader slots without medium etc.)
    drives = [d for d in drives
              if d["type"] != "disk" or _has_medium(d["name"])]
    for d in drives:
        d["system_device"] = d["name"] in system_devs
    return jsonify(drives)


def _run_smartctl(dev_path):
    """Run smartctl with several fallback modes for USB bridges.
    Returns the parsed JSON dict (smartctl --json always emits valid JSON
    even on error, with smartctl.exit_status describing the issue)."""
    attempts = [
        ["smartctl", "--json", "-a", dev_path],
        ["smartctl", "--json", "-a", "-d", "sat", dev_path],
        ["smartctl", "--json", "-a", "-d", "scsi", dev_path],
        ["smartctl", "--json", "-a", "-d", "usbjmicron", dev_path],
        ["smartctl", "--json", "-a", "-d", "usbprolific", dev_path],
        ["smartctl", "--json", "-a", "-d", "usbsunplus", dev_path],
    ]
    last = None
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            data = json.loads(r.stdout) if r.stdout else {}
            last = data
            # exit_status bits: 0=ok, 1=cmdline err, 2=device open failed,
            # 3=some smart cmd failed, etc. 0,4,64,128 are "device responded".
            status = data.get("smartctl", {}).get("exit_status", -1)
            if status in (0, 4, 64, 128):
                data["_smartctl_cmd"] = " ".join(cmd)
                return data
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
            last = {"error": str(e)}
    if isinstance(last, dict):
        last["_smartctl_cmd"] = "(all attempts failed)"
    return last or {"error": "smartctl failed"}


def _smart_summary(data):
    """Boil the full smartctl JSON down to the fields we want to show."""
    s = {
        "device":           data.get("device", {}).get("name"),
        "model":            data.get("model_name") or data.get("scsi_model_name"),
        "serial":           data.get("serial_number"),
        "firmware":         data.get("firmware_version"),
        "capacity_bytes":   data.get("user_capacity", {}).get("bytes"),
        "rotation_rate":    data.get("rotation_rate"),   # 0 = SSD
        "smart_supported":  data.get("smart_support", {}).get("available", False),
        "smart_enabled":    data.get("smart_support", {}).get("enabled", False),
        "passed":           data.get("smart_status", {}).get("passed"),
        "temperature_c":    (data.get("temperature") or {}).get("current"),
        "power_on_hours":   (data.get("power_on_time") or {}).get("hours"),
        "power_cycles":     data.get("power_cycle_count"),
        "attributes":       [],
        "errors":           None,
        "raw":               data,                       # full JSON for debugging
    }
    # ATA attribute table: pull a curated set if present
    keep = {1: "Read Error Rate", 5: "Reallocated Sectors", 9: "Power-On Hours",
            12: "Power Cycle Count", 187: "Reported Uncorrectable", 188: "Command Timeout",
            190: "Airflow Temperature", 194: "Temperature", 196: "Reallocation Events",
            197: "Pending Sectors", 198: "Offline Uncorrectable",
            199: "UDMA CRC Errors", 231: "SSD Life Left", 241: "Total LBAs Written"}
    for a in (data.get("ata_smart_attributes") or {}).get("table", []) or []:
        aid = a.get("id")
        if aid in keep:
            s["attributes"].append({
                "id":     aid,
                "name":   keep[aid],
                "value":  a.get("value"),
                "worst":  a.get("worst"),
                "thresh": a.get("thresh"),
                "raw":    (a.get("raw") or {}).get("value"),
                "raw_str":(a.get("raw") or {}).get("string"),
                "failing":a.get("when_failed") not in (None, "", "-"),
            })
    # NVMe-style health log (if present)
    nvme = data.get("nvme_smart_health_information_log")
    if nvme:
        s["nvme"] = {
            "critical_warning":     nvme.get("critical_warning"),
            "percentage_used":      nvme.get("percentage_used"),
            "available_spare":      nvme.get("available_spare"),
            "available_spare_thresh": nvme.get("available_spare_threshold"),
            "media_errors":         nvme.get("media_errors"),
            "unsafe_shutdowns":     nvme.get("unsafe_shutdowns"),
            "data_units_read":      nvme.get("data_units_read"),
            "data_units_written":   nvme.get("data_units_written"),
        }
    # error counts (ATA)
    errlog = data.get("ata_smart_error_log") or {}
    if errlog:
        s["errors"] = (errlog.get("summary") or {}).get("count")
    return s


@app.route("/api/drives/<name>/smart")
def api_drive_smart(name):
    # Whitelist: only allow real device names from our list
    devices = flatten_devices(list_block_devices())
    if not any(d["name"] == name for d in devices):
        return jsonify({"error": "unknown device"}), 404
    # SMART on partitions makes no sense — use the parent disk
    parent = name
    if name and name[-1:].isdigit():
        # strip trailing digits (sdb1 -> sdb, nvme0n1p1 -> nvme0n1)
        import re
        parent = re.sub(r"(p?\d+)$", "", name)
    dev_path = f"/dev/{parent}"
    raw = _run_smartctl(dev_path)
    summary = _smart_summary(raw)
    summary["queried_device"] = dev_path
    return jsonify(summary)


MOUNT_FIFO   = "/tmp/mount_cmd"
MOUNT_RESULT = "/tmp/mount_result"

def _helper_call(action, arg1="", arg2="", arg3="", timeout=20):
    import time
    deadline_fifo = time.time() + 10
    while not os.path.exists(MOUNT_FIFO):
        if time.time() > deadline_fifo:
            return 1, "Mount-Helper FIFO nicht gefunden"
        time.sleep(0.2)
    try: os.remove(MOUNT_RESULT)
    except FileNotFoundError: pass
    cmd_line = f"{action}|{arg1}|{arg2}|{arg3}\n"
    # Retry a few times on ENXIO — the helper script briefly has no reader
    # on the FIFO between iterations (race window when it re-opens).
    last_err = None
    for attempt in range(20):  # ~2s total
        try:
            fd = os.open(MOUNT_FIFO, os.O_WRONLY | os.O_NONBLOCK)
            os.write(fd, cmd_line.encode())
            os.close(fd)
            last_err = None
            break
        except OSError as e:
            last_err = e
            if e.errno == 6:  # ENXIO: no reader yet
                time.sleep(0.1)
                continue
            return 1, f"FIFO-Fehler: {e}"
    if last_err is not None:
        return 1, f"FIFO-Fehler: {last_err}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(MOUNT_RESULT):
            try:
                with open(MOUNT_RESULT) as f:
                    line = f.read().strip()
                rc_str, _, out = line.partition("|")
                return int(rc_str), out
            except Exception: pass
        time.sleep(0.1)
    return 1, "Timeout"

@app.route("/api/drives/mount", methods=["POST"])
def api_mount():
    body       = request.get_json(force=True)
    device     = body.get("device", "").strip()
    mountpoint = body.get("mountpoint", "").strip()
    fstype     = body.get("fstype", "auto")

    if not device or not mountpoint:
        return jsonify({"error": "device and mountpoint required"}), 400

    # Resolve to stable by-id path — prevents /dev/sdX reordering across reboots
    dev_name   = os.path.basename(os.path.realpath(device))
    by_id      = get_by_id_path(dev_name)
    stable_dev = by_id if by_id else device
    if by_id:
        print(f"[MOUNT] Resolved {device} → {by_id}", flush=True)
    else:
        print(f"[MOUNT] No by-id path found for {device}, using raw device path", flush=True)

    # If mountpoint doesn't start with /, treat as name under /media
    if not mountpoint.startswith("/"):
        safe_name  = re.sub(r"[^a-zA-Z0-9_\-]", "_", mountpoint)
        mountpoint = f"/media/{safe_name}"

    # A mount point may only ever be under /media or /mnt - this runs as root
    # and an arbitrary target would shadow any directory on the host.
    mountpoint = _safe(mountpoint, roots=["/media", "/mnt"])

    _helper_call("MKDIR", mountpoint)
    os.makedirs(mountpoint, exist_ok=True)

    rc, out = _helper_call("MOUNT", stable_dev, mountpoint, fstype)
    if rc != 0:
        return jsonify({"error": out or "Mount fehlgeschlagen"}), 500

    # Resolve the actual FS type the kernel is using — for the drive list tag
    resolved_fs = fstype
    if fstype == "auto":
        try:
            with open("/proc/mounts") as f:
                for ln in f:
                    p = ln.split()
                    if len(p) >= 3 and p[1] == mountpoint:
                        resolved_fs = p[2]
                        break
        except Exception:
            pass
    # /proc/mounts reports ntfs-3g (FUSE) mounts as "fuseblk" — normalize to a
    # meaningful tag and a value that mount_fs understands on restore.
    if resolved_fs == "fuseblk":
        resolved_fs = "ntfs"
    # Persist the resolved type so the tag stays visible after unmount
    if resolved_fs and resolved_fs != "auto":
        update_fstype_memory({os.path.basename(os.path.realpath(stable_dev)): resolved_fs})

    mounts = load_json(MOUNTS_FILE, [])
    mounts = [m for m in mounts if m.get("mountpoint") != mountpoint]
    mounts.append({
        "device":         stable_dev,
        "dev_path":       device,
        "mountpoint":     mountpoint,
        "fstype":         fstype,
        "resolved_fstype": resolved_fs,
    })
    save_json(MOUNTS_FILE, mounts)
    reload_samba()
    return jsonify({"ok": True, "mountpoint": mountpoint, "stable_device": stable_dev})

@app.route("/api/drives/unmount", methods=["POST"])
def api_unmount():
    body       = request.get_json(force=True)
    mountpoint = body.get("mountpoint", "").strip()
    device     = body.get("device", "").strip()
    if not mountpoint and not device:
        return jsonify({"error": "mountpoint or device required"}), 400
    target = mountpoint or device
    force  = bool(body.get("force", False))

    mounts = load_json(MOUNTS_FILE, [])

    # 1) Tell smbd to close all shares whose path lies under the mountpoint,
    #    so it releases its file handles before we unmount.
    try:
        shares = load_json(SHARES_FILE, [])
        affected = [s.get("name") for s in shares
                    if s.get("path", "").rstrip("/").startswith(mountpoint.rstrip("/"))
                    and s.get("name")]
        for sname in affected:
            subprocess.run(["smbcontrol", "all", "close-share", sname],
                           capture_output=True, timeout=5)
        if affected:
            print(f"[UNMOUNT] Closed Samba handles for: {', '.join(affected)}", flush=True)
    except Exception as e:
        print(f"[UNMOUNT] close-share failed: {e}", flush=True)

    def _umount_with_fallback(path):
        """Try plain umount, fall back to lazy umount on force or busy."""
        rc, out = _helper_call("UMOUNT", path)
        if rc == 0:
            return 0, out, False
        is_busy = "busy" in (out or "").lower() or "target is busy" in (out or "").lower()
        if force and is_busy:
            rc2, out2 = _helper_call("UMOUNT_LAZY", path)
            return rc2, out2, True
        return rc, out, False

    # 2) Unmount
    rc, out, lazy = _umount_with_fallback(target)
    if rc != 0:
        # Collect diagnostic info on what's holding it
        _, who = _helper_call("FUSER", target, timeout=5)
        return jsonify({
            "error": out or "Aushängen fehlgeschlagen",
            "busy": "busy" in (out or "").lower(),
            "holders": who,
        }), 500
    mounts = [m for m in mounts if m.get("mountpoint") != mountpoint and m.get("device") != device]
    save_json(MOUNTS_FILE, mounts)
    # Regenerate smb.conf — shares on this mount point become unavailable
    reload_samba()
    return jsonify({"ok": True})

# ─────────────────────────── disk / partition API ───────────────────

_DEVNAME_RE = re.compile(r'^[a-zA-Z0-9_]+$')


def _safe_disk_path(name):
    """Return /dev/<name> if name is a plain block device name and the
    device exists, otherwise None. Blocks path-traversal / shell tricks."""
    if not name or not _DEVNAME_RE.match(name):
        return None
    path = f"/dev/{name}"
    try:
        st = os.stat(path)
    except OSError:
        return None
    import stat as _stat
    if not _stat.S_ISBLK(st.st_mode):
        return None
    return path


def _is_system_disk(name):
    sys_devs = get_system_devices()
    # name might be a partition (sdc1) or whole disk (sdc) — strip partition suffix too
    base = re.sub(r'p?\d+$', '', name)
    return name in sys_devs or base in sys_devs


def _parse_parted(text):
    """Parse `parted -m -s <dev> unit MiB print free` output."""
    lines = [l for l in text.strip().split('\n') if l]
    if not lines or not lines[0].startswith('BYT'):
        return None
    if len(lines) < 2:
        return None
    di = lines[1].rstrip(';').split(':')
    if len(di) < 6:
        return None
    def to_mib(s):
        try: return float(s.replace('MiB', '').replace('MB', '').replace('GB', ''))
        except: return 0.0
    disk = {
        'path':     di[0],
        'size_mib': to_mib(di[1]),
        'table':    di[5] if len(di) > 5 else '',
        'model':    di[6] if len(di) > 6 else '',
    }
    parts = []
    for line in lines[2:]:
        f = line.rstrip(';').split(':')
        if len(f) < 4:
            continue
        start, end, size = to_mib(f[1]), to_mib(f[2]), to_mib(f[3])
        is_free = (len(f) >= 5 and 'free' in f[4].lower())
        if is_free:
            parts.append({
                'free':     True,
                'start_mib': start, 'end_mib': end, 'size_mib': size,
            })
        else:
            try:    num = int(f[0])
            except: num = 0
            parts.append({
                'free':     False,
                'num':      num,
                'start_mib': start, 'end_mib': end, 'size_mib': size,
                'fstype':   f[4] if len(f) > 4 else '',
                'label':    f[5] if len(f) > 5 else '',
                'flags':    f[6] if len(f) > 6 else '',
            })
    return {'disk': disk, 'partitions': parts}


def _mounted_paths():
    """Return {device_path: mountpoint} from /proc/mounts.
    Resolves realpath so /dev/disk/by-id/... matches /dev/sdb2.
    `in _mounted_paths()` still works (dict membership)."""
    out = {}
    try:
        with open('/proc/mounts') as f:
            for line in f:
                p = line.split()
                if len(p) >= 2 and p[0].startswith('/dev/'):
                    out.setdefault(p[0], p[1])
                    try:
                        real = os.path.realpath(p[0])
                        out.setdefault(real, p[1])
                    except Exception:
                        pass
    except Exception: pass
    return out


@app.route("/api/disk/<name>/partitions", methods=["GET"])
def api_disk_partitions(name):
    path = _safe_disk_path(name)
    if not path:
        return jsonify({"error": f"invalid disk: {name}"}), 400
    if not _has_medium(name):
        return jsonify({"error": f"no medium in /dev/{name} (empty card-reader / hub port)"}), 410
    rc, out = _helper_call("PARTLIST", path, timeout=15)
    # decode the \n encoding from helper
    out = (out or "").replace('\\n', '\n')
    parsed = _parse_parted(out) if rc == 0 else None
    if not parsed:
        # Disk might have no partition table at all — return blank layout
        try: sz_bytes = int(open(f"/sys/class/block/{name}/size").read().strip()) * 512
        except: sz_bytes = 0
        return jsonify({
            "disk": {"path": path, "name": name, "size_mib": sz_bytes / 1024 / 1024,
                     "table": "", "model": ""},
            "partitions": [],
            "system_device": _is_system_disk(name),
            "raw_error": out if rc != 0 else None,
        })
    parsed["disk"]["name"] = name
    parsed["system_device"] = _is_system_disk(name)
    mounted = _mounted_paths()
    for p in parsed["partitions"]:
        if not p["free"]:
            p["device"] = f"{path}{p['num']}" if not name[-1].isdigit() else f"{path}p{p['num']}"
            p["mountpoint"] = mounted.get(p["device"])
            p["mounted"] = p["mountpoint"] is not None
    return jsonify(parsed)


@app.route("/api/disk/<name>/init", methods=["POST"])
def api_disk_init(name):
    path = _safe_disk_path(name)
    if not path:
        return jsonify({"error": f"invalid disk: {name}"}), 400
    if _is_system_disk(name):
        return jsonify({"error": "Refusing to touch a system disk"}), 403
    body  = request.get_json(force=True) or {}
    table = body.get("table", "gpt")
    if table not in ("gpt", "msdos"):
        return jsonify({"error": "table must be 'gpt' or 'msdos'"}), 400
    if body.get("confirm") != name:
        return jsonify({"error": "confirmation does not match"}), 400
    # Pre-check: parted refuses mklabel ("Partition(s) on /dev/sdX are being
    # used.") if ANY partition of the disk is currently mounted. List them
    # explicitly so the user knows exactly what to unmount.
    mounted = _mounted_paths()
    busy = []
    prefix_p = f"{path}p"
    prefix_n = path
    for dev, mp in mounted.items():
        if dev.startswith(prefix_p) and dev[len(prefix_p):].isdigit():
            busy.append((dev, mp))
        elif dev.startswith(prefix_n) and dev[len(prefix_n):].isdigit():
            busy.append((dev, mp))
    if busy:
        listing = ", ".join(f"{d} → {m}" for d, m in busy)
        return jsonify({"error": f"Unmount first: {listing}"}), 409
    rc, out = _helper_call("PARTMKLABEL", path, table, timeout=30)
    if rc != 0:
        return jsonify({"error": out or "mklabel failed"}), 500
    return jsonify({"ok": True})


@app.route("/api/disk/<name>/partition", methods=["POST"])
def api_disk_partition_add(name):
    path = _safe_disk_path(name)
    if not path:
        return jsonify({"error": f"invalid disk: {name}"}), 400
    if _is_system_disk(name):
        return jsonify({"error": "Refusing to touch a system disk"}), 403
    body = request.get_json(force=True) or {}
    if body.get("confirm") != name:
        return jsonify({"error": "confirmation does not match"}), 400
    try:
        start_mib = float(body.get("start_mib"))
    except (TypeError, ValueError):
        return jsonify({"error": "start_mib required (number)"}), 400
    # Align start: parted refuses to place a partition in the GPT header
    # region (first ~1 MiB) and also wants MiB-aligned starts for performance.
    # Round UP to the next whole MiB, with a minimum of 1 MiB.
    import math
    start_mib = max(1.0, math.ceil(start_mib))
    end = body.get("end_mib", "100%")
    if isinstance(end, str) and end.strip().endswith("%"):
        end_arg = end.strip()
    else:
        try:
            # Round end DOWN to whole MiB so we never overflow the free region.
            end_mib = math.floor(float(end))
            if end_mib <= start_mib:
                return jsonify({"error": f"end_mib ({end_mib}) must be greater than start_mib ({start_mib})"}), 400
            end_arg = f"{end_mib}MiB"
        except (TypeError, ValueError):
            return jsonify({"error": "end_mib invalid"}), 400
    rc, out = _helper_call("PARTADD", path, f"{int(start_mib)}", end_arg, timeout=60)
    if rc != 0:
        return jsonify({"error": out or "mkpart failed"}), 500
    return jsonify({"ok": True})


@app.route("/api/disk/<name>/partition/<int:num>", methods=["DELETE"])
def api_disk_partition_rm(name, num):
    path = _safe_disk_path(name)
    if not path:
        return jsonify({"error": f"invalid disk: {name}"}), 400
    if _is_system_disk(name):
        return jsonify({"error": "Refusing to touch a system disk"}), 403
    body = request.get_json(silent=True) or {}
    part_name = f"{name}{num}" if not name[-1].isdigit() else f"{name}p{num}"
    if body.get("confirm") != part_name:
        return jsonify({"error": "confirmation does not match"}), 400
    part_path = f"{path}{num}" if not name[-1].isdigit() else f"{path}p{num}"
    if part_path in _mounted_paths():
        return jsonify({"error": "Partition is mounted — unmount first"}), 409
    rc, out = _helper_call("PARTRM", path, str(num), timeout=30)
    if rc != 0:
        return jsonify({"error": out or "rm failed"}), 500
    return jsonify({"ok": True})


@app.route("/api/disk/<name>/partition/<int:num>/format", methods=["POST"])
def api_partition_format(name, num):
    path = _safe_disk_path(name)
    if not path:
        return jsonify({"error": f"invalid disk: {name}"}), 400
    if _is_system_disk(name):
        return jsonify({"error": "Refusing to touch a system disk"}), 403
    body = request.get_json(force=True) or {}
    part_name = f"{name}{num}" if not name[-1].isdigit() else f"{name}p{num}"
    if body.get("confirm") != part_name:
        return jsonify({"error": "confirmation does not match"}), 400
    fstype = (body.get("fstype") or "").strip().lower()
    if fstype not in ("ext4", "ext3", "ext2", "exfat", "vfat", "fat32", "ntfs"):
        return jsonify({"error": f"unsupported fstype: {fstype}"}), 400
    label = (body.get("label") or "").strip()
    # Cap label length per FS limits
    label_limits = {"vfat": 11, "fat32": 11, "exfat": 15, "ext2": 16, "ext3": 16, "ext4": 16, "ntfs": 32}
    label = label[:label_limits.get(fstype, 16)]
    part_path = f"{path}{num}" if not name[-1].isdigit() else f"{path}p{num}"
    if part_path in _mounted_paths():
        return jsonify({"error": "Partition is mounted — unmount first"}), 409
    # mkfs.ntfs on a multi-TB drive can take 15+ min; ext4 quick, exfat fast.
    rc, out = _helper_call("MKFS", part_path, fstype, label, timeout=1800)
    if rc != 0:
        return jsonify({"error": out or "mkfs failed"}), 500
    # Refresh known FS-type memory so the drive list shows the new type
    try:
        dev_name = os.path.basename(part_path)
        memory = load_json(FSTYPE_MEMORY_FILE, {}) or {}
        memory[dev_name] = "ntfs" if fstype == "ntfs" else fstype
        save_json(FSTYPE_MEMORY_FILE, memory)
    except Exception: pass
    return jsonify({"ok": True})


# ─────────────────────────── shares API ─────────────────────────

def ensure_share_dir(path):
    """Create the share directory and apply Samba-friendly permissions.

    exFAT / FAT32 / NTFS do not support POSIX permissions, so os.chmod()
    raises PermissionError (EPERM) or OSError (ENOTSUP/EINVAL) on them. That
    is harmless — those filesystems grant access to everyone anyway — so we
    only warn and continue instead of failing share creation. (Issue #8)
    """
    try:
        os.makedirs(path, mode=0o2775, exist_ok=True)
    except FileExistsError:
        pass
    except OSError as e:
        # Directory may already exist on a non-POSIX FS, or the parent is
        # read-only. Re-raise only if the path is genuinely not there.
        if not os.path.isdir(path):
            raise
        print(f"ensure_share_dir: makedirs({path}) warning: {e}", flush=True)
    try:
        os.chmod(path, 0o2775)
    except OSError as e:
        # exFAT/FAT/NTFS have no Unix permission model — skip silently-ish
        print(f"ensure_share_dir: chmod not supported on {path} "
              f"(likely exFAT/FAT/NTFS): {e}", flush=True)

@app.route("/api/shares", methods=["GET"])
def api_get_shares():
    shares = load_json(SHARES_FILE, [])
    for s in shares:
        usage = get_disk_usage(s.get("path", "/"))
        if usage: s["usage"] = usage
    return jsonify(shares)

@app.route("/api/shares", methods=["POST"])
def api_create_share():
    body = request.get_json(force=True)
    name = body.get("name", "").strip()
    path = body.get("path", "").strip().rstrip("/")
    if not name or not path:
        return jsonify({"error": "name and path required"}), 400
    name = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    path = _safe(path)

    # Create directory with Samba-friendly permissions (tolerates exFAT/FAT)
    ensure_share_dir(path)

    shares = load_json(SHARES_FILE, [])
    if any(s["name"] == name for s in shares):
        return jsonify({"error": "Share name already exists"}), 409
    share = {
        "name": name, "path": path,
        "comment": body.get("comment", ""),
        "writable": body.get("writable", True),
        "public": body.get("public", False),
        "users": body.get("users", []),
        "groups": body.get("groups", []),
    }
    shares.append(share)
    save_json(SHARES_FILE, shares)
    reload_samba()
    return jsonify(share), 201

@app.route("/api/shares/<name>", methods=["PUT"])
def api_update_share(name):
    body   = request.get_json(force=True)
    # Strip trailing slashes from path
    if "path" in body:
        body["path"] = _safe(body["path"].strip().rstrip("/"))
    shares = load_json(SHARES_FILE, [])
    for s in shares:
        if s["name"] == name:
            for key in ("comment", "writable", "public", "users", "groups", "path"):
                if key in body: s[key] = body[key]
            # Ensure share directory exists with correct permissions
            p = s.get("path", "")
            if p:
                ensure_share_dir(p)
            save_json(SHARES_FILE, shares)
            reload_samba()
            return jsonify(s)
    return jsonify({"error": "Not found"}), 404

@app.route("/api/shares/<name>", methods=["DELETE"])
def api_delete_share(name):
    shares = load_json(SHARES_FILE, [])
    shares = [s for s in shares if s["name"] != name]
    save_json(SHARES_FILE, shares)
    reload_samba()
    n = sharing_store.disable_links_for_share(name)
    if n:
        print(f"[SHARE] {n} Link(s) deaktiviert, Freigabe {name} geloescht", flush=True)
    return jsonify({"ok": True, "links_disabled": n})

# ─────────────────────────── users API ──────────────────────────

@app.route("/api/users", methods=["GET"])
def api_get_users():
    users = load_json(USERS_FILE, [])
    groups = load_json(GROUPS_FILE, [])
    # Enrich users with their group memberships
    for u in users:
        u["groups"] = [g["name"] for g in groups if u["username"] in g.get("members", [])]
    return jsonify(users)

@app.route("/api/users", methods=["POST"])
def api_create_user():
    body     = request.get_json(force=True)
    username = re.sub(r"[^a-zA-Z0-9_\-]", "", body.get("username", "").strip())
    password = body.get("password", "").strip()
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400
    subprocess.run(["adduser", "-D", "-H", "-s", "/sbin/nologin", username], capture_output=True)
    proc = subprocess.run(["smbpasswd", "-a", "-s", username],
        input=f"{password}\n{password}\n", text=True, capture_output=True)
    if proc.returncode != 0:
        return jsonify({"error": proc.stderr or "Failed to set Samba password"}), 500
    users = load_json(USERS_FILE, [])
    users = [u for u in users if u["username"] != username]
    users.append({"username": username})
    save_json(USERS_FILE, users)
    backup_samba_passwords()
    return jsonify({"username": username}), 201

@app.route("/api/users/<username>", methods=["PUT"])
def api_update_user(username):
    body     = request.get_json(force=True)
    password = body.get("password", "").strip()
    if not password:
        return jsonify({"error": "password required"}), 400
    # Ensure system user exists (may be lost after container restart)
    subprocess.run(["adduser", "-D", "-H", "-s", "/sbin/nologin", username], capture_output=True)
    # Use -a flag to add samba entry if it doesn't exist yet
    proc = subprocess.run(["smbpasswd", "-a", "-s", username],
        input=f"{password}\n{password}\n", text=True, capture_output=True)
    if proc.returncode != 0:
        return jsonify({"error": proc.stderr or "Failed"}), 500
    backup_samba_passwords()
    return jsonify({"ok": True})

@app.route("/api/users/<username>", methods=["DELETE"])
def api_delete_user(username):
    subprocess.run(["smbpasswd", "-x", username], capture_output=True)
    subprocess.run(["deluser", username], capture_output=True)
    users = load_json(USERS_FILE, [])
    users = [u for u in users if u["username"] != username]
    save_json(USERS_FILE, users)
    # Also remove from groups
    groups = load_json(GROUPS_FILE, [])
    for g in groups:
        g["members"] = [m for m in g.get("members", []) if m != username]
    save_json(GROUPS_FILE, groups)
    backup_samba_passwords()
    return jsonify({"ok": True})

# ─────────────────────────── groups API ─────────────────────────

@app.route("/api/groups", methods=["GET"])
def api_get_groups():
    return jsonify(load_json(GROUPS_FILE, []))

@app.route("/api/groups", methods=["POST"])
def api_create_group():
    body = request.get_json(force=True)
    name = re.sub(r"[^a-zA-Z0-9_\-]", "", body.get("name", "").strip())
    if not name:
        return jsonify({"error": "group name required"}), 400
    groups = load_json(GROUPS_FILE, [])
    if any(g["name"] == name for g in groups):
        return jsonify({"error": "Group already exists"}), 409
    # Create system group
    subprocess.run(["addgroup", name], capture_output=True)
    group = {"name": name, "members": body.get("members", [])}
    # Add members to system group
    for m in group["members"]:
        subprocess.run(["addgroup", m, name], capture_output=True)
    groups.append(group)
    save_json(GROUPS_FILE, groups)
    return jsonify(group), 201

@app.route("/api/groups/<name>", methods=["PUT"])
def api_update_group(name):
    body   = request.get_json(force=True)
    groups = load_json(GROUPS_FILE, [])
    for g in groups:
        if g["name"] == name:
            old_members = set(g.get("members", []))
            new_members = set(body.get("members", []))
            # Remove members no longer in group
            for m in old_members - new_members:
                subprocess.run(["delgroup", m, name], capture_output=True)
            # Add new members
            for m in new_members - old_members:
                subprocess.run(["addgroup", m, name], capture_output=True)
            g["members"] = list(new_members)
            save_json(GROUPS_FILE, groups)
            return jsonify(g)
    return jsonify({"error": "Not found"}), 404

@app.route("/api/groups/<name>", methods=["DELETE"])
def api_delete_group(name):
    groups = load_json(GROUPS_FILE, [])
    groups = [g for g in groups if g["name"] != name]
    save_json(GROUPS_FILE, groups)
    subprocess.run(["delgroup", name], capture_output=True)
    return jsonify({"ok": True})

# ─────────────────────────── status API ─────────────────────────

def _protection_mode_active():
    """Detect Home Assistant 'Protection mode' / 'Gesicherter Modus'.

    When protection is enabled, the supervisor strips privileged capabilities
    (notably CAP_SYS_ADMIN, bit 21) even though config.yaml requests them.
    Without CAP_SYS_ADMIN we cannot mount drives, so we surface a clear warning.
    """
    try:
        with open("/proc/self/status", "r") as fh:
            for line in fh:
                if line.startswith("CapEff:"):
                    cap_eff = int(line.split()[1], 16)
                    # CAP_SYS_ADMIN = 21
                    return not (cap_eff & (1 << 21))
    except Exception:
        pass
    return False

@app.route("/api/status")
def api_status():
    import psutil
    smbd_running = any(p.name() == "smbd" for p in psutil.process_iter(["name"]))
    nmbd_running = any(p.name() == "nmbd" for p in psutil.process_iter(["name"]))
    mem  = psutil.virtual_memory()
    cpu  = psutil.cpu_percent(interval=0.1)
    disk = shutil.disk_usage("/")
    return jsonify({
        "smbd": smbd_running, "nmbd": nmbd_running,
        "cpu_percent": cpu,
        "memory": {"total": mem.total, "used": mem.used, "percent": mem.percent},
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free,
                 "percent": round(disk.used / disk.total * 100, 1)},
        "protection_mode": _protection_mode_active(),
        "csrf": csrf_token(),
        "admin_password_enabled": bool(_admin_auth.get("enabled")),
        "user": session.get("user", ""),
    })

@app.route("/api/samba/restart", methods=["POST"])
def api_restart_samba():
    import time
    try:
        # Regenerate smb.conf
        reload_samba()

        # Kill existing
        subprocess.run(["pkill", "-9", "smbd"], check=False)
        subprocess.run(["pkill", "-9", "nmbd"], check=False)
        time.sleep(1)

        # Start fresh - use simple flags, redirect output to log
        subprocess.Popen(["smbd", "--foreground", "--no-process-group"],
                         stdout=open("/var/log/samba/smbd.log", "a"),
                         stderr=subprocess.STDOUT)
        subprocess.Popen(["nmbd", "--foreground", "--no-process-group"],
                         stdout=open("/var/log/samba/nmbd.log", "a"),
                         stderr=subprocess.STDOUT)
        time.sleep(2)

        # Verify
        import psutil
        smbd_ok = any("smbd" in (p.name() or "") for p in psutil.process_iter(["name"]))
        nmbd_ok = any("nmbd" in (p.name() or "") for p in psutil.process_iter(["name"]))

        if not smbd_ok or not nmbd_ok:
            return jsonify({"error": f"smbd={'OK' if smbd_ok else 'FEHLT'}, nmbd={'OK' if nmbd_ok else 'FEHLT'}"}), 500
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─────────────────────────── settings backup API ────────────────

@app.route("/api/settings/backup-status")
def api_settings_backup_status():
    """Auto-backup status (used for reinstall-safe indicator)."""
    meta_file = os.path.join(CONFIG_AUTO_DIR, "meta.json")
    if os.path.exists(meta_file):
        meta = load_json(meta_file, {})
        return jsonify({"available": True, "timestamp": meta.get("timestamp")})
    return jsonify({"available": False})

@app.route("/api/settings/backups", methods=["GET"])
def api_settings_backups_list():
    """List all manual backup snapshots."""
    result = []
    if os.path.isdir(CONFIG_BACKUPS_DIR):
        for name in sorted(os.listdir(CONFIG_BACKUPS_DIR), reverse=True):
            d = os.path.join(CONFIG_BACKUPS_DIR, name)
            meta_file = os.path.join(d, "meta.json")
            if os.path.isdir(d) and os.path.exists(meta_file):
                meta = load_json(meta_file, {})
                result.append({"id": name, "timestamp": meta.get("timestamp"), "label": name})
    return jsonify(result)

@app.route("/api/settings/backup", methods=["POST"])
def api_settings_backup():
    """Create a new manual backup snapshot."""
    import datetime
    name = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dest = os.path.join(CONFIG_BACKUPS_DIR, name)
    try:
        _copy_data_to(dest)
        # Keep only MAX_MANUAL_BACKUPS newest
        entries = sorted(os.listdir(CONFIG_BACKUPS_DIR))
        for old in entries[:-MAX_MANUAL_BACKUPS]:
            shutil.rmtree(os.path.join(CONFIG_BACKUPS_DIR, old), ignore_errors=True)
        meta = load_json(os.path.join(dest, "meta.json"), {})
        print(f"[SETTINGS-BACKUP] Manual snapshot created: {name}", flush=True)
        return jsonify({"ok": True, "id": name, "timestamp": meta.get("timestamp")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/settings/backups/<backup_id>", methods=["DELETE"])
def api_settings_backup_delete(backup_id):
    """Delete a specific manual backup snapshot."""
    # Security: only allow simple timestamp-format IDs, no path traversal
    import re
    if not re.match(r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$', backup_id):
        return jsonify({"error": "Ungültige Backup-ID"}), 400
    dest = os.path.join(CONFIG_BACKUPS_DIR, backup_id)
    if not os.path.isdir(dest):
        return jsonify({"error": "Backup nicht gefunden"}), 404
    shutil.rmtree(dest)
    print(f"[SETTINGS-BACKUP] Snapshot deleted: {backup_id}", flush=True)
    return jsonify({"ok": True})

@app.route("/api/settings/backups/<backup_id>/restore", methods=["POST"])
def api_settings_backup_restore(backup_id):
    """Restore settings from a specific manual backup snapshot."""
    import re
    if not re.match(r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$', backup_id):
        return jsonify({"error": "Ungültige Backup-ID"}), 400
    src = os.path.join(CONFIG_BACKUPS_DIR, backup_id)
    if not os.path.isdir(src):
        return jsonify({"error": "Backup nicht gefunden"}), 404
    try:
        _restore_from(src)
        reload_samba()
        print(f"[SETTINGS-BACKUP] Restored from snapshot: {backup_id}", flush=True)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/settings/restore", methods=["POST"])
def api_settings_restore():
    """Restore from auto-backup slot."""
    if not os.path.exists(os.path.join(CONFIG_AUTO_DIR, "meta.json")):
        return jsonify({"error": "Kein Auto-Backup vorhanden"}), 404
    try:
        _restore_from(CONFIG_AUTO_DIR)
        reload_samba()
        print("[SETTINGS-BACKUP] Auto-backup restored", flush=True)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────── options + path confinement ─────────

_OPTIONS = None


def _opt(key, default=None):
    """Read an add-on option from /data/options.json.

    The Supervisor writes the resolved options there; that is the same file
    bashio reads. Going straight to it avoids exporting list options through
    shell variables in run.sh, which is the kind of plumbing that only breaks
    on the device.
    """
    global _OPTIONS
    if _OPTIONS is None:
        try:
            with open(f"{DATA_DIR}/options.json") as f:
                _OPTIONS = json.load(f)
        except (OSError, ValueError):
            _OPTIONS = {}
    val = _OPTIONS.get(key, default)
    return default if val is None else val


DEFAULT_FILE_ROOTS = ["/media", "/mnt", "/share", "/config", "/addon_configs"]
FILE_ACCESS_FILE = f"{DATA_DIR}/file_access.json"


def file_access():
    """Current file-access settings.

    Whatever the Settings tab last saved wins over the add-on option, because
    the add-on cannot write its own options.json.
    """
    saved = load_json(FILE_ACCESS_FILE, {})
    if isinstance(saved, dict) and saved.get("roots"):
        roots = saved["roots"]
        unlock = bool(saved.get("unlock_system"))
    else:
        roots = _opt("file_allowed_roots", None)
        unlock = False
        if not isinstance(roots, list) or not roots:
            roots = DEFAULT_FILE_ROOTS
    roots = [str(r).strip() for r in roots if str(r).strip()]
    return {"roots": roots or list(DEFAULT_FILE_ROOTS), "unlock_system": unlock}


def file_roots():
    """Directories the file API may touch. Everything else is refused."""
    return file_access()["roots"]


def _deny_list():
    """/data and the kernel pseudo-filesystems always stay shut. The rest is
    only blocked until the admin unlocks it in the Settings tab."""
    if file_access()["unlock_system"]:
        return safepath.HARD_DENY
    return safepath.DENY_ROOTS


def _safe(path, roots=None):
    """Confine a client-supplied path. Raises safepath.PathError, which the
    error handler below turns into a generic 403."""
    return safepath.resolve_in_roots(
        roots if roots is not None else file_roots(), path, deny=_deny_list())


@app.route("/api/settings/file-access", methods=["GET"])
def api_get_file_access():
    acc = file_access()
    return jsonify({
        "roots": acc["roots"],
        "unlock_system": acc["unlock_system"],
        "full_access": any(safepath.real(r) == safepath.real("/") for r in acc["roots"]),
        "defaults": DEFAULT_FILE_ROOTS,
        "always_blocked": list(safepath.HARD_DENY),
        "unlockable": list(safepath.SOFT_DENY),
    })


@app.route("/api/settings/file-access", methods=["POST"])
def api_set_file_access():
    body = request.get_json(force=True) or {}

    if body.get("full_access"):
        roots, unlock = ["/"], True
    elif body.get("reset"):
        roots, unlock = list(DEFAULT_FILE_ROOTS), False
    else:
        raw = body.get("roots", None)
        if raw is None:
            acc = file_access()
            roots = acc["roots"]
        else:
            if not isinstance(raw, list):
                return jsonify({"error": "roots muss eine Liste sein"}), 400
            roots = []
            for r in raw:
                r = str(r).strip().rstrip("/") or "/"
                if not os.path.isabs(r):
                    return jsonify({"error": f"„{r}“ ist kein absoluter Pfad"}), 400
                if "\x00" in r:
                    return jsonify({"error": "Ungültiger Pfad"}), 400
                if r not in roots:
                    roots.append(r)
            if not roots:
                return jsonify({"error": "Mindestens ein Ordner wird gebraucht"}), 400
        unlock = bool(body.get("unlock_system", file_access()["unlock_system"]))

    save_json(FILE_ACCESS_FILE, {"roots": roots, "unlock_system": unlock})
    print(f"[FILE-ACCESS] geaendert: roots={roots} unlock_system={unlock} ip={_client_ip()}", flush=True)
    return jsonify({"ok": True, "roots": roots, "unlock_system": unlock})


@app.errorhandler(safepath.PathError)
def _handle_path_error(e):
    # The reason goes to the add-on log only - the client gets one flat answer
    # so the API cannot be used to probe which paths exist.
    print(f"[SAFEPATH] abgewiesen: {e}", flush=True)
    return jsonify({"error": "Pfad nicht erlaubt"}), 403


def _protected_paths():
    """Paths that must never be deleted: the roots themselves, every mount
    point and every configured share directory."""
    out = {safepath.real(r) for r in file_roots()}
    out.update(safepath.real(m.get("mountpoint", "")) for m in load_json(MOUNTS_FILE, []) if m.get("mountpoint"))
    out.update(safepath.real(s.get("path", "")) for s in load_json(SHARES_FILE, []) if s.get("path"))
    return out


def _slash_is_root():
    """True when "/" itself is allowed (the "all files" switch). The synthetic
    roots listing makes no sense then - it would contain one entry, "/",
    pointing back at itself, and the file manager could never get in."""
    return any(safepath.real(r) == safepath.real("/") for r in file_roots())


def _roots_listing():
    """Synthetic listing shown instead of the container root."""
    entries = []
    for r in file_roots():
        try:
            real_r = safepath.real(r)
        except Exception:
            continue
        if not os.path.isdir(real_r):
            continue
        entries.append({
            "name": r.strip("/") or r, "path": real_r, "is_dir": True,
            "size": None, "size_truncated": False,
            "modified": os.path.getmtime(real_r) if os.path.exists(real_r) else 0,
            "readable": os.access(real_r, os.R_OK), "is_symlink": False,
        })
    return entries


@app.route("/api/roots")
def api_roots():
    """The allowed roots, so the UI can offer them instead of hard-coded paths."""
    return jsonify({"roots": [e["path"] for e in _roots_listing()]})



# ─────────────────────────── sharing (admin side) ───────────────

DEFAULT_SHARE_ROOTS = ["/media", "/mnt", "/share"]


def share_roots():
    roots = _opt("share_allowed_roots", None)
    if not isinstance(roots, list) or not roots:
        roots = DEFAULT_SHARE_ROOTS
    return [str(r).strip() for r in roots if str(r).strip()]


def public_link_url(token):
    """Absolute URL for a link. The add-on cannot know its public hostname,
    so share_public_url must be configured; otherwise fall back to the
    address the browser used for this request plus the share port."""
    base = (_opt("share_public_url", "") or "").strip().rstrip("/")
    fallback = not base
    if fallback:
        host = request.host.split(":")[0] if request else "localhost"
        base = f"http://{host}:{int(_opt('share_port', 8101))}"
    return f"{base}/s/{token}", fallback


def _link_out(link):
    out = dict(link)
    out["url"], out["url_is_fallback"] = public_link_url(link["token"])
    out["live"] = sharing_store.link_is_live(link)
    out.pop("password_hash", None)
    out["has_password"] = bool(link.get("password_hash"))
    out["locked"] = _link_locked(link["id"])
    return out


@app.errorhandler(sharing_store.ShareError)
def _handle_share_error(e):
    return jsonify({"error": str(e)}), 400


@app.route("/api/sharing/status")
def api_sharing_status():
    links = sharing_store.list_links()
    _, fallback = public_link_url("x")
    clam = _clamd_state()
    snap = _lock_snapshot()
    return jsonify({
        "sharing_enabled": bool(_opt("sharing_enabled", False)),
        "public_site_running": _share_running(),
        "sandboxed": _share_sandboxed(),
        "share_port": int(_opt("share_port", 8101)),
        "share_bind": _opt("share_bind", "0.0.0.0"),
        "public_url": (_opt("share_public_url", "") or "").strip(),
        "url_is_fallback": fallback,
        "admin_password_enabled": bool(_admin_auth.get("enabled")),
        "links": len(links),
        "links_live": sum(1 for l in links if sharing_store.link_is_live(l)),
        "accounts": len(sharing_store.list_accounts()),
        "allowed_roots": share_roots(),
        "clamav_enabled": bool(_opt("share_clamav_enabled", False)),
        "clamav_target": f"{_opt('share_clamav_host', '127.0.0.1')}:{_opt('share_clamav_port', 3310)}",
        "clamav_on_error": _opt("share_clamav_on_error", "reject"),
        "clamav_reachable": clam["ok"],
        "clamav_message": clam["message"],
        "authfail_limit": AUTHFAIL_IP[0],
        "authfail_window_min": AUTHFAIL_IP[1] // 60,
        "locks": snap["locks"],
        "authfail": snap["counters"],
    })


CROWDSEC_DIR = "/config/.storage/crowdsec/config"
CROWDSEC_FILES = {
    "parsers/s01-parse/simplenas-share.yaml": "simplenas-share-parser.yaml",
    "scenarios/simplenas-share.yaml": "simplenas-share-scenarios.yaml",
    "acquis.d/simplenas-share.yaml": "simplenas-share-acquis.yaml",
}


CROWDSEC_SETUP_FILE = f"{DATA_DIR}/crowdsec_setup.json"
# Both add-ons map the Home Assistant config directory, so a file under
# /config is the one both can reach. /share is mapped here but NOT into the
# CrowdSec add-on, which is why the first default left CrowdSec tailing a
# file it could never see ("No matching files for pattern").
DEFAULT_EXPORT_PATH = "/config/.simplenas/share_access.log"
LEGACY_EXPORT_PATHS = ("/share/simplenas/share_access.log",)


def _crowdsec_src(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "crowdsec", name)


def _share_export_path():
    """Where the second copy of the access log goes. The add-on option wins;
    otherwise the path the CrowdSec setup button chose (the add-on cannot
    write its own options.json, so that choice lives in /data)."""
    export = (_opt("share_log_export_path", "") or "").strip()
    if export:
        return export
    saved = load_json(CROWDSEC_SETUP_FILE, {})
    path = str(saved.get("export_path") or "").strip() if isinstance(saved, dict) else ""
    return DEFAULT_EXPORT_PATH if path in LEGACY_EXPORT_PATHS else path


def _crowdsec_can_read(path):
    """Can the CrowdSec add-on open this file?

    Both add-ons map the Home Assistant config directory, so /config is the
    one place each of them reaches. /share is mapped here but not there - a
    log kept there makes CrowdSec start with "No matching files for pattern"
    and no scenario ever fires.
    """
    return bool(path) and path.startswith("/config/")


def _crowdsec_log_path(export):
    """The file the acquisition should name: the configured export when
    CrowdSec can read it, otherwise the default next to it."""
    return export if _crowdsec_can_read(export) else DEFAULT_EXPORT_PATH


def _crowdsec_installed():
    return os.path.exists(os.path.join(CROWDSEC_DIR, "acquis.d", "simplenas-share.yaml"))


def _apply_export_paths(export, announce=False):
    """Open the configured export and, when CrowdSec is set up, make sure the
    file its acquisition names is written too. Returns the CrowdSec path."""
    max_mb = _opt("share_log_max_mb", 5)
    if export:
        accesslog.set_export(export, max_mb, slot="main")
        if announce:
            print(f"[SHARE] Zugriffsprotokoll wird zusaetzlich nach {export} geschrieben", flush=True)
    cs_path = _crowdsec_log_path(export)
    if _crowdsec_installed() or not export:
        accesslog.set_export(cs_path, max_mb, slot="crowdsec")
        if announce and cs_path != export:
            print(f"[SHARE] CrowdSec liest das Protokoll unter {cs_path} "
                  f"(share_log_export_path zeigt auf einen Ordner, den CrowdSec nicht sieht)", flush=True)
    return cs_path


def _write_crowdsec_acquis(export):
    """Write the acquisition file pointing at `export`. Returns True on change."""
    dest = os.path.join(CROWDSEC_DIR, "acquis.d", "simplenas-share.yaml")
    with open(_crowdsec_src("simplenas-share-acquis.yaml"), encoding="utf-8") as f:
        # Replace the path the template lists, whatever it is - keying on
        # DEFAULT_EXPORT_PATH would silently write the template's own path
        # through unchanged the moment the two drift apart.
        content = re.sub(r"(?m)^(\s*-\s+)\S+$", lambda m: m.group(1) + export,
                         f.read(), count=1)
    if os.path.exists(dest):
        with open(dest, encoding="utf-8") as f:
            if f.read() == content:
                return False
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(content)
    return True


def _sync_crowdsec_acquis(export):
    """Keep an already installed acquisition file on the current export path.

    Only touches a file this add-on wrote itself, and only when the path it
    names is out of date - otherwise CrowdSec keeps tailing a file nobody
    writes any more, and the scenarios never fire.
    """
    dest = os.path.join(CROWDSEC_DIR, "acquis.d", "simplenas-share.yaml")
    if not export or not os.path.exists(dest):
        return False
    try:
        with open(dest, encoding="utf-8") as f:
            if export in f.read():
                return False
        _write_crowdsec_acquis(export)
    except OSError as e:
        print(f"[SHARE] CrowdSec-Acquisition nicht aktualisierbar: {e}", flush=True)
        return False
    print(f"[SHARE] CrowdSec-Acquisition auf {export} umgestellt - CrowdSec-Addon neu starten", flush=True)
    return True


_CLAMD_PROBE = {"ts": 0.0, "ok": None, "message": ""}


def _clamd_state(max_age=60):
    """Is clamd actually answering? Cached, because the status card polls.

    With share_clamav_on_error=reject an unreachable clamd silently turns
    every upload into a rejection, so this belongs in the status, not only
    behind the Test button.
    """
    if not _opt("share_clamav_enabled", False):
        return {"ok": None, "message": ""}
    now = time.time()
    if now - _CLAMD_PROBE["ts"] > max_age or _CLAMD_PROBE["ok"] is None:
        host = str(_opt("share_clamav_host", "127.0.0.1") or "127.0.0.1")
        port = int(_opt("share_clamav_port", 3310) or 3310)
        ok = False
        try:
            ok = bool(clamav.ping(host, port, timeout=3))
        except Exception:
            ok = False
        _CLAMD_PROBE.update(ts=now, ok=ok,
                            message="" if ok else f"clamd auf {host}:{port} antwortet nicht auf PING")
    return {"ok": _CLAMD_PROBE["ok"], "message": _CLAMD_PROBE["message"]}


@app.route("/api/sharing/crowdsec/status")
def api_sharing_crowdsec_status():
    present = os.path.isdir(CROWDSEC_DIR)
    installed = {rel: os.path.exists(os.path.join(CROWDSEC_DIR, rel)) for rel in CROWDSEC_FILES}
    export = _share_export_path()
    cs_path = _crowdsec_log_path(export)
    option = (_opt("share_log_export_path", "") or "").strip()
    return jsonify({
        "crowdsec_config_found": present,
        "config_dir": CROWDSEC_DIR,
        "installed": installed,
        "all_installed": present and all(installed.values()),
        "export_path": export,
        "crowdsec_path": cs_path,
        "default_export_path": DEFAULT_EXPORT_PATH,
        "export_active": accesslog.writes_to(cs_path),
        "option_path_blind": bool(option) and not _crowdsec_can_read(option),
    })


@app.route("/api/sharing/crowdsec/install", methods=["POST"])
def api_sharing_crowdsec_install():
    """Copy parser, scenarios and acquisition into the CrowdSec add-on's config.
    Both add-ons see /config, so this is a plain file copy. CrowdSec picks the
    files up after a restart of its add-on. Without share_log_export_path the
    export starts right here at the default path - no option, no restart."""
    if not os.path.isdir(CROWDSEC_DIR):
        return jsonify({"error": f"CrowdSec-Konfiguration nicht gefunden ({CROWDSEC_DIR}). Ist das CrowdSec-Addon installiert?"}), 404
    export = _share_export_path() or DEFAULT_EXPORT_PATH
    if not (_opt("share_log_export_path", "") or "").strip():
        save_json(CROWDSEC_SETUP_FILE, {"export_path": export})
    cs_path = _crowdsec_log_path(export)
    max_mb = _opt("share_log_max_mb", 5)
    if export and not accesslog.set_export(export, max_mb, slot="main"):
        return jsonify({"error": f"Protokoll-Export nach {export} nicht moeglich (Ordner nicht beschreibbar?)."}), 500
    if not accesslog.set_export(cs_path, max_mb, slot="crowdsec"):
        return jsonify({"error": f"Protokoll-Export nach {cs_path} nicht moeglich (Ordner nicht beschreibbar?)."}), 500
    export = cs_path
    written = []
    for rel, src in CROWDSEC_FILES.items():
        if src.endswith("acquis.yaml"):
            _write_crowdsec_acquis(export)
            written.append(rel)
            continue
        dest = os.path.join(CROWDSEC_DIR, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(_crowdsec_src(src), encoding="utf-8") as f:
            content = f.read()
        with open(dest, "w", encoding="utf-8") as f:
            f.write(content)
        written.append(rel)
    print(f"[SHARE] CrowdSec-Dateien installiert: {', '.join(written)}", flush=True)
    return jsonify({"ok": True, "written": written, "restart_needed": "CrowdSec",
                    "export_path": export, "export_active": accesslog.writes_to(export)})


@app.route("/api/sharing/clamav/test", methods=["POST"])
def api_sharing_clamav_test():
    """PING, then an EICAR round trip - the difference between configured and working."""
    host = str(_opt("share_clamav_host", "127.0.0.1") or "127.0.0.1")
    port = int(_opt("share_clamav_port", 3310) or 3310)
    ok, msg = clamav.self_test(host, port)
    print(f"[SHARE] ClamAV-Test {host}:{port}: {'OK' if ok else 'FEHLER'} - {msg}", flush=True)
    return jsonify({"ok": ok, "message": msg, "enabled": bool(_opt("share_clamav_enabled", False))})


@app.route("/api/sharing/links", methods=["GET"])
def api_sharing_links():
    return jsonify([_link_out(l) for l in sharing_store.list_links()])


@app.route("/api/sharing/links", methods=["POST"])
def api_sharing_create_link():
    body = request.get_json(force=True) or {}
    link = sharing_store.create_link(body, created_by=session.get("user", "admin"),
                                     shares=load_json(SHARES_FILE, []), allowed_roots=share_roots())
    print(f"[SHARE] Link angelegt: {link['name']} ({link['id']}) root={link['root']} by={session.get('user','admin')}", flush=True)
    return jsonify(_link_out(link)), 201


@app.route("/api/sharing/links/<link_id>", methods=["PUT"])
def api_sharing_update_link(link_id):
    body = request.get_json(force=True) or {}
    link = sharing_store.update_link(link_id, body, shares=load_json(SHARES_FILE, []), allowed_roots=share_roots())
    return jsonify(_link_out(link))


@app.route("/api/sharing/links/<link_id>", methods=["DELETE"])
def api_sharing_delete_link(link_id):
    sharing_store.delete_link(link_id)
    print(f"[SHARE] Link geloescht: {link_id}", flush=True)
    return jsonify({"ok": True})


@app.route("/api/sharing/links/<link_id>/rotate", methods=["POST"])
def api_sharing_rotate(link_id):
    link = sharing_store.rotate_token(link_id)
    print(f"[SHARE] Token erneuert: {link_id}", flush=True)
    return jsonify(_link_out(link))


@app.route("/api/sharing/links/<link_id>/reset-counters", methods=["POST"])
def api_sharing_reset_counters(link_id):
    if not sharing_store.get_link(link_id):
        return jsonify({"error": "Link nicht gefunden"}), 404
    sharing_store.reset_counters(link_id)
    return jsonify(_link_out(sharing_store.get_link(link_id)))


@app.route("/api/sharing/links/<link_id>/unlock", methods=["POST"])
def api_sharing_unlock(link_id):
    if not sharing_store.get_link(link_id):
        return jsonify({"error": "Link nicht gefunden"}), 404
    _do_unlock(f"authfail:link:{link_id}")
    return jsonify(_link_out(sharing_store.get_link(link_id)))


@app.route("/api/sharing/locks/unlock", methods=["POST"])
def api_sharing_locks_unlock():
    """Lift one lockout shown in the Sharing card (own IP after testing, say)."""
    key = str((request.get_json(silent=True) or {}).get("key", "")).strip()
    if not key or not key.startswith(("authfail:", "ban:", "scan:")):
        return jsonify({"error": "Unbekannte Sperre"}), 400
    _do_unlock(key)
    print(f"[SHARE] Sperre aufgehoben: {key}", flush=True)
    return jsonify({"ok": True, "key": key})


@app.route("/api/sharing/log", methods=["GET"])
def api_sharing_log():
    limit = max(1, min(int(request.args.get("limit", 200)), 1000))
    return jsonify(accesslog.tail(limit, request.args.get("event") or None, request.args.get("link_id") or None))


@app.route("/api/sharing/log", methods=["DELETE"])
def api_sharing_log_clear():
    accesslog.clear()
    return jsonify({"ok": True})


@app.route("/api/sharing/accounts", methods=["GET"])
def api_sharing_accounts():
    return jsonify(sharing_store.list_accounts())


@app.route("/api/sharing/accounts", methods=["POST"])
def api_sharing_create_account():
    body = request.get_json(force=True) or {}
    acc = sharing_store.create_account(body.get("username", ""), body.get("password", ""),
                                       body.get("display_name", ""), created_by=session.get("user", "admin"))
    print(f"[SHARE] Konto angelegt: {acc['username']}", flush=True)
    return jsonify(acc), 201


@app.route("/api/sharing/accounts/<username>", methods=["PUT"])
def api_sharing_update_account(username):
    body = request.get_json(force=True) or {}
    return jsonify(sharing_store.update_account(username, body))


@app.route("/api/sharing/accounts/<username>", methods=["DELETE"])
def api_sharing_delete_account(username):
    sharing_store.delete_account(username)
    print(f"[SHARE] Konto geloescht: {username}", flush=True)
    return jsonify({"ok": True})


# ─────────────────────────── browse API ─────────────────────────

@app.route("/api/browse")
def api_browse():
    path = request.args.get("path", "/").strip()
    if path in ("", "/") and not _slash_is_root():
        # The container root is not browsable - offer the allowed roots instead
        roots = _roots_listing()
        return jsonify({"path": "/", "parent": None, "is_root": True,
                        "breadcrumb": [{"name": "/", "path": "/"}],
                        "entries": [{"name": e["name"], "path": e["path"],
                                     "readable": e["readable"], "is_symlink": False} for e in roots]})
    path = _safe(_remap_path(os.path.abspath(path)))
    entries = []
    try:
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            try:
                if not os.path.isdir(full): continue  # folgt Symlinks automatisch → korrekt
                entries.append({
                    "name": name, "path": full,
                    "readable": os.access(full, os.R_OK),
                    "is_symlink": os.path.islink(full),
                })
            except Exception:
                # Symlink-Fallback: Eintrag trotzdem anzeigen
                entries.append({
                    "name": name, "path": full,
                    "readable": False,
                    "is_symlink": True,
                })
    except PermissionError:
        # Fallback: use ls via subprocess (runs as root)
        try:
            result = subprocess.run(
                ["ls", "-1a", path],          # -a statt Glob, damit Symlinks auftauchen
                capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for name in result.stdout.strip().split("\n"):
                    name = name.strip()
                    if not name or name in (".", ".."): continue
                    full = os.path.join(path, name)
                    if os.path.isdir(full):   # folgt Symlinks → Symlink-Dirs werden inkludiert
                        entries.append({"name": name, "path": full, "readable": True, "is_symlink": os.path.islink(full)})
        except Exception:
            return jsonify({"error": "Kein Zugriff auf " + path}), 403
    except FileNotFoundError:
        return jsonify({"error": "Pfad nicht gefunden"}), 404
    return jsonify({"path": path, "parent": _parent_of(path),
                     "breadcrumb": _breadcrumb(path), "entries": entries})


@app.route("/api/mkdir", methods=["POST"])
def api_mkdir():
    body = request.get_json(force=True)
    path = body.get("path", "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    path = _safe(path)
    try:
        os.makedirs(path, exist_ok=True)
        return jsonify({"ok": True, "path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─────────────────────────── file manager API ───────────────────

def _remap_path(path):
    """HA only exposes addon_configs at /addon_configs/ inside the container.
    /homeassistant/addons_config/... and /config/addons_config/... are not
    accessible, so redirect to the real mount point."""
    for prefix in ("/homeassistant/addons_config/", "/config/addons_config/"):
        if path.startswith(prefix):
            remapped = "/addon_configs/" + path[len(prefix):]
            print(f"[REMAP] {path} -> {remapped} (exists={os.path.isdir(remapped)})", flush=True)
            return remapped
        if path == prefix.rstrip("/"):
            print(f"[REMAP] {path} -> /addon_configs (exists={os.path.isdir('/addon_configs')})", flush=True)
            return "/addon_configs"
    return path

def _breadcrumb(path):
    """Crumbs from the allowed root down to path - never above it, so the UI
    cannot offer a click into the container root."""
    real_path = safepath.real(path)
    base = None
    for r in file_roots():
        real_r = safepath.real(r)
        if safepath.is_within(real_r, real_path):
            base = real_r
            break
    parts, cur = [], real_path
    while True:
        parts.insert(0, {"name": os.path.basename(cur) or cur, "path": cur})
        if base is not None and cur == base:
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    if not parts or parts[0]["path"] != "/":
        parts.insert(0, {"name": "/", "path": "/"})
    return parts


def _parent_of(path):
    """Parent directory, or '/' (the roots listing) when path is a root."""
    real_path = safepath.real(path)
    if real_path == safepath.real("/"):
        return None
    for r in file_roots():
        if real_path == safepath.real(r):
            return None if _slash_is_root() else "/"
    parent = os.path.dirname(real_path)
    return parent if parent != real_path else None


def _recursive_dir_size(path, max_seconds=8):
    """Sum file sizes recursively under `path`. Returns (bytes, truncated).
    truncated=True when the walk was aborted due to the time budget."""
    total = 0
    deadline = time.time() + max_seconds
    try:
        for root, dirs, files in os.walk(path, followlinks=False):
            if time.time() > deadline:
                return total, True
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
    except Exception:
        pass
    return total, False


@app.route("/api/files")
def api_files():
    """List files and directories (unlike /browse which is dirs only).

    ?dir_size=1 recursively computes the size of each directory entry
    (capped per directory by _recursive_dir_size). Off by default because
    it can be slow on large trees / slow USB media."""
    path = request.args.get("path", "/").strip()
    want_dir_size = request.args.get("dir_size", "0") in ("1", "true", "yes")
    if path in ("", "/") and not _slash_is_root():
        return jsonify({"path": "/", "parent": None, "is_root": True,
                        "breadcrumb": [{"name": "/", "path": "/"}],
                        "entries": _roots_listing()})
    path = _safe(_remap_path(os.path.abspath(path or "/")))
    entries = []
    try:
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            try:
                is_link = os.path.islink(full)
                size_truncated = False
                try:
                    st = os.stat(full)         # folgt Symlink
                    is_dir = os.path.isdir(full)
                    if is_dir:
                        if want_dir_size:
                            size, size_truncated = _recursive_dir_size(full)
                        else:
                            size = None
                    else:
                        size = st.st_size
                    mtime = st.st_mtime
                except OSError:
                    # Symlink-Ziel nicht auflösbar (z.B. bind-mount außerhalb Container)
                    # → als Verzeichnis behandeln wenn es ein Symlink ist
                    is_dir = is_link
                    size = None
                    try:
                        mtime = os.lstat(full).st_mtime
                    except Exception:
                        mtime = 0
                entries.append({
                    "name": name, "path": full, "is_dir": is_dir,
                    "size": size,
                    "size_truncated": size_truncated,
                    "modified": mtime,
                    "readable": os.access(full, os.R_OK),
                    "is_symlink": os.path.islink(full),
                })
            except Exception:
                # Letzter Fallback: Eintrag trotzdem anzeigen statt überspringen
                entries.append({
                    "name": name, "path": full, "is_dir": True,
                    "size": None, "modified": 0, "readable": False,
                    "is_symlink": False,
                })
    except PermissionError:
        return jsonify({"error": "Kein Zugriff"}), 403
    except FileNotFoundError:
        return jsonify({"error": "Pfad nicht gefunden"}), 404
    return jsonify({"path": path, "parent": _parent_of(path),
                     "breadcrumb": _breadcrumb(path), "entries": entries})


@app.route("/api/files/delete", methods=["POST"])
def api_files_delete():
    body = request.get_json(force=True)
    path = _safe(body.get("path", "").strip())
    if path in _protected_paths():
        return jsonify({"error": "Dieser Pfad kann nicht gelöscht werden"}), 400
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/rename", methods=["POST"])
def api_files_rename():
    body = request.get_json(force=True)
    path = body.get("path", "").strip()
    new_name = body.get("new_name", "").strip()
    if not path or not new_name:
        return jsonify({"error": "path and new_name required"}), 400
    path = _safe(path)
    if path in _protected_paths():
        return jsonify({"error": "Dieser Pfad kann nicht umbenannt werden"}), 400
    if new_name in (".", "..") or "/" in new_name or "\\" in new_name or "\x00" in new_name:
        return jsonify({"error": "Ungültiger Name"}), 400
    new_path = _safe(os.path.join(os.path.dirname(path), new_name))
    try:
        os.rename(path, new_path)
        return jsonify({"ok": True, "new_path": new_path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/copy", methods=["POST"])
def api_files_copy():
    body = request.get_json(force=True)
    src = body.get("src", "").strip()
    dst = body.get("dst", "").strip()
    if not src or not dst:
        return jsonify({"error": "src and dst required"}), 400
    src, dst = _safe(src), _safe(dst)
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/move", methods=["POST"])
def api_files_move():
    body = request.get_json(force=True)
    src = body.get("src", "").strip()
    dst = body.get("dst", "").strip()
    if not src or not dst:
        return jsonify({"error": "src and dst required"}), 400
    src, dst = _safe(src), _safe(dst)
    if src in _protected_paths():
        return jsonify({"error": "Dieser Pfad kann nicht verschoben werden"}), 400
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/upload", methods=["POST"])
def api_files_upload():
    target_dir = _safe(request.form.get("path", "/media"))
    if "file" not in request.files:
        return jsonify({"error": "no file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "no filename"}), 400
    # The browser controls this name; strip any directory part before use.
    leaf = os.path.basename(f.filename.replace("\\", "/")).strip()
    if not leaf or leaf in (".", "..") or "\x00" in leaf:
        return jsonify({"error": "Ungültiger Dateiname"}), 400
    os.makedirs(target_dir, exist_ok=True)
    dest = safepath.resolve_new(target_dir, "", leaf)
    if os.path.exists(dest):
        return jsonify({"error": f"„{leaf}“ existiert bereits"}), 409
    f.save(dest)
    return jsonify({"ok": True, "path": dest})

@app.route("/api/files/download")
def api_files_download():
    from flask import send_file
    path = _safe(request.args.get("path", "").strip())
    if not os.path.isfile(path):
        return jsonify({"error": "Datei nicht gefunden"}), 404
    return send_file(path, as_attachment=True)

@app.route("/api/files/zip")
def api_files_zip():
    """Stream a folder as a ZIP - same generator as the public site."""
    folder = _safe(request.args.get("path", "").strip())
    if not os.path.isdir(folder):
        return jsonify({"error": "Ordner nicht gefunden"}), 404
    limit_bytes = int(_opt("share_zip_max_gb", 5) or 5) * 1024 ** 3
    try:
        total, count = zipstream.preflight(folder, limit_bytes, int(_opt("share_zip_max_files", 10000) or 10000))
    except zipstream.TooBig as e:
        return jsonify({"error": f"Ordner zu groß für ZIP (Grenze {e.limit_bytes // 1024**3} GB / {e.limit_files} Dateien)"}), 413
    name = (os.path.basename(folder.rstrip("/")) or "archive") + ".zip"
    resp = app.response_class(zipstream.stream(folder, total), mimetype="application/zip")
    resp.headers["Content-Disposition"] = share_web.content_disposition(name)
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.route("/api/files/view")
def api_files_view():
    """Serve a file inline (for browser-native preview of PDFs, images,
    videos, audio, text). Falls back to download for unknown MIME types."""
    from flask import send_file
    import mimetypes
    path = _safe(request.args.get("path", "").strip())
    if not os.path.isfile(path):
        return jsonify({"error": "Datei nicht gefunden"}), 404
    mime, _ = mimetypes.guess_type(path)
    # send_file in modern Flask supports Range requests via conditional=True
    # which is the default — videos/audio can seek without full download
    return send_file(path, mimetype=mime, as_attachment=False, conditional=True)

@app.route("/api/files/content")
def api_files_content():
    path = _safe(request.args.get("path", "").strip())
    if not os.path.isfile(path):
        return jsonify({"error": "Datei nicht gefunden"}), 404
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return jsonify({"ok": True, "content": content})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/write", methods=["POST"])
def api_files_write():
    body = request.get_json(force=True)
    path = _safe(body.get("path", "").strip())
    content = body.get("content", "")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─────────────────────────── backup API ─────────────────────────

BACKUPS_FILE = f"{DATA_DIR}/backups.json"
_backup_status = {}  # runtime status: {job_id: {running, progress, last_run, last_error}}

@app.route("/api/backups", methods=["GET"])
def api_get_backups():
    jobs = load_json(BACKUPS_FILE, [])
    for j in jobs:
        st = _backup_status.get(j["id"], {})
        j["running"] = st.get("running", False)
        j["progress"] = st.get("progress", "")
        j["last_run"] = st.get("last_run", j.get("last_run", ""))
        j["last_error"] = st.get("last_error", j.get("last_error", ""))
    return jsonify(jobs)

@app.route("/api/backups", methods=["POST"])
def api_create_backup():
    body = request.get_json(force=True)
    name = body.get("name", "").strip()
    src = body.get("src", "").strip()
    dst = body.get("dst", "").strip()
    if not name or not src or not dst:
        return jsonify({"error": "name, src and dst required"}), 400
    src, dst = _safe(src), _safe(dst)
    import uuid
    job = {
        "id": str(uuid.uuid4())[:8],
        "name": name, "src": src, "dst": dst,
        "schedule": body.get("schedule", "manual"),
        "keep": body.get("keep", 3),
        "last_run": "", "last_error": "",
    }
    jobs = load_json(BACKUPS_FILE, [])
    jobs.append(job)
    save_json(BACKUPS_FILE, jobs)
    return jsonify(job), 201

@app.route("/api/backups/<job_id>", methods=["PUT"])
def api_update_backup(job_id):
    body = request.get_json(force=True)
    jobs = load_json(BACKUPS_FILE, [])
    for j in jobs:
        if j["id"] == job_id:
            for k in ("src", "dst"):
                if k in body: body[k] = _safe(str(body[k]).strip())
            for k in ("name", "src", "dst", "schedule", "keep"):
                if k in body: j[k] = body[k]
            save_json(BACKUPS_FILE, jobs)
            return jsonify(j)
    return jsonify({"error": "Not found"}), 404

@app.route("/api/backups/<job_id>", methods=["DELETE"])
def api_delete_backup(job_id):
    jobs = load_json(BACKUPS_FILE, [])
    jobs = [j for j in jobs if j["id"] != job_id]
    save_json(BACKUPS_FILE, jobs)
    return jsonify({"ok": True})

@app.route("/api/backups/<job_id>/run", methods=["POST"])
def api_run_backup(job_id):
    jobs = load_json(BACKUPS_FILE, [])
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        return jsonify({"error": "Not found"}), 404
    if _backup_status.get(job_id, {}).get("running"):
        return jsonify({"error": "Backup läuft bereits"}), 409

    import threading
    def run_backup(j):
        import time, datetime
        jid = j["id"]
        _backup_status[jid] = {"running": True, "progress": "Starte...", "last_error": ""}
        try:
            # Re-check at run time: the job file could have been edited by hand,
            # and this starts rsync as root.
            src = _safe(j["src"].rstrip("/"))
            dst_base = _safe(j["dst"].rstrip("/"))
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            dst_dir = os.path.join(dst_base, f"{j['name']}_{ts}")

            _backup_status[jid]["progress"] = f"Kopiere {src} → {dst_dir}"
            # Use rsync for better performance and progress
            proc = subprocess.run(
                ["rsync", "-a", "--info=progress2", src + "/", dst_dir + "/"],
                capture_output=True, text=True, timeout=3600)
            if proc.returncode != 0:
                # Fallback to shutil if rsync not available
                if "not found" in (proc.stderr or ""):
                    shutil.copytree(src, dst_dir)
                else:
                    raise Exception(proc.stderr or f"rsync failed (rc={proc.returncode})")

            # Cleanup old backups (keep N newest)
            keep = j.get("keep", 3)
            prefix = j["name"] + "_"
            parent = j["dst"].rstrip("/")
            if os.path.isdir(parent):
                existing = sorted([d for d in os.listdir(parent) if d.startswith(prefix) and os.path.isdir(os.path.join(parent, d))], reverse=True)
                for old in existing[keep:]:
                    _backup_status[jid]["progress"] = f"Lösche altes Backup: {old}"
                    shutil.rmtree(os.path.join(parent, old), ignore_errors=True)

            _backup_status[jid] = {"running": False, "progress": "", "last_run": ts, "last_error": ""}
            # Persist last_run
            jobs2 = load_json(BACKUPS_FILE, [])
            for j2 in jobs2:
                if j2["id"] == jid:
                    j2["last_run"] = ts
                    j2["last_error"] = ""
            save_json(BACKUPS_FILE, jobs2)
        except Exception as e:
            _backup_status[jid] = {"running": False, "progress": "", "last_run": "", "last_error": str(e)}
            jobs2 = load_json(BACKUPS_FILE, [])
            for j2 in jobs2:
                if j2["id"] == jid:
                    j2["last_error"] = str(e)
            save_json(BACKUPS_FILE, jobs2)

    threading.Thread(target=run_backup, args=(job,), daemon=True).start()
    return jsonify({"ok": True, "message": "Backup gestartet"})

# ─────────────────────────── frontend ───────────────────────────

@app.route("/")
def index():
    return render_template("index.html",
                           admin_enabled=_admin_auth.get("enabled", False),
                           csrf=csrf_token())

def _install_safe_getfqdn():
    """Keep the reverse DNS lookup during bind from killing the addon.

    http.server calls socket.getfqdn() while binding. With host_network the
    addon uses the router's DNS; a PTR record that is not valid UTF-8 then
    raises UnicodeDecodeError and the addon never starts.
    """
    real_getfqdn = socket.getfqdn

    def safe_getfqdn(name=""):
        try:
            return real_getfqdn(name)
        except (UnicodeDecodeError, UnicodeError, OSError):
            return name or "localhost"

    socket.getfqdn = safe_getfqdn


def _sharing_should_start():
    """The public site needs sharing_enabled AND a working admin password.
    run.sh checks the same thing; two independent checks for the one
    condition that must never fail."""
    if not _opt("sharing_enabled", False):
        return False
    if _admin_auth.get("setup_required") or not _admin_auth.get("enabled") or not _admin_auth.get("password_hash"):
        print("[SHARE] sharing_enabled, aber kein Admin-Passwort - oeffentliche Seite bleibt aus", flush=True)
        return False
    return True


# The public share site runs either as a locked-down separate process
# (share_sandbox=auto|on, the default) or, when that is unavailable, as a
# thread in this process (the pre-3.8 behaviour). _SHARE tracks which.
_SHARE = {"sandboxed": False, "proc": None}
_SNAP_CACHE = {"ts": 0.0, "snap": None}


def _share_sandboxed():
    p = _SHARE["proc"]
    return bool(_SHARE["sandboxed"] and p is not None and p.poll() is None)


def _share_running():
    return True if _share_sandboxed() else bool(share_web.RUNNING)


def _lock_snapshot():
    """Locks/counters for the admin card. From the worker's snapshot file when
    sandboxed, straight from the in-process limiter otherwise. Cached ~1 s so
    rendering a link list does not read the file per row."""
    if not _share_sandboxed():
        snap = LIMITER.snapshot(AUTHFAIL_IP[1])
        snap["link_locked"] = None            # None = ask the limiter per link
        return snap
    now = time.time()
    if _SNAP_CACHE["snap"] is None or now - _SNAP_CACHE["ts"] > 1:
        _SNAP_CACHE["snap"] = sharesandbox.read_snapshot(DATA_DIR) or {
            "locks": [], "counters": [], "link_locked": []}
        _SNAP_CACHE["ts"] = now
    return _SNAP_CACHE["snap"]


def _link_locked(link_id):
    snap = _lock_snapshot()
    ll = snap.get("link_locked")
    if ll is None:
        return LIMITER.locked(f"authfail:link:{link_id}", *AUTHFAIL_LINK)
    return link_id in ll


def _do_unlock(key):
    if _share_sandboxed():
        sharesandbox.queue_unlock(DATA_DIR, key)
    else:
        LIMITER.clear(key=key)


def _start_log_shipper(src, dst, max_mb):
    """Sandbox only: the worker writes src; ship new lines to dst (the path
    CrowdSec reads under /config, which the worker cannot see). Rotates dst so
    it cannot grow without bound."""
    import threading

    def run():
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
        except OSError:
            pass
        pos, ino, cap = None, None, max(1, int(max_mb)) * 1024 * 1024
        while True:
            try:
                st = os.stat(src)
                if pos is None or ino != st.st_ino or st.st_size < pos:
                    pos, ino = st.st_size, st.st_ino      # start at the end, ship only new lines
                elif st.st_size > pos:
                    with open(src, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(pos)
                        data = f.read()
                        pos = f.tell()
                    if os.path.exists(dst) and os.path.getsize(dst) > cap:
                        try:
                            os.replace(dst, dst + ".1")
                        except OSError:
                            pass
                    with open(dst, "a", encoding="utf-8") as g:
                        g.write(data)
            except OSError:
                pass
            time.sleep(2)

    threading.Thread(target=run, daemon=True, name="share-logship").start()


def _launch_sandbox(host, port, export):
    """Start the worker in a mount namespace, dropped to nobody. Returns the
    Popen once it answers /healthz, or None (caller falls back)."""
    import shutil
    import urllib.request
    if not (shutil.which("unshare") and shutil.which("setpriv")):
        print("[SHARE] unshare/setpriv nicht vorhanden - keine Sandbox", flush=True)
        return None
    os.makedirs(os.path.join(DATA_DIR, "tmp"), exist_ok=True)
    # every file the jail binds must already exist, or the bind mount fails
    seeds = {"share_links.json": "[]", "share_accounts.json": "[]", "share_auth.json": "{}",
             "shares.json": "[]", "share_counters.json": "{}",
             sharesandbox.SNAPSHOT_FILE: "{}", sharesandbox.UNLOCK_FILE: "[]"}
    for name, default in seeds.items():
        p = os.path.join(DATA_DIR, name)
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as h:
                h.write(default)
    for name in (sharesandbox.LOG_FILE,):
        p = os.path.join(DATA_DIR, name)
        if not os.path.exists(p):
            open(p, "a").close()
    sharesandbox.write_options(DATA_DIR, sharesandbox.curate_options(
        _opt, {"share_bind": host, "share_port": port}))
    env = dict(os.environ, SHARE_DATA_DIR="/data", TMPDIR="/data/tmp")
    cmd = ["unshare", "--mount", "--propagation", "private", "--", "/bin/sh", "/app/share_jail.sh"]
    try:
        proc = subprocess.Popen(cmd, env=env)
    except OSError as e:
        print(f"[SHARE] Sandbox-Start fehlgeschlagen: {e}", flush=True)
        return None
    url = f"http://127.0.0.1:{port}/healthz"
    for _ in range(50):                       # up to ~10 s
        if proc.poll() is not None:
            print(f"[SHARE] Sandbox-Prozess sofort beendet (Code {proc.returncode})", flush=True)
            return None
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return proc
        except Exception:
            pass
        time.sleep(0.2)
    print("[SHARE] Sandbox antwortet nicht auf /healthz - Abbruch, Rueckfall", flush=True)
    try:
        proc.terminate()
    except Exception:
        pass
    return None


def start_share_site():
    """Bring up the public share site. Returns True once it is serving."""
    host = str(_opt("share_bind", "0.0.0.0") or "0.0.0.0")
    port = int(_opt("share_port", 8101) or 8101)

    import socket as _s
    probe = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    probe.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
    try:
        probe.bind((host, port))
    except OSError as e:
        print(f"[SHARE] FEHLER: Port {port} ist bereits belegt ({e}). Laeuft ein anderes Addon auf "
              f"diesem Port (z. B. Nextcloud)? share_port aendern oder das andere Addon stoppen.", flush=True)
        return False
    finally:
        probe.close()
    if host == "127.0.0.1":
        print("[SHARE] Hinweis: share_bind=127.0.0.1 - nur ein Reverse Proxy, der selbst im Host-Netz "
              "laeuft, erreicht die Seite. Der Nginx Proxy Manager als Addon kann das NICHT; "
              "dann share_bind=0.0.0.0 und im Proxy die IP des Hosts eintragen.", flush=True)

    export = _share_export_path()
    cs_needed = bool(export or _crowdsec_installed())
    cs_path = _crowdsec_log_path(export) if cs_needed else None
    max_mb = _opt("share_log_max_mb", 5)

    mode = str(_opt("share_sandbox", "auto") or "auto").strip().lower()
    if mode not in ("auto", "on", "off"):
        mode = "auto"
    if mode != "off":
        proc = _launch_sandbox(host, port, export)
        if proc is not None:
            _SHARE.update(sandboxed=True, proc=proc)
            main_log = os.path.join(DATA_DIR, sharesandbox.LOG_FILE)
            accesslog.attach_read(main_log)          # admin reads the worker's log
            if cs_path:
                _sync_crowdsec_acquis(cs_path)        # parent owns /config
                if cs_path != main_log:
                    _start_log_shipper(main_log, cs_path, max_mb)
                    print(f"[SHARE] Protokoll wird nach {cs_path} weitergereicht (CrowdSec)", flush=True)
            return True
        if mode == "on":
            print("[SHARE] FEHLER: share_sandbox=on, aber die Sandbox startet nicht. Seite bleibt aus.", flush=True)
            return False
        print("[SHARE] Sandbox nicht verfuegbar - Rueckfall auf den Hauptprozess", flush=True)

    # ── in-process fallback (pre-3.8 behaviour) ─────────────────────────────
    if cs_needed:
        accesslog.init(os.path.join(DATA_DIR, sharesandbox.LOG_FILE), max_mb)
        cs2 = _apply_export_paths(export, announce=True)
        _sync_crowdsec_acquis(cs2)
    try:
        srv = share_worker.serve_share(_opt, lambda: load_json(SHARES_FILE, []),
                                       share_roots, DATA_DIR, blocking=False, sandboxed=False)
    except Exception as e:
        print(f"[SHARE] Start der oeffentlichen Seite fehlgeschlagen: {e}", flush=True)
        return False
    import threading
    threading.Thread(target=srv.run, daemon=True, name="share-http").start()
    _SHARE.update(sandboxed=False, proc=None)
    return True


def serve(wsgi_app, port):
    """Run the admin UI on waitress, a production WSGI server.

    Flask's built-in server is single-purpose development tooling; it is what
    ran here until 3.2.2. waitress is pure Python (no wheel needed on Alpine)
    and threaded. WEB_SERVER=werkzeug is an escape hatch for one release in
    case ingress misbehaves on a device I could not test on.
    """
    if os.environ.get("WEB_SERVER", "").lower() == "werkzeug":
        print("[WEB] WEB_SERVER=werkzeug - Entwicklungsserver", flush=True)
        wsgi_app.run(host="0.0.0.0", port=port, debug=False)
        return
    try:
        from waitress import create_server
    except ImportError:
        print("[WEB] waitress fehlt - Rueckfall auf den Entwicklungsserver", flush=True)
        wsgi_app.run(host="0.0.0.0", port=port, debug=False)
        return
    print(f"[WEB] waitress auf 0.0.0.0:{port}", flush=True)
    create_server(
        wsgi_app, host="0.0.0.0", port=port,
        threads=8,
        ident=None,                       # no "Server: waitress" header
        channel_timeout=300,              # slow USB media, long listings
        max_request_body_size=MAX_UPLOAD_BYTES + 8 * 1024 * 1024,
        asyncore_use_poll=True,
    ).run()


if __name__ == "__main__":
    _install_safe_getfqdn()
    os.makedirs(DATA_DIR, exist_ok=True)
    for fpath, default in [(SHARES_FILE, []), (USERS_FILE, []), (MOUNTS_FILE, []), (GROUPS_FILE, []), (BACKUPS_FILE, [])]:
        if not os.path.exists(fpath):
            save_json(fpath, default)
    _setup_admin_auth()
    if _sharing_should_start():
        try:
            start_share_site()
        except SystemExit:
            raise
        except Exception as e:
            print(f"[SHARE] Start der oeffentlichen Seite fehlgeschlagen: {e}", flush=True)
    port = int(os.environ.get("WEB_PORT", 8100))
    serve(app, port)
