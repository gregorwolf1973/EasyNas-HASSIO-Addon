#!/usr/bin/env python3
"""Simple NAS - Flask Web GUI"""
import json
import os
import subprocess
import re
import shutil
from flask import Flask, jsonify, request, render_template, abort

app = Flask(__name__)

DATA_DIR    = "/data"
SHARES_FILE = f"{DATA_DIR}/shares.json"
USERS_FILE  = f"{DATA_DIR}/users.json"
MOUNTS_FILE = f"{DATA_DIR}/mounts.json"
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
    try:
        subprocess.run(["pkill", "-HUP", "smbd"], check=False)
    except Exception:
        pass
    subprocess.run(
        ["python3", "/app/generate_smb_conf.py", WORKGROUP],
        check=False
    )
    try:
        subprocess.run(["smbcontrol", "smbd", "reload-config"], check=False, timeout=5)
    except Exception:
        pass

def get_disk_usage(path):
    try:
        usage = shutil.disk_usage(path)
        return {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": round(usage.used / usage.total * 100, 1) if usage.total else 0
        }
    except Exception:
        return None

# ─────────────────────────── drives API ─────────────────────────

def list_block_devices():
    """List all block devices using lsblk."""
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-o",
             "NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINT,UUID,MODEL,VENDOR,HOTPLUG,RM"],
            capture_output=True, text=True, check=True
        )
        data = json.loads(result.stdout)
        return data.get("blockdevices", [])
    except Exception as e:
        return []

def flatten_devices(devices, parent=None):
    result = []
    for dev in devices:
        d = {
            "name": dev.get("name"),
            "path": f"/dev/{dev.get('name')}",
            "size": dev.get("size", "?"),
            "type": dev.get("type"),
            "fstype": dev.get("fstype"),
            "label": dev.get("label"),
            "mountpoint": dev.get("mountpoint"),
            "uuid": dev.get("uuid"),
            "model": dev.get("model") or (parent.get("model") if parent else None),
            "vendor": dev.get("vendor") or (parent.get("vendor") if parent else None),
            "removable": dev.get("rm") == "1" or dev.get("hotplug") == "1",
            "parent": parent.get("name") if parent else None,
        }
        result.append(d)
        children = dev.get("children", [])
        if children:
            result.extend(flatten_devices(children, dev))
    return result

@app.route("/api/drives")
def api_drives():
    devices = flatten_devices(list_block_devices())
    # Filter: only disks and partitions with a filesystem or bare disk
    drives = [d for d in devices if d["type"] in ("disk", "part")]
    return jsonify(drives)

@app.route("/api/drives/mount", methods=["POST"])
def api_mount():
    body = request.get_json()
    device    = body.get("device")
    mountpoint = body.get("mountpoint")
    fstype    = body.get("fstype", "auto")

    if not device or not mountpoint:
        return jsonify({"error": "device and mountpoint required"}), 400

    # Sanitize mountpoint
    if not mountpoint.startswith("/mnt/"):
        mountpoint = "/mnt/" + re.sub(r"[^a-zA-Z0-9_\-]", "_", mountpoint.lstrip("/"))

    os.makedirs(mountpoint, exist_ok=True)

    try:
        cmd = ["mount"]
        if fstype and fstype != "auto":
            cmd += ["-t", fstype]
        cmd += [device, mountpoint]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        return jsonify({"error": e.stderr or "Mount failed"}), 500

    # Save to mounts.json
    mounts = load_json(MOUNTS_FILE, [])
    mounts = [m for m in mounts if m.get("device") != device]
    mounts.append({"device": device, "mountpoint": mountpoint, "fstype": fstype})
    save_json(MOUNTS_FILE, mounts)

    return jsonify({"ok": True, "mountpoint": mountpoint})

