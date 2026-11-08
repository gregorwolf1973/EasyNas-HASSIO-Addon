#!/usr/bin/env python3
"""SSD TRIM: runs fstrim on a mounted drive and remembers the result.

fstrim can take minutes on a large drive, so it runs in a background thread and
the web interface polls for the result. Not every USB-SATA bridge passes TRIM
through to the SSD; fstrim then fails with "not supported", which is reported
as a normal result rather than as an error.
"""
import ctypes
import fcntl
import json
import os
import re
import subprocess
import threading
import time

BYTES = re.compile(r"\((\d+) bytes\) trimmed")
TIMEOUT = 3600  # seconds


def parse_result(returncode, stdout, stderr):
    """Turn fstrim's exit code and output into a result dict."""
    output = (stderr or stdout or "").strip()
    if returncode == 0:
        match = BYTES.search(stdout or "")
        return {"status": "ok", "bytes": int(match.group(1)) if match else None, "message": ""}
    if "not supported" in output.lower():
        return {"status": "unsupported", "bytes": None, "message": output}
    return {"status": "error", "bytes": None, "message": output or f"fstrim exit code {returncode}"}


def run_fstrim(mountpoint, runner=subprocess.run):
    try:
        done = runner(["fstrim", "-v", mountpoint], capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError:
        return {"status": "error", "bytes": None, "message": "fstrim is not installed"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "bytes": None, "message": f"fstrim timed out after {TIMEOUT} seconds"}
    return parse_result(done.returncode, done.stdout, done.stderr)


def discard_supported(disk, sys_block="/sys/block"):
    """True if the kernel reports that the disk accepts TRIM."""
    try:
        with open(os.path.join(sys_block, disk, "queue", "discard_max_bytes")) as fh:
            return int(fh.read().strip() or 0) > 0
    except (OSError, ValueError):
        return False


# --- USB adapters that can TRIM but are not enabled by Linux -----------------
#
# Many USB-SATA/NVMe bridges (e.g. Realtek RTL9210, ASMedia) answer the SCSI
# "Logical Block Provisioning" VPD page with LBPU=1 (UNMAP supported), but the
# kernel leaves such disks in provisioning_mode "full", so fstrim says "not
# supported". Writing "unmap" there enables TRIM until the disk is reconnected.
# /sys is mounted read-only in the add-on, so a private read-write sysfs mount
# is used for that single write. Opt-in (option trim_usb_unmap), because a few
# bridges claim UNMAP support and then mishandle it.

SYSFS_RW = "/run/simple-nas-sysfs"


def _read_bytes(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return b""


def provisioning_file(disk, sys_block="/sys/block"):
    """Path of the disk's provisioning_mode below sys_block, or None."""
    base = os.path.join(sys_block, disk, "device", "scsi_disk")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    for name in names:
        path = os.path.join(base, name, "provisioning_mode")
        if os.path.exists(path):
            return path
    return None


def usb_driver(disk, sys_block="/sys/block"):
    """Kernel driver of the USB bridge ("uas", "usb-storage") or ""."""
    path = os.path.realpath(os.path.join(sys_block, disk, "device"))
    while path and path != os.path.dirname(path):
        drv = os.path.join(path, "driver")
        if os.path.islink(drv):
            name = os.path.basename(os.readlink(drv))
            if name in ("uas", "usb-storage"):
                return name
        path = os.path.dirname(path)
    return ""


# SCSI INQUIRY for a VPD page via the SG_IO ioctl. usb-storage tells the kernel
# to skip VPD pages, so /sys has no vpd_pgb0/vpd_pgb2 for such bridges even
# when they support UNMAP. Asking the drive directly is a read-only command;
# it is only sent when the user enabled trim_usb_unmap.

class _SgIoHdr(ctypes.Structure):
    _fields_ = [
        ("interface_id", ctypes.c_int), ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte), ("mx_sb_len", ctypes.c_ubyte), ("iovec_count", ctypes.c_ushort),
        ("dxfer_len", ctypes.c_uint), ("dxferp", ctypes.c_void_p), ("cmdp", ctypes.c_void_p),
        ("sbp", ctypes.c_void_p), ("timeout", ctypes.c_uint), ("flags", ctypes.c_uint),
        ("pack_id", ctypes.c_int), ("usr_ptr", ctypes.c_void_p),
        ("status", ctypes.c_ubyte), ("masked_status", ctypes.c_ubyte), ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte), ("host_status", ctypes.c_ushort), ("driver_status", ctypes.c_ushort),
        ("resid", ctypes.c_int), ("duration", ctypes.c_uint), ("info", ctypes.c_uint),
    ]


