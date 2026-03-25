#!/usr/bin/env python3
"""Restore saved mounts from mounts.json on startup via host namespace (nsenter)."""
import json
import os
import subprocess

MOUNTS_FILE = "/data/mounts.json"

def host_cmd(cmd):
    """Wrap command in nsenter to run in host mount namespace."""
    return ["nsenter", "-t", "1", "-m", "--"] + cmd

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

    # Create mountpoint directory on host
    subprocess.run(host_cmd(["mkdir", "-p", mountpoint]),
                   capture_output=True)

    # Check if already mounted on host
    result = subprocess.run(
        host_cmd(["mountpoint", "-q", mountpoint]),
        capture_output=True
    )
    if result.returncode == 0:
        print(f"Already mounted: {mountpoint}")
        continue

    # Mount on host
    cmd = ["mount"]
    if fstype and fstype != "auto":
        cmd += ["-t", fstype]
    cmd += [device, mountpoint]

    try:
        subprocess.run(host_cmd(cmd), check=True, capture_output=True)
        print(f"Mounted {device} -> {mountpoint}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to mount {device}: {e.stderr.decode()}")
