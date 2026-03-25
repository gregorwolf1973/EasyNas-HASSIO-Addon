#!/usr/bin/env python3
"""Restore saved mounts from mounts.json on startup."""
import json
import os
import subprocess

MOUNTS_FILE = "/data/mounts.json"

mounts = []
try:
    with open(MOUNTS_FILE) as f:
        mounts = json.load(f)
except Exception:
    pass

for m in mounts:
    device = m.get("device")
    mountpoint = m.get("mountpoint")
    fstype = m.get("fstype", "auto")
    if not device or not mountpoint:
        continue
    os.makedirs(mountpoint, exist_ok=True)
    # Check if already mounted
    result = subprocess.run(["mountpoint", "-q", mountpoint])
    if result.returncode == 0:
        print(f"Already mounted: {mountpoint}")
        continue
    try:
        subprocess.run(
            ["mount", "-t", fstype, device, mountpoint],
            check=True, capture_output=True
        )
        print(f"Mounted {device} -> {mountpoint}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to mount {device}: {e.stderr.decode()}")
