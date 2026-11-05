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


if __name__ == "__main__":
    unittest.main()
