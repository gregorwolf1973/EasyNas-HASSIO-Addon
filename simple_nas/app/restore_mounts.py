#!/usr/bin/env python3
"""Restore saved mounts via the mount_helper FIFO daemon."""
import json, os, time

MOUNTS_FILE  = "/data/mounts.json"
MOUNT_FIFO   = "/tmp/mount_cmd"
MOUNT_RESULT = "/tmp/mount_result"

# Retry settings for USB devices that aren't ready immediately at boot
MOUNT_RETRIES   = 5
MOUNT_RETRY_DELAY = 3  # seconds between retries

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
    # Prefer the resolved type saved at mount time (e.g. "ext4") over the
    # original user choice ("auto") — auto-detect fails on some USB devices
    fstype     = m.get("resolved_fstype") or m.get("fstype", "auto")
    if not device or not mountpoint:
        continue
    helper_call("MKDIR", mountpoint)
    os.makedirs(mountpoint, exist_ok=True)

    rc, out = 1, "not started"
    for attempt in range(1, MOUNT_RETRIES + 1):
        rc, out = helper_call("MOUNT", device, mountpoint, fstype)
        if rc == 0:
            break
        # USB devices may not be ready immediately at boot — retry
        if "Can't open blockdev" in out or "No such file or directory" in out:
            if attempt < MOUNT_RETRIES:
                print(f"Mount attempt {attempt}/{MOUNT_RETRIES} failed for {device} "
                      f"(device not ready yet), retrying in {MOUNT_RETRY_DELAY}s…")
                time.sleep(MOUNT_RETRY_DELAY)
                continue
        break  # non-retryable error

    if rc == 0:
        print(f"Mounted {device} -> {mountpoint}")
    else:
        print(f"Failed to mount {device}: {out}")
