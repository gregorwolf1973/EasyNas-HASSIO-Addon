#!/usr/bin/env python3
"""Emit hd-idle per-disk arguments for the drives listed in mounts.json.

Resolves each mount device (e.g. /dev/disk/by-id/usb-…-part1) to its base
disk name (sdb1 -> sdb, nvme0n1p1 -> nvme0n1) and prints one
'-a <disk> -i <seconds>' pair per unique disk.

Only user-mounted drives are affected. The HA system disk is never listed
in mounts.json, so it is left spinning — hd-idle is started with a global
default of '-i 0' (disabled) in run.sh, meaning any disk not named here is
never touched.
"""
import json, os, sys, re

IDLE = sys.argv[1] if len(sys.argv) > 1 else "0"
MOUNTS_FILE = "/data/mounts.json"


def base_disk(dev_path):
    """Resolve a device/partition path to its base disk name."""
    try:
        real = os.path.realpath(dev_path)
    except Exception:
        real = dev_path
    name = os.path.basename(real)
    # nvme0n1p1 -> nvme0n1 ; mmcblk0p1 -> mmcblk0
    m = re.match(r"^(nvme\d+n\d+|mmcblk\d+)p\d+$", name)
    if m:
        return m.group(1)
    # sdb1 -> sdb ; hda2 -> hda ; vdb1 -> vdb
    m = re.match(r"^([shv]d[a-z]+)\d+$", name)
    if m:
        return m.group(1)
    return name


def main():
    try:
        mounts = json.load(open(MOUNTS_FILE))
    except Exception:
        mounts = []

    seen = set()
    args = []
    for m in mounts:
        dev = m.get("device", "")
        if not dev:
            continue
        disk = base_disk(dev)
        # Skip anything that didn't resolve to a real block device name
        if not disk or not os.path.exists(f"/dev/{disk}"):
            continue
        if disk not in seen:
            seen.add(disk)
            args.append(f"-a {disk} -i {IDLE}")

    print(" ".join(args))


if __name__ == "__main__":
    main()
