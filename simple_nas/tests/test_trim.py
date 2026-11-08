#!/usr/bin/env python3
"""SSD TRIM: reading fstrim's answer, the schedule and one run per drive."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import trim  # noqa: E402

DAY = 86400


class ParseTest(unittest.TestCase):
    def test_success_with_bytes(self):
        result = trim.parse_result(0, "/mnt/ssd: 1.2 GiB (1288490188 bytes) trimmed on /dev/sdb1\n", "")
        self.assertEqual(result, {"status": "ok", "bytes": 1288490188, "message": ""})

    def test_success_without_size(self):
        self.assertIsNone(trim.parse_result(0, "", "")["bytes"])

    def test_unsupported_bridge(self):
        result = trim.parse_result(1, "", "fstrim: /mnt/ssd: the discard operation is not supported\n")
        self.assertEqual(result["status"], "unsupported")

    def test_other_error_keeps_message(self):
        result = trim.parse_result(32, "", "fstrim: /mnt/ssd: FITRIM ioctl failed: Input/output error")
        self.assertEqual(result["status"], "error")
        self.assertIn("Input/output", result["message"])


class FakeProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class RunTest(unittest.TestCase):
    def test_calls_fstrim_verbose(self):
        calls = []

        def runner(cmd, **kwargs):
            calls.append(cmd)
            return FakeProcess(0, "x (10 bytes) trimmed")

        self.assertEqual(trim.run_fstrim("/mnt/a", runner)["bytes"], 10)
        self.assertEqual(calls, [["fstrim", "-v", "/mnt/a"]])

    def test_missing_program(self):
        def runner(cmd, **kwargs):
            raise FileNotFoundError

        self.assertEqual(trim.run_fstrim("/mnt/a", runner)["status"], "error")

    def test_timeout(self):
        def runner(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 1)

        self.assertIn("timed out", trim.run_fstrim("/mnt/a", runner)["message"])


class ScheduleTest(unittest.TestCase):
    def test_off_never_due(self):
        self.assertFalse(trim.is_due(None, 0, 1000))

    def test_first_run_is_due(self):
        self.assertTrue(trim.is_due(None, 30, 1000))

    def test_due_after_interval(self):
        self.assertFalse(trim.is_due(1000, 30, 1000 + 29 * DAY))
        self.assertTrue(trim.is_due(1000, 30, 1000 + 30 * DAY))


class DiscardTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        os.makedirs(os.path.join(self.dir.name, "sdb", "queue"))

    def write(self, value):
        with open(os.path.join(self.dir.name, "sdb", "queue", "discard_max_bytes"), "w") as fh:
            fh.write(value)

    def test_reads_kernel_value(self):
        self.write("2147450880\n")
        self.assertTrue(trim.discard_supported("sdb", self.dir.name))
        self.write("0\n")
        self.assertFalse(trim.discard_supported("sdb", self.dir.name))

    def test_missing_disk(self):
        self.assertFalse(trim.discard_supported("sdz", self.dir.name))


class TrimmerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "trim.json")

    def test_result_is_stored(self):
        trimmer = trim.Trimmer(self.path, run=lambda mp: {"status": "ok", "bytes": 5, "message": ""}, clock=lambda: 77)
        trimmer.execute("/mnt/a")
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["/mnt/a"]["time"], 77)
        self.assertEqual(trimmer.last_run("/mnt/a"), 77)
        self.assertEqual(trimmer.status()["running"], [])

    def test_only_one_run_per_drive(self):
        entered, leave = threading.Event(), threading.Event()

        def run(mountpoint):
            entered.set()
            leave.wait(5)
            return {"status": "ok", "bytes": 0, "message": ""}

        trimmer = trim.Trimmer(self.path, run=run)
        self.assertTrue(trimmer.start("/mnt/a"))
        self.assertTrue(entered.wait(5))
        self.assertFalse(trimmer.start("/mnt/a"))
        self.assertIsNone(trimmer.execute("/mnt/a"))
        self.assertEqual(trimmer.status()["running"], ["/mnt/a"])
        leave.set()
        for thread in threading.enumerate():
            if thread.name == "fstrim":
                thread.join(5)

    def test_crash_in_fstrim_is_recorded(self):
        def run(mountpoint):
            raise RuntimeError("kaputt")

        trimmer = trim.Trimmer(self.path, run=run)
        with self.assertRaises(RuntimeError):
            trimmer.execute("/mnt/a")
        self.assertEqual(trimmer.status()["results"]["/mnt/a"]["status"], "error")
        self.assertEqual(trimmer.status()["running"], [])


def fake_disk(root, name="sda", usb=True, mode="full", lbpu=True, max_unmap=4194240, discard=0,
              vpd=True, driver=None):
    """A minimal /sys/block/<name> as the kernel shows a USB bridge.

    vpd=False leaves out the VPD pages, as usb-storage does; driver adds a
    driver link on the USB interface.
    """
    bus = "usb2/2-1/2-1:1.0" if usb else "ata1"
    real = os.path.join(root, "devices", "pci0000:00", bus, "host1", "target1:0:0", "1:0:0:0", "block", name)
    device = os.path.join(os.path.dirname(os.path.dirname(real)))
    os.makedirs(os.path.join(real, "queue"))
    os.makedirs(os.path.join(device, "scsi_disk", "1:0:0:0"))
    os.symlink(device, os.path.join(real, "device"))
    os.makedirs(os.path.join(root, "block"), exist_ok=True)
    os.symlink(real, os.path.join(root, "block", name))
    with open(os.path.join(device, "scsi_disk", "1:0:0:0", "provisioning_mode"), "w") as fh:
        fh.write(mode + "\n")
    with open(os.path.join(real, "queue", "discard_max_bytes"), "w") as fh:
        fh.write(f"{discard}\n")
    if driver:
        interface = os.path.join(root, "devices", "pci0000:00", bus)
        os.makedirs(os.path.join(root, "bus", driver), exist_ok=True)
        os.symlink(os.path.join(root, "bus", driver), os.path.join(interface, "driver"))
    if not vpd:
        return os.path.join(device, "scsi_disk", "1:0:0:0", "provisioning_mode")
    with open(os.path.join(device, "vpd_pgb2"), "wb") as fh:
        fh.write(bytes([0, 0xB2, 0, 4, 0, 0x80 if lbpu else 0, 0, 0]))
    pgb0 = bytearray(64)
    pgb0[1] = 0xB0
    pgb0[20:24] = max_unmap.to_bytes(4, "big")
    with open(os.path.join(device, "vpd_pgb0"), "wb") as fh:
        fh.write(bytes(pgb0))
    return os.path.join(device, "scsi_disk", "1:0:0:0", "provisioning_mode")


class UnmapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.block = os.path.join(self.root, "block")
        self.rw = os.path.join(self.root, "rw")
        self.mounts = []

    def tearDown(self):
        self.tmp.cleanup()

    def mount(self, target):
        # the private read-write sysfs shows the same tree
        os.makedirs(target, exist_ok=True)
        os.symlink(self.block, os.path.join(target, "block"))
        self.mounts.append(target)

    def umount(self, target):
        os.unlink(os.path.join(target, "block"))
        self.mounts.remove(target)

    def test_reads_what_an_rtl9210_reports(self):
        fake_disk(self.root)
        info = trim.unmap_info("sda", self.block)
        self.assertEqual(info, {"usb": True, "driver": "", "mode": "full", "lbpu": True,
                                "max_unmap": 4194240, "vpd": "sysfs"})
        self.assertTrue(trim.unmap_possible("sda", self.block))

    def test_finds_the_usb_driver(self):
        fake_disk(self.root, driver="usb-storage")
        self.assertEqual(trim.unmap_info("sda", self.block)["driver"], "usb-storage")

    def test_usb_storage_without_vpd_asks_the_drive(self):
        fake_disk(self.root, vpd=False, driver="usb-storage")
        asked = []

        def ask(disk, page):
            asked.append((disk, page))
            if page == 0xB2:
                return bytes([0, 0xB2, 0, 4, 0, 0x80, 0, 0])
            return b""   # no Block Limits page: the kernel uses its own maximum
        info = trim.unmap_info("sda", self.block, ask)
        self.assertEqual(asked, [("sda", 0xB2), ("sda", 0xB0)])
        self.assertEqual((info["lbpu"], info["max_unmap"], info["vpd"]), (True, 0, "device"))
        self.assertTrue(trim.unmap_possible("sda", self.block, ask))

    def test_without_vpd_and_without_asking_support_is_unknown(self):
        fake_disk(self.root, vpd=False, driver="usb-storage")
        info = trim.unmap_info("sda", self.block)
        self.assertEqual(info["vpd"], "none")
        self.assertFalse(trim.unmap_possible("sda", self.block))
        self.assertIn("answers no VPD pages", trim.describe("sda", info))
        self.assertIn("driver usb-storage", trim.describe("sda", info))

    def test_drive_without_unmap_support_stays_off(self):
        fake_disk(self.root, vpd=False)
        ask = lambda disk, page: bytes([0, 0xB2, 0, 4, 0, 0, 0, 0]) if page == 0xB2 else b""
        self.assertFalse(trim.unmap_possible("sda", self.block, ask))

    def test_sysfs_vpd_wins_over_asking(self):
        fake_disk(self.root)
        info = trim.unmap_info("sda", self.block, lambda disk, page: self.fail("must not ask"))
        self.assertEqual(info["vpd"], "sysfs")

    def test_scsi_vpd_without_device_is_empty(self):
        self.assertEqual(trim.scsi_vpd("sdz", 0xB2, dev=self.root), b"")

    def test_not_possible_without_lbpu_or_usb_or_when_already_on(self):
        for kwargs in ({"lbpu": False}, {"usb": False}, {"mode": "unmap"}, {"max_unmap": 0}):
            with self.subTest(**kwargs):
                root = tempfile.mkdtemp(dir=self.root)
                fake_disk(root, **kwargs)
                self.assertFalse(trim.unmap_possible("sda", os.path.join(root, "block")))

    def test_missing_files_mean_not_possible(self):
        os.makedirs(os.path.join(self.block, "sdz"))
        self.assertFalse(trim.unmap_possible("sdz", self.block))

    def test_enable_writes_unmap_and_unmounts(self):
        mode = fake_disk(self.root)
        msg = trim.enable_unmap("sda", self.block, self.rw, mount=self.mount, umount=self.umount,
                                ask_device=None)
        with open(mode) as fh:
            self.assertEqual(fh.read(), "unmap")
        self.assertEqual(self.mounts, [])
        self.assertIn("still reports no discard", msg)   # the fake kernel does not react

    def test_enable_reports_success_when_kernel_accepts(self):
        fake_disk(self.root)
        discard = os.path.join(self.block, "sda", "queue", "discard_max_bytes")

        def umount(target):
            self.umount(target)
            with open(discard, "w") as fh:
                fh.write("2147450880\n")
        msg = trim.enable_unmap("sda", self.block, self.rw, mount=self.mount, umount=umount)
        self.assertEqual(msg, "TRIM enabled on sda (provisioning_mode unmap)")

    def test_enable_on_usb_storage_after_asking_the_drive(self):
        mode = fake_disk(self.root, vpd=False, driver="usb-storage")
        ask = lambda disk, page: bytes([0, 0xB2, 0, 4, 0, 0x80, 0, 0]) if page == 0xB2 else b""
        trim.enable_unmap("sda", self.block, self.rw, mount=self.mount, umount=self.umount, ask_device=ask)
        with open(mode) as fh:
            self.assertEqual(fh.read(), "unmap")

    def test_enable_explains_why_it_did_nothing(self):
        mode = fake_disk(self.root, lbpu=False)
        msg = trim.enable_unmap("sda", self.block, self.rw, mount=self.mount, umount=self.umount,
                                ask_device=None)
        self.assertIn("does not report UNMAP support", msg)
        with open(mode) as fh:
            self.assertEqual(fh.read().strip(), "full")
        self.assertEqual(self.mounts, [])

    def test_enable_is_silent_when_trim_already_works(self):
        fake_disk(self.root, discard=2147450880)
        self.assertIsNone(trim.enable_unmap("sda", self.block, self.rw, mount=self.mount,
                                            umount=self.umount, ask_device=None))

    def test_enable_leaves_other_disks_alone(self):
        mode = fake_disk(self.root, usb=False)
        self.assertIsNone(trim.enable_unmap("sda", self.block, self.rw, mount=self.mount, umount=self.umount))
        with open(mode) as fh:
            self.assertEqual(fh.read().strip(), "full")
        self.assertEqual(self.mounts, [])

    def test_enable_reports_mount_failure(self):
        fake_disk(self.root)

        def mount(target):
            raise subprocess.CalledProcessError(32, "mount")
        msg = trim.enable_unmap("sda", self.block, self.rw, mount=mount, umount=self.umount)
        self.assertTrue(msg.startswith("cannot enable TRIM on sda"))

    def test_prepare_hook_adds_fields_to_result(self):
        path = os.path.join(self.root, "results.json")
        trimmer = trim.Trimmer(path, run=lambda m: {"status": "unsupported", "bytes": None, "message": "x"},
                               prepare=lambda m: {"unmap_possible": True})
        result = trimmer.execute("/mnt/a")
        self.assertTrue(result["unmap_possible"])
        self.assertEqual(result["status"], "unsupported")


if __name__ == "__main__":
    unittest.main()
