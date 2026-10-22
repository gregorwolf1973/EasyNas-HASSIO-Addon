"""Explicit, FD-bound NTFS3 capacity measurement. No polling or block reads.

Linux adapter is deliberately injectable: local tests use synthetic metadata
only. Public error messages are fixed codes, never paths, aliases or secrets.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import threading


class VerificationError(Exception):
    pass


def fail(code):
    raise VerificationError(code)


def unescape(value):
    # mountinfo uses octal escapes, not shell/URL escaping.
    return re.sub(r"\\(040|011|012|134)", lambda m: chr(int(m[1], 8)), value)


@dataclass(frozen=True)
class Mount:
    mount_id: int
    device: str
    root: str
    path: str
    fstype: str


def parse_mounts(text):
    result = []
    try:
        for line in text.splitlines():
            left, right = line.split(" - ", 1)
            fields, fs = left.split(), right.split()
            major, minor = fields[2].split(":")
            device = f"{int(major)}:{int(minor)}"
            if int(major) < 0 or int(minor) < 0 or int(fields[0]) <= 0 or len(fs) < 3:
                fail("invalid_mountinfo")
            result.append(Mount(int(fields[0]), device, unescape(fields[3]),
                                unescape(fields[4]), fs[0]))
        if not result or len({m.mount_id for m in result}) != len(result):
            fail("invalid_mountinfo")
    except (ValueError, IndexError):
        fail("invalid_mountinfo")
    return result


def safe_root(path):
    if not isinstance(path, str) or "\x00" in path:
        fail("invalid_mount_path")
    parsed = PurePosixPath(path)
    # One direct /media mount, no arbitrary directories or share paths.
    if parsed.parent != PurePosixPath("/media") or ".." in parsed.parts or str(parsed) != path:
        fail("invalid_mount_path")
    return path


def alias_matches(alias, identity_hash, partition):
    suffix = f"-part{partition}"
    return (alias.startswith("usb-") and alias.endswith(suffix)
            and hashlib.sha256(alias[:-len(suffix)].encode()).hexdigest() == identity_hash)


@dataclass(frozen=True)
class Identity:
    partition_device: str
    disk_device: str
    diskseq: int


class Linux:
    """Reads kernel metadata; only open_root/fstatvfs may touch the WD FS."""

    def supported(self):
        if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "fstatvfs")):
            fail("unsupported_platform")
        if os.open not in os.supports_dir_fd:
            fail("unsupported_platform")

    @staticmethod
    def text(path, limit=1024 * 1024):
        with open(path, encoding="utf-8") as file:
            text = file.read(limit + 1)
        if len(text) > limit:
            fail("metadata_too_large")
        return text

    def identity(self, alias, partition):
        # stat follows the by-id link but never opens the block device.
        node = os.stat("/dev/disk/by-id/" + alias)
        if not stat.S_ISBLK(node.st_mode):
            fail("not_block_metadata")
        device = f"{os.major(node.st_rdev)}:{os.minor(node.st_rdev)}"
        sys_path = Path("/sys/dev/block/" + device).resolve(strict=True)
        if not str(sys_path).startswith("/sys/devices/"):
            fail("invalid_sysfs")
        if int(self.text(sys_path / "partition", 64).strip()) != partition:
            fail("wrong_partition")
        if self.text(sys_path / "dev", 64).strip() != device:
            fail("device_mismatch")
        disk = sys_path.parent
        sequence = int(self.text(disk / "diskseq", 64).strip())
        disk_device = self.text(disk / "dev", 64).strip()
        if sequence <= 0 or not re.fullmatch(r"[0-9]+:[0-9]+", disk_device):
            fail("invalid_disk_metadata")
        return Identity(device, disk_device, sequence)

    def mounts(self):
        return parse_mounts(self.text("/proc/self/mountinfo"))

    def open_root(self, path):
        # Reject symlinks at EVERY component, including /media. No realpath()
        # over the target filesystem and no follow-through into arbitrary files.
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open("/", flags)
        try:
            for component in PurePosixPath(path).parts[1:]:
                child = os.open(component, flags, dir_fd=fd)
                previous, fd = fd, child
                os.close(previous)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def fd_identity(self, fd):
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            fail("not_directory")
        ids = [line.split(":", 1)[1].strip()
               for line in self.text(f"/proc/self/fdinfo/{fd}").splitlines()
               if line.startswith("mnt_id:")]
        if len(ids) != 1:
            fail("missing_fd_mount_id")
        return int(ids[0]), f"{os.major(info.st_dev)}:{os.minor(info.st_dev)}", info.st_ino

    def capacity(self, fd):
        return os.fstatvfs(fd)  # Exactly one call on the retained directory FD.

    def close(self, fd):
        os.close(fd)


def selected_mount(mounts, root, identity):
    matches = [m for m in mounts if m.path == root]
    if len(matches) != 1:
        fail("mount_unverified")
    mount = matches[0]
    if mount.device != identity.partition_device or mount.root != "/" or mount.fstype != "ntfs3":
        fail("wrong_filesystem")
    if any(m.path.startswith(root + "/") for m in mounts):
        fail("submount_conflict")
    return mount


def usage_values(value):
    counts = (value.f_frsize, value.f_blocks, value.f_bfree, value.f_bavail)
    if any(type(x) is not int or x < 0 for x in counts):
        fail("invalid_capacity")
    size, blocks, free_blocks, available = counts
    if size == 0 or blocks == 0 or not 0 <= available <= free_blocks <= blocks:
        fail("invalid_capacity")
    # Keep the existing capacity_core contract used + free == total without
    # silently changing the meaning of Simple NAS/shutil's available space.
    if free_blocks != available:
        fail("reserved_space_unsupported")
    total, free = blocks * size, available * size
    if total > 2**63 - 1:
        fail("invalid_capacity")
    used = total - free
    return dict(total=total, used=used, free=free, percent=round(used / total * 100, 1))


class Meter:
    def __init__(self, backend=None):
        self.backend = backend if backend is not None else Linux()
        self.lock = threading.Lock()

    def measure(self, mounts_config, identity_hash, partition):
        if not isinstance(identity_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", identity_hash):
            fail("invalid_identity")
        if type(partition) is not int or not 1 <= partition <= 128:
            fail("invalid_partition")
        if not self.lock.acquire(blocking=False):
            fail("busy")
        fd = None
        try:
            self.backend.supported()
            if not isinstance(mounts_config, list):
                fail("invalid_configuration")
            targets = []
            for entry in mounts_config:
                if not isinstance(entry, dict):
                    fail("invalid_configuration")
                device = entry.get("device", "")
                if not isinstance(device, str) or not device.startswith("/dev/disk/by-id/"):
                    continue
                alias = device.removeprefix("/dev/disk/by-id/")
                if "/" not in alias and alias_matches(alias, identity_hash, partition):
                    targets.append((alias, safe_root(entry.get("mountpoint"))))
            if len(targets) != 1:
                fail("configured_identity_unverified")
            alias, root = targets[0]
            identity = self.backend.identity(alias, partition)
            mount = selected_mount(self.backend.mounts(), root, identity)
            # From here onward an explicitly authorized filesystem access occurs.
            fd = self.backend.open_root(root)
            reference = self.backend.fd_identity(fd)
            if reference[:2] != (mount.mount_id, identity.partition_device):
                fail("fd_mount_mismatch")

            def recheck():
                if self.backend.identity(alias, partition) != identity:
                    fail("device_changed")
                if selected_mount(self.backend.mounts(), root, identity) != mount:
                    fail("mount_changed")
                if self.backend.fd_identity(fd) != reference:
                    fail("fd_changed")

            recheck()
            usage = usage_values(self.backend.capacity(fd))
            recheck()
            return dict(schema=1, usage=usage,
                        measured_at=datetime.now(timezone.utc).isoformat(),
                        provenance=dict(disk_id_sha256=identity_hash, partition=partition,
                                        major_minor=identity.partition_device,
                                        diskseq=identity.diskseq, mount_id=mount.mount_id,
                                        fstype=mount.fstype, binding="retained_directory_fd"))
        except VerificationError:
            raise
        except (OSError, ValueError, TypeError, OverflowError, AttributeError):
            fail("measurement_failed")
        finally:
            try:
                if fd is not None:
                    try:
                        self.backend.close(fd)
                    except OSError:
                        fail("descriptor_close_failed")
            finally:
                self.lock.release()
