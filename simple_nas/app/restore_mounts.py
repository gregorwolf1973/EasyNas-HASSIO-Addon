#!/usr/bin/env python3
"""Restore saved mounts via the mount_helper FIFO daemon."""
import json, os, time

MOUNTS_FILE  = "/data/mounts.json"
MOUNT_FIFO   = "/tmp/mount_cmd"
MOUNT_RESULT = "/tmp/mount_result"

def helper_call(action, arg1="", arg2="", arg3="", timeout=15):
    try:
        os.remove(MOUNT_RESULT)
    except FileNotFoundError:
        pass
    cmd_line = f"{action}|{arg1}|{arg2}|{arg3}\n"
    try:
        fd = os.open(MOUNT_FIFO, os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, cmd_line.encode())
        os.close(fd)
    except OSError as e:
        return 1, str(e)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(MOUNT_RESULT):
            try:
                with open(MOUNT_RESULT) as f:
                    line = f.read().strip()
                rc_str, _, out = line.partition("|")
                return int(rc_str), out
            except Exception:
                pass
        time.sleep(0.1)
    return 1, "Timeout"

mounts = []
try:
    with open(MOUNTS_FILE) as f:
        mounts = json.load(f)
except Exception:
    pass

for m in mounts:
    device     = m.get("device")
    mountpoint = m.get("mountpoint")
    fstype     = m.get("fstype", "auto")
    share_bind = m.get("share_bind")
    if not device or not mountpoint:
        continue
    helper_call("MKDIR", mountpoint)
    os.makedirs(mountpoint, exist_ok=True)
    rc, out = helper_call("MOUNT", device, mountpoint, fstype)
    if rc == 0:
        print(f"Mounted {device} -> {mountpoint}")
        # Restore bind-mount to /share/<name> for HA Core access
        if share_bind:
            os.makedirs(share_bind, exist_ok=True)
            rc_b, out_b = helper_call("BIND", mountpoint, share_bind)
            if rc_b == 0:
                print(f"  Bind {mountpoint} -> {share_bind}")
            else:
                print(f"  Bind to {share_bind} failed: {out_b}")
    else:
        print(f"Failed to mount {device}: {out}")
