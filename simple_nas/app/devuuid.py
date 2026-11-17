#!/usr/bin/env python3
"""Find saved drives again by their file system UUID.

mounts.json stores a /dev/disk/by-id path. For USB drives that name contains
the serial number of the USB adapter, not of the disk: after swapping the
adapter or enclosure the path no longer exists and the drive stays unmounted
although nothing changed on it. The UUID is written into the file system at
format time and travels with the data, so it is kept as a second key.
"""
import os
import stat
import subprocess

BY_UUID_DIR = "/dev/disk/by-uuid"


def _is_block(path):
    try:
        return stat.S_ISBLK(os.stat(path).st_mode)
    except OSError:
        return False


def uuid_of(device, by_uuid_dir=BY_UUID_DIR, blkid=True):
    """File system UUID of a device path (or None)."""
    try:
        real = os.path.realpath(device)
    except Exception:
        return None
    try:
        for name in sorted(os.listdir(by_uuid_dir)):
            try:
                if os.path.realpath(os.path.join(by_uuid_dir, name)) == real:
                    return name
            except Exception:
                pass
    except OSError:
        pass
    if blkid and _is_block(real):
        try:
            out = subprocess.run(["blkid", "-o", "value", "-s", "UUID", real],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
            return out or None
        except Exception:
            pass
    return None


def resolve(entry, by_uuid_dir=BY_UUID_DIR, is_block=_is_block):
    """Device path to mount for a mounts.json entry.

    Returns (path, how) with how = "device" (saved path present), "uuid"
    (found again by UUID) or (None, None) when the drive is not there (yet)."""
    device = entry.get("device") or ""
    if device and is_block(device):
        return device, "device"
    uuid = entry.get("uuid")
    if uuid and "/" not in uuid:
        path = os.path.join(by_uuid_dir, uuid)
        if is_block(path):
            return path, "uuid"
    return None, None
