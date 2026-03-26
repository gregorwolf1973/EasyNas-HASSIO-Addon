#!/usr/bin/env python3
"""Simple NAS - Flask Web GUI v2.0"""
import json
import os
import subprocess
import re
import shutil
from flask import Flask, jsonify, request, render_template

app = Flask(__name__)

DATA_DIR    = "/data"
SHARES_FILE = f"{DATA_DIR}/shares.json"
USERS_FILE  = f"{DATA_DIR}/users.json"
MOUNTS_FILE = f"{DATA_DIR}/mounts.json"
GROUPS_FILE = f"{DATA_DIR}/groups.json"
WORKGROUP   = os.environ.get("WORKGROUP", "WORKGROUP")

# ─────────────────────────── helpers ────────────────────────────

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def reload_samba():
    subprocess.run(["python3", "/app/generate_smb_conf.py", WORKGROUP], check=False)
    try:
        subprocess.run(["smbcontrol", "smbd", "reload-config"], check=False, timeout=5)
    except Exception:
        subprocess.run(["pkill", "-HUP", "smbd"], check=False)

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

def list_block_devices():
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-o",
             "NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINT,UUID,MODEL,VENDOR,HOTPLUG,RM"],
            capture_output=True, text=True, check=True)
        return json.loads(result.stdout).get("blockdevices", [])
    except Exception:
        return []

def flatten_devices(devices, parent=None):
    result = []
    for dev in devices:
        d = {
            "name": dev.get("name"), "path": f"/dev/{dev.get('name')}",
            "size": dev.get("size", "?"), "type": dev.get("type"),
            "fstype": dev.get("fstype"), "label": dev.get("label"),
            "mountpoint": dev.get("mountpoint"), "uuid": dev.get("uuid"),
            "model": dev.get("model") or (parent.get("model") if parent else None),
            "vendor": dev.get("vendor") or (parent.get("vendor") if parent else None),
            "removable": dev.get("rm") == "1" or dev.get("hotplug") == "1",
            "parent": parent.get("name") if parent else None,
        }
        result.append(d)
        for child in dev.get("children", []):
            result.extend(flatten_devices([child], dev))
    return result

@app.route("/api/drives")
def api_drives():
    devices = flatten_devices(list_block_devices())
    drives = [d for d in devices if d["type"] in ("disk", "part")]
    return jsonify(drives)

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
    try:
        fd = os.open(MOUNT_FIFO, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, cmd_line.encode())
        os.close(fd)
    except OSError as e:
        return 1, f"FIFO-Fehler: {e}"
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

    # If mountpoint doesn't start with /, treat as name under /media
    if not mountpoint.startswith("/"):
        safe_name  = re.sub(r"[^a-zA-Z0-9_\-]", "_", mountpoint)
        mountpoint = f"/media/{safe_name}"

    _helper_call("MKDIR", mountpoint)
    os.makedirs(mountpoint, exist_ok=True)

    rc, out = _helper_call("MOUNT", device, mountpoint, fstype)
    if rc != 0:
        return jsonify({"error": out or "Mount fehlgeschlagen"}), 500

    mounts = load_json(MOUNTS_FILE, [])
    mounts = [m for m in mounts if m.get("device") != device]
    mounts.append({"device": device, "mountpoint": mountpoint, "fstype": fstype})
    save_json(MOUNTS_FILE, mounts)
    return jsonify({"ok": True, "mountpoint": mountpoint})

@app.route("/api/drives/unmount", methods=["POST"])
def api_unmount():
    body       = request.get_json(force=True)
    mountpoint = body.get("mountpoint", "").strip()
    device     = body.get("device", "").strip()
    if not mountpoint and not device:
        return jsonify({"error": "mountpoint or device required"}), 400
    target = mountpoint or device
    rc, out = _helper_call("UMOUNT", target)
    if rc != 0:
        return jsonify({"error": out or "Aushängen fehlgeschlagen"}), 500
    mounts = load_json(MOUNTS_FILE, [])
    mounts = [m for m in mounts if m.get("mountpoint") != mountpoint and m.get("device") != device]
    save_json(MOUNTS_FILE, mounts)
    return jsonify({"ok": True})

# ─────────────────────────── shares API ─────────────────────────

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
    path = body.get("path", "").strip()
    if not name or not path:
        return jsonify({"error": "name and path required"}), 400
    name = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    os.makedirs(path, exist_ok=True)
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
    shares = load_json(SHARES_FILE, [])
    for s in shares:
        if s["name"] == name:
            for key in ("comment", "writable", "public", "users", "groups", "path"):
                if key in body: s[key] = body[key]
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
    return jsonify({"ok": True})

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
    return jsonify({"username": username}), 201

@app.route("/api/users/<username>", methods=["PUT"])
def api_update_user(username):
    body     = request.get_json(force=True)
    password = body.get("password", "").strip()
    if not password:
        return jsonify({"error": "password required"}), 400
    proc = subprocess.run(["smbpasswd", "-s", username],
        input=f"{password}\n{password}\n", text=True, capture_output=True)
    if proc.returncode != 0:
        return jsonify({"error": proc.stderr or "Failed"}), 500
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

# ─────────────────────────── browse API ─────────────────────────

@app.route("/api/browse")
def api_browse():
    path = request.args.get("path", "/").strip()
    path = os.path.realpath(path)
    if not path.startswith("/"): path = "/"
    entries = []
    try:
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            try:
                if not os.path.isdir(full): continue
                entries.append({"name": name, "path": full, "readable": os.access(full, os.R_OK)})
            except Exception: pass
    except PermissionError:
        # Fallback: use ls via subprocess (runs as root)
        try:
            result = subprocess.run(
                ["ls", "-1", "-d", path + "/*/"],
                capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    line = line.rstrip("/")
                    if line:
                        name = os.path.basename(line)
                        entries.append({"name": name, "path": line, "readable": True})
        except Exception:
            return jsonify({"error": "Kein Zugriff auf " + path}), 403
    except FileNotFoundError:
        return jsonify({"error": "Pfad nicht gefunden"}), 404
    parts = []
    cur = path
    while True:
        parent = os.path.dirname(cur)
        parts.insert(0, {"name": os.path.basename(cur) or "/", "path": cur})
        if parent == cur: break
        cur = parent
    return jsonify({"path": path, "parent": os.path.dirname(path) if path != "/" else None,
                     "breadcrumb": parts, "entries": entries})

@app.route("/api/mkdir", methods=["POST"])
def api_mkdir():
    body = request.get_json(force=True)
    path = body.get("path", "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    try:
        os.makedirs(path, exist_ok=True)
        return jsonify({"ok": True, "path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─────────────────────────── frontend ───────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    for fpath, default in [(SHARES_FILE, []), (USERS_FILE, []), (MOUNTS_FILE, []), (GROUPS_FILE, [])]:
        if not os.path.exists(fpath):
            save_json(fpath, default)
    app.run(host="0.0.0.0", port=8099, debug=False)
