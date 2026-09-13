#!/usr/bin/env python3
"""What the admin can see about lockouts, failure counters and clamd.

The bug these cover: five wrong passwords produced no visible reaction at
all, so there was no way to tell a working lockout from a broken one.
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import app as nas            # noqa: E402
import sharing_store as ss   # noqa: E402
from ratelimit import AUTHFAIL_IP, LIMITER, Limiter  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class SnapshotTest(unittest.TestCase):
    def test_counters_and_locks_are_reported(self):
        clock = Clock()
        lim = Limiter(clock=clock)
        for _ in range(5):
            lim.record("authfail:ip:1.2.3.4")
        lim.record("authfail:user:admin")
        snap = lim.snapshot(15 * 60)
        self.assertEqual(snap["locks"], [])
        counts = {(c["kind"], c["target"]): c["count"] for c in snap["counters"]}
        self.assertEqual(counts[("ip", "1.2.3.4")], 5)
        self.assertEqual(counts[("user", "admin")], 1)
        # busiest first, so the admin card shows the interesting one
        self.assertEqual(snap["counters"][0]["target"], "1.2.3.4")

    def test_lock_shows_up_with_remaining_time_and_disappears(self):
        clock = Clock()
        lim = Limiter(clock=clock)
        lim.lock("authfail:ip:9.9.9.9", 15 * 60, 24 * 3600)
        snap = lim.snapshot()
        self.assertEqual(len(snap["locks"]), 1)
        lock = snap["locks"][0]
        self.assertEqual((lock["kind"], lock["target"]), ("ip", "9.9.9.9"))
        self.assertEqual(lock["strikes"], 1)
        self.assertTrue(890 < lock["remaining"] <= 900)
        clock.t += 901
        self.assertEqual(lim.snapshot()["locks"], [])

    def test_ban_key_keeps_its_shape(self):
        lim = Limiter(clock=Clock())
        lim.lock("ban:5.5.5.5", 60, 3600)
        lock = lim.snapshot()["locks"][0]
        self.assertEqual((lock["kind"], lock["target"]), ("ban", "5.5.5.5"))

    def test_only_authfail_buckets_are_counted(self):
        lim = Limiter(clock=Clock())
        lim.record("ip:1.1.1.1")          # plain request counter, not a failure
        lim.record("scan:1.1.1.1")
        self.assertEqual(lim.snapshot()["counters"], [])

    def test_old_failures_fall_out_of_the_window(self):
        clock = Clock()
        lim = Limiter(clock=clock)
        lim.record("authfail:ip:2.2.2.2")
        clock.t += 15 * 60 + 1
        self.assertEqual(lim.snapshot(15 * 60)["counters"], [])


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.media = os.path.join(self.tmp, "media")
        os.makedirs(self.media)
        self._saved = (nas._OPTIONS, nas.DATA_DIR, nas.SHARES_FILE, nas.FILE_ACCESS_FILE,
                       dict(nas._CLAMD_PROBE))
        nas._OPTIONS = {"file_allowed_roots": [self.media], "share_allowed_roots": [self.media],
                        "share_clamav_enabled": False}
        nas.DATA_DIR = self.tmp
        nas.SHARES_FILE = os.path.join(self.tmp, "shares.json")
        nas.FILE_ACCESS_FILE = os.path.join(self.tmp, "file_access.json")
        ss.init(self.tmp, nas.load_json, nas.save_json)
        nas.save_json(nas.SHARES_FILE, [])
        nas.app.config["TESTING"] = True
        nas.app.secret_key = "test-key"
        self.c = nas.app.test_client()
        self.c.get("/api/roots")
        with self.c.session_transaction() as sess:
            self.h = {"X-CSRF-Token": sess["csrf"]}
        LIMITER.clear(prefix="authfail:")

    def tearDown(self):
        LIMITER.clear(prefix="authfail:")
        (nas._OPTIONS, nas.DATA_DIR, nas.SHARES_FILE, nas.FILE_ACCESS_FILE, probe) = self._saved
        nas._CLAMD_PROBE.update(probe)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_status_exposes_threshold_and_counters(self):
        st = self.c.get("/api/sharing/status").get_json()
        self.assertEqual(st["authfail_limit"], AUTHFAIL_IP[0])
        self.assertEqual(st["authfail_window_min"], AUTHFAIL_IP[1] // 60)
        self.assertEqual(st["locks"], [])
        for _ in range(5):
            LIMITER.record("authfail:ip:7.7.7.7")
        st = self.c.get("/api/sharing/status").get_json()
        hit = [c for c in st["authfail"] if c["target"] == "7.7.7.7"]
        self.assertEqual(hit[0]["count"], 5)
        # five failures are below the threshold - that is exactly why nothing
        # appeared to happen, and now the number is visible
        self.assertLess(hit[0]["count"], st["authfail_limit"])
        self.assertEqual(st["locks"], [])

    def test_unlock_clears_a_lock(self):
        LIMITER.lock("authfail:ip:8.8.8.8", 900, 3600)
        st = self.c.get("/api/sharing/status").get_json()
        self.assertEqual([l["target"] for l in st["locks"]], ["8.8.8.8"])
        r = self.c.post("/api/sharing/locks/unlock", json={"key": "authfail:ip:8.8.8.8"}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/api/sharing/status").get_json()["locks"], [])

    def test_unlock_refuses_foreign_keys(self):
        r = self.c.post("/api/sharing/locks/unlock", json={"key": "shares"}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/sharing/locks/unlock", json={"key": ""}, headers=self.h)
        self.assertEqual(r.status_code, 400)

    def test_unlock_needs_csrf(self):
        r = self.c.post("/api/sharing/locks/unlock", json={"key": "ban:1.2.3.4"})
        self.assertEqual(r.status_code, 403)

    def test_clamav_probe_is_reported_and_cached(self):
        calls = []
        real = nas.clamav.ping
        nas.clamav.ping = lambda h, p, timeout=5: (calls.append((h, p)), False)[1]
        try:
            nas._OPTIONS["share_clamav_enabled"] = True
            nas._OPTIONS["share_clamav_host"] = "127.0.0.1"
            nas._OPTIONS["share_clamav_port"] = 3310
            nas._CLAMD_PROBE.update(ts=0.0, ok=None, message="")
            st = self.c.get("/api/sharing/status").get_json()
            self.assertFalse(st["clamav_reachable"])
            self.assertIn("3310", st["clamav_message"])
            self.c.get("/api/sharing/status")          # cached, no second probe
            self.assertEqual(len(calls), 1)
        finally:
            nas.clamav.ping = real

    def test_no_probe_when_scanning_is_off(self):
        real = nas.clamav.ping
        nas.clamav.ping = lambda *a, **k: self.fail("must not probe when disabled")
        try:
            nas._OPTIONS["share_clamav_enabled"] = False
            nas._CLAMD_PROBE.update(ts=0.0, ok=None, message="")
            st = self.c.get("/api/sharing/status").get_json()
            self.assertIsNone(st["clamav_reachable"])
        finally:
            nas.clamav.ping = real


if __name__ == "__main__":
    unittest.main()
