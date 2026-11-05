#!/usr/bin/env python3
"""hd-idle log filter: timestamps, durations and totals that survive restarts."""
import io
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import hd_idle_log  # noqa: E402

START = 1_800_000_000


class FakeClock:
    def __init__(self, *times):
        self.times = list(times)

    def __call__(self):
        return self.times.pop(0)


class DurationTest(unittest.TestCase):
    def test_two_largest_units(self):
        cases = {0: "0s", 45: "45s", 725: "12m 5s", 11220: "3h 7m", 187200: "2d 4h"}
        for seconds, text in cases.items():
            self.assertEqual(hd_idle_log.fmt_duration(seconds), text)


class TrackerTest(unittest.TestCase):
    def test_first_spindown_counts_from_start(self):
        stats = {}
        tracker = hd_idle_log.Tracker(stats, START)
        message = tracker.event("sda", "spindown", START + 1800)
        self.assertIn("after running for 30m 0s", message)
        self.assertEqual(stats["sda"], {"running_seconds": 1800, "spindowns": 1})

    def test_full_cycle_adds_both_periods(self):
        stats = {}
        tracker = hd_idle_log.Tracker(stats, START)
        tracker.event("sda", "spindown", START + 600)
        message = tracker.event("sda", "spinup", START + 4200)
        self.assertIn("after standby for 1h 0m", message)
        self.assertEqual(stats["sda"]["standby_seconds"], 3600)
        self.assertEqual(stats["sda"]["spinups"], 1)
        tracker.event("sda", "spindown", START + 4500)
        self.assertEqual(stats["sda"]["running_seconds"], 900)
        self.assertEqual(stats["sda"]["spindowns"], 2)

    def test_disks_are_independent(self):
        stats = {}
        tracker = hd_idle_log.Tracker(stats, START)
        tracker.event("sda", "spindown", START + 60)
        tracker.event("sdb", "spindown", START + 120)
        self.assertEqual(stats["sda"]["running_seconds"], 60)
        self.assertEqual(stats["sdb"]["running_seconds"], 120)

    def test_repeated_event_is_not_counted_twice(self):
        stats = {}
        tracker = hd_idle_log.Tracker(stats, START)
        tracker.event("sda", "spindown", START + 60)
        self.assertEqual(tracker.event("sda", "spindown", START + 120), "sda spindown")
        self.assertEqual(stats["sda"], {"running_seconds": 60, "spindowns": 1})

    def test_clock_going_backwards_adds_nothing(self):
        stats = {}
        hd_idle_log.Tracker(stats, START).event("sda", "spindown", START - 100)
        self.assertEqual(stats["sda"]["running_seconds"], 0)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "hdd_stats.json")

    def run_filter(self, lines, *times):
        out = io.StringIO()
        hd_idle_log.run(lines, out=out, clock=FakeClock(*times), path=self.path)
        return out.getvalue().splitlines()

    def test_lines_get_timestamps_and_other_output_passes(self):
        lines = self.run_filter(["sda spindown\n", "\n", "something else\n"], START, START + 60, START + 61)
        expected = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(START + 60))
        self.assertTrue(lines[0].startswith(f"{expected} sda spindown after running for 1m 0s"))
        self.assertTrue(lines[1].endswith(" something else"))
        self.assertEqual(len(lines), 2)

    def test_totals_survive_restart(self):
        self.run_filter(["sda spindown\n"], START, START + 60)
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["sda"]["spindowns"], 1)
        lines = self.run_filter(["sda spindown\n"], START + 100, START + 160)
        self.assertEqual(lines[0], "hd-idle totals so far: sda: running 1m 0s, standby 0s, 1 spindowns")
        self.assertIn("(total: running 2m 0s, standby 0s, 2 spindowns)", lines[1])

    def test_damaged_stats_file_starts_empty(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("kaputt")
        self.assertEqual(hd_idle_log.load_stats(self.path), {})

    def test_save_failure_does_not_stop_the_filter(self):
        missing = os.path.join(self.dir.name, "fehlt", "stats.json")
        out = io.StringIO()
        stderr, sys.stderr = sys.stderr, io.StringIO()
        try:
            hd_idle_log.run(["sda spindown\n"], out=out, clock=FakeClock(START, START + 1), path=missing)
        finally:
            sys.stderr = stderr
        self.assertIn("sda spindown", out.getvalue())


if __name__ == "__main__":
    unittest.main()