SG_IO = 0x2285
SG_DXFER_FROM_DEV = -3


def scsi_vpd(disk, page, dev="/dev"):
    """Read one VPD page from /dev/<disk> with INQUIRY; b"" if that fails."""
    size = 252
    data = ctypes.create_string_buffer(size)
    sense = ctypes.create_string_buffer(32)
    cdb = (ctypes.c_ubyte * 6)(0x12, 0x01, page, 0, size, 0)
    hdr = _SgIoHdr(interface_id=ord("S"), dxfer_direction=SG_DXFER_FROM_DEV, cmd_len=6,
                   mx_sb_len=len(sense), dxfer_len=size, dxferp=ctypes.addressof(data),
                   cmdp=ctypes.addressof(cdb), sbp=ctypes.addressof(sense), timeout=5000)
    try:
        fd = os.open(os.path.join(dev, disk), os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return b""
    try:
        fcntl.ioctl(fd, SG_IO, hdr)
    except OSError:
        return b""
    finally:
        os.close(fd)
    if hdr.status or hdr.host_status:
        return b""
    got = data.raw[:max(0, size - hdr.resid)]
    return got if len(got) >= 4 and got[1] == page else b""


def unmap_info(disk, sys_block="/sys/block", ask_device=None):
    """What the disk reports about UNMAP: dict with usb, driver, mode, lbpu, max_unmap, vpd.

    vpd says where the VPD pages came from: "sysfs", "device" (asked with
    ask_device, a function like scsi_vpd, because /sys has none) or "none".
    """
    real = os.path.realpath(os.path.join(sys_block, disk))
    pgb2 = _read_bytes(os.path.join(sys_block, disk, "device", "vpd_pgb2"))
    pgb0 = _read_bytes(os.path.join(sys_block, disk, "device", "vpd_pgb0"))
    source = "sysfs" if pgb2 else "none"
    if not pgb2 and ask_device is not None:
        pgb2 = ask_device(disk, 0xB2)
        if pgb2:
            pgb0 = pgb0 or ask_device(disk, 0xB0)
            source = "device"
    path = provisioning_file(disk, sys_block)
    mode = ""
    if path:
        mode = _read_bytes(path).decode("ascii", "replace").strip()
    return {
        "usb": "/usb" in real,
        "driver": usb_driver(disk, sys_block),
        "mode": mode,
        # page B2 (Logical Block Provisioning), byte 5 bit 7 = LBPU
        "lbpu": len(pgb2) >= 6 and pgb2[1] == 0xB2 and bool(pgb2[5] & 0x80),
        # page B0 (Block Limits), bytes 20-23 = maximum UNMAP LBA count
        "max_unmap": int.from_bytes(pgb0[20:24], "big") if len(pgb0) >= 24 and pgb0[1] == 0xB0 else 0,
        "vpd": source,
    }


def possible(info):
    """True if unmap_info() describes a USB disk whose TRIM can be switched on."""
    # Without the Block Limits page (usb-storage) the kernel uses its own
    # maximum, so a missing max_unmap only counts against VPD read from /sys.
    return (info["usb"] and info["mode"] == "full" and info["lbpu"]
            and (info["max_unmap"] > 0 or info["vpd"] == "device"))


def unmap_possible(disk, sys_block="/sys/block", ask_device=None):
    """True for a USB disk that reports UNMAP support but has it switched off."""
    return possible(unmap_info(disk, sys_block, ask_device))


def describe(disk, info):
    """One log line saying why TRIM was not switched on."""
    if not info["usb"]:
        why = "not a USB drive"
    elif info["mode"] != "full":
        why = f"provisioning_mode is '{info['mode'] or 'missing'}', not 'full'"
    elif info["vpd"] == "none":
        why = "the adapter answers no VPD pages, so UNMAP support is unknown"
    elif not info["lbpu"]:
        why = "the drive does not report UNMAP support (LBPU=0)"
    else:
        why = "the drive reports a maximum UNMAP size of 0"
    return (f"TRIM not switched on for {disk}: {why} "
            f"(driver {info['driver'] or 'unknown'}, mode {info['mode'] or '-'}, "
            f"LBPU {int(info['lbpu'])}, max_unmap {info['max_unmap']}, VPD from {info['vpd']})")


def _mount_sysfs(target):
    os.makedirs(target, exist_ok=True)
    subprocess.run(["mount", "-t", "sysfs", "sysfs", target], check=True, capture_output=True, timeout=20)


def _umount(target):
    subprocess.run(["umount", target], capture_output=True, timeout=20)
    try:
        os.rmdir(target)
    except OSError:
        pass


def enable_unmap(disk, sys_block="/sys/block", rw_root=SYSFS_RW, mount=_mount_sysfs, umount=_umount,
                 ask_device=scsi_vpd):
    """Switch a USB disk to provisioning_mode "unmap". Returns a short message.

    Only touches disks for which unmap_possible() is true; for everything else
    it returns the reason and leaves the disk alone.
    """
    if discard_supported(disk, sys_block):
        return None
    info = unmap_info(disk, sys_block, ask_device)
    if not info["usb"]:
        return None
    if not possible(info):
        return describe(disk, info)
    try:
        mount(rw_root)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"cannot enable TRIM on {disk}: {exc}"
    try:
        path = provisioning_file(disk, os.path.join(rw_root, "block"))
        if not path:
            return f"cannot enable TRIM on {disk}: provisioning_mode not found"
        with open(path, "w") as fh:
            fh.write("unmap")
    except OSError as exc:
        return f"cannot enable TRIM on {disk}: {exc}"
    finally:
        umount(rw_root)
    if discard_supported(disk, sys_block):
        return f"TRIM enabled on {disk} (provisioning_mode unmap)"
    return f"TRIM switched on for {disk}, but the kernel still reports no discard support"


def is_due(last_run, days, now):
    return days > 0 and (last_run is None or now - last_run >= days * 86400)


class Trimmer:
    """Runs one fstrim per mountpoint and keeps the last result in a JSON file."""

    def __init__(self, path, run=run_fstrim, clock=time.time, prepare=None):
        self.path = path
        self.run = run
        self.prepare = prepare   # called with the mountpoint before fstrim; may return extra result fields
        self.clock = clock
        self.lock = threading.Lock()
        self.running = set()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data):
        tmp = f"{self.path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self.path)
        except OSError as exc:
            print(f"[TRIM] cannot save results: {exc}", flush=True)

    def status(self):
        with self.lock:
            return {"running": sorted(self.running), "results": self._load()}

    def last_run(self, mountpoint):
        with self.lock:
            return self._load().get(mountpoint, {}).get("time")

    def execute(self, mountpoint):
        """Run fstrim now and return its result; None if it is already running."""
        with self.lock:
            if mountpoint in self.running:
                return None
            self.running.add(mountpoint)
        result = {"status": "error", "bytes": None, "message": "fstrim failed"}
        try:
            extra = (self.prepare(mountpoint) if self.prepare else None) or {}
            result = dict(self.run(mountpoint))
            result.update(extra)
        finally:
            result["time"] = self.clock()
            with self.lock:
                data = self._load()
                data[mountpoint] = result
                self._save(data)
                self.running.discard(mountpoint)
        print(f"[TRIM] {mountpoint}: {result['status']} {result.get('bytes') or ''} "
              f"{result.get('message') or ''}".rstrip(), flush=True)
        return result

    def start(self, mountpoint):
        """Run fstrim in the background. False if it is already running."""
        with self.lock:
            if mountpoint in self.running:
                return False
        threading.Thread(target=self.execute, args=(mountpoint,), daemon=True, name="fstrim").start()
        return True
