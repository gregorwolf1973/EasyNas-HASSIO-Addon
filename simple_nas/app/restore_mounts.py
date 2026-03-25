#!/usr/bin/env python3
"""Restore saved mounts via host mount namespace + root filesystem."""
import json
import os
import subprocess

MOUNTS_FILE = "/data/mounts.json"

def ns_cmd(cmd):
    """nsenter with host mount namespace AND host root filesystem."""
    return [
        "nsenter",
        "--mount=/proc/1/ns/mnt",
        "--root=/proc/1/root",
        "--wd=/",
        "--"
    ] + cmd

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
    if not device or not mountpoint:
        continue

    subprocess.run(ns_cmd(["mkdir", "-p", mountpoint]), capture_output=True)
    os.makedirs(mountpoint, exist_ok=True)

    result = subprocess.run(
        ns_cmd(["mountpoint", "-q", mountpoint]), capture_output=True
    )
    if result.returncode == 0:
        print(f"Already mounted: {mountpoint}")
        continue

    cmd = ["mount"]
    if fstype and fstype != "auto":
        cmd += ["-t", fstype]
    cmd += [device, mountpoint]

    try:
        subprocess.run(ns_cmd(cmd), check=True, capture_output=True)
        print(f"Mounted {device} -> {mountpoint}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to mount {device}: {e.stderr.decode()}")