@app.route("/api/drives/unmount", methods=["POST"])
def api_unmount():
    body = request.get_json()
    mountpoint = body.get("mountpoint")
    device     = body.get("device")

    if not mountpoint and not device:
        return jsonify({"error": "mountpoint or device required"}), 400

    target = mountpoint or device
    try:
        subprocess.run(["umount", target], capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        return jsonify({"error": e.stderr or "Unmount failed"}), 500

    # Remove from mounts.json
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
        if usage:
            s["usage"] = usage
    return jsonify(shares)

@app.route("/api/shares", methods=["POST"])
def api_create_share():
    body = request.get_json()
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
        "name": name,
        "path": path,
        "comment": body.get("comment", ""),
        "writable": body.get("writable", True),
        "public": body.get("public", False),
        "users": body.get("users", []),
    }
    shares.append(share)
    save_json(SHARES_FILE, shares)
    reload_samba()
    return jsonify(share), 201

@app.route("/api/shares/<name>", methods=["PUT"])
def api_update_share(name):
    body = request.get_json()
    shares = load_json(SHARES_FILE, [])
    for s in shares:
        if s["name"] == name:
            s.update({k: v for k, v in body.items() if k in
                      ("comment", "writable", "public", "users", "path")})
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
    return jsonify(load_json(USERS_FILE, []))

@app.route("/api/users", methods=["POST"])
def api_create_user():
    body = request.get_json()
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()

    if not username or not password:
        return jsonify({"error": "username and password required"}), 400

    username = re.sub(r"[^a-zA-Z0-9_\-]", "", username)

    # Add system user (no login shell)
    subprocess.run(["adduser", "-D", "-H", "-s", "/sbin/nologin", username],
                   capture_output=True)
    # Set Samba password
    proc = subprocess.run(
        ["smbpasswd", "-a", "-s", username],
        input=f"{password}\n{password}\n",
        text=True, capture_output=True
    )
    if proc.returncode != 0:
        return jsonify({"error": proc.stderr or "Failed to set password"}), 500

    users = load_json(USERS_FILE, [])
    users = [u for u in users if u["username"] != username]
    users.append({"username": username})
    save_json(USERS_FILE, users)
    return jsonify({"username": username}), 201

@app.route("/api/users/<username>", methods=["PUT"])
def api_update_user(username):
    body = request.get_json()
    password = body.get("password", "").strip()
    if not password:
        return jsonify({"error": "password required"}), 400
    proc = subprocess.run(
        ["smbpasswd", "-s", username],
        input=f"{password}\n{password}\n",
        text=True, capture_output=True
    )
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
    return jsonify({"ok": True})

# ─────────────────────────── status API ─────────────────────────

@app.route("/api/status")
def api_status():
    import psutil
    smbd_running = any(p.name() == "smbd" for p in psutil.process_iter(["name"]))
    nmbd_running = any(p.name() == "nmbd" for p in psutil.process_iter(["name"]))
    mem = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=0.1)
    disk = shutil.disk_usage("/")
    return jsonify({
        "smbd": smbd_running,
        "nmbd": nmbd_running,
        "cpu_percent": cpu,
        "memory": {
            "total": mem.total,
            "used": mem.used,
            "percent": mem.percent
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": round(disk.used / disk.total * 100, 1)
        }
    })

@app.route("/api/samba/restart", methods=["POST"])
def api_restart_samba():
    subprocess.run(["pkill", "smbd"], check=False)
    subprocess.run(["pkill", "nmbd"], check=False)
    import time; time.sleep(1)
    subprocess.Popen(["smbd", "--no-process-group", "--foreground", "--log-stdout"])
    subprocess.Popen(["nmbd", "--no-process-group", "--foreground", "--log-stdout"])
    return jsonify({"ok": True})

# ─────────────────────────── main ───────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    for f, d in [(SHARES_FILE, []), (USERS_FILE, []), (MOUNTS_FILE, [])]:
        if not os.path.exists(f):
            save_json(f, d)
    app.run(host="0.0.0.0", port=8099, debug=False)
