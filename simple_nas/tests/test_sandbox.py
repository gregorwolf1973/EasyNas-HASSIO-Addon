#!/usr/bin/env python3
"""The privilege-separated share worker (step 9): the file-based IPC between the
admin process and the sandboxed worker, and the in-process fallback still serving."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

# Other test modules patch time.sleep to a no-op (share_web.time.sleep) and never
# restore it, which would make the polling loops below spin instantly. Capture the
# real sleep at import, before any test runs.
_SLEEP = time.sleep

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import app as nas            # noqa: E402
import ratelimit            # noqa: E402
import share_worker         # noqa: E402
import sharesandbox as sb   # noqa: E402
import sharing_store as ss  # noqa: E402


class IpcTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_snapshot_roundtrip_and_staleness(self):
        self.assertIsNone(sb.read_snapshot(self.tmp))
        sb.write_snapshot(self.tmp, {"locks": [{"kind": "ip", "target": "1.2.3.4", "remaining": 500}],
                                     "counters": [], "link_locked": ["abc"]})
        snap = sb.read_snapshot(self.tmp)
        self.assertEqual(snap["link_locked"], ["abc"])
        self.assertEqual(snap["locks"][0]["target"], "1.2.3.4")
        self.assertIn("ts", snap)
        # a stale file reads as None so the card never shows old locks as live
        old = json.load(open(os.path.join(self.tmp, sb.SNAPSHOT_FILE)))
        old["ts"] = time.time() - 999
        json.dump(old, open(os.path.join(self.tmp, sb.SNAPSHOT_FILE), "w"))
        self.assertIsNone(sb.read_snapshot(self.tmp, max_age=30))
        self.assertIsNotNone(sb.read_snapshot(self.tmp, max_age=0))     # 0 = never stale

    def test_unlock_queue_drain(self):
        self.assertEqual(sb.drain_unlocks(self.tmp), [])
        sb.queue_unlock(self.tmp, "authfail:ip:9.9.9.9")
        sb.queue_unlock(self.tmp, "ban:8.8.8.8")
        sb.queue_unlock(self.tmp, "authfail:ip:9.9.9.9")          # deduped
        self.assertEqual(sorted(sb.drain_unlocks(self.tmp)), ["authfail:ip:9.9.9.9", "ban:8.8.8.8"])
        self.assertEqual(sb.drain_unlocks(self.tmp), [])          # emptied

    def test_curated_options_have_no_secret(self):
        store = {"share_bind": "0.0.0.0", "share_port": 8097, "share_cookie_secure": True,
                 "admin_password": "SECRET", "admin_username": "admin", "workgroup": "WG"}
        opts = sb.curate_options(lambda k, d=None: store.get(k, d), {"share_port": 8097})
        self.assertNotIn("admin_password", opts)
        self.assertNotIn("admin_username", opts)
        self.assertNotIn("workgroup", opts)
        self.assertEqual(opts["share_cookie_secure"], True)
        self.assertEqual(opts["share_port"], 8097)
        sb.write_options(self.tmp, opts)
        self.assertNotIn("SECRET", open(os.path.join(self.tmp, sb.OPTS_FILE)).read())
        self.assertEqual(sb.read_options(self.tmp)["share_bind"], "0.0.0.0")

    def test_jail_masks_the_secret_data_files(self):
        # the files an RCE in the worker must NOT be able to read
        self.assertIn("admin_auth.json", sb.JAIL_MASK_FILES)
        self.assertIn("options.json", sb.JAIL_MASK_FILES)
        self.assertIn("samba", sb.JAIL_MASK_DIRS)
        for hidden in ("/config", "/ssl", "/addon_configs", "/backup"):
            self.assertIn(hidden, sb.JAIL_HIDE_TREES)
        # the writable state files the worker seeds are not among the masks
        for needed in ("share_links.json", "share_accounts.json", sb.LOG_FILE, sb.SNAPSHOT_FILE):
            self.assertIn(needed, sb.JAIL_SEED_FILES)
            self.assertNotIn(needed, sb.JAIL_MASK_FILES)


class WorkerSnapshotLoopTest(unittest.TestCase):
    """The worker publishes limiter state and applies unlocks."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._lim = ratelimit.LIMITER
        ratelimit.LIMITER = share_worker.LIMITER = ratelimit.Limiter()
        ss.init(self.tmp, share_worker._load_json, share_worker._save_json)
        share_worker._save_json(os.path.join(self.tmp, "shares.json"), [])
        media = os.path.join(self.tmp, "media"); os.makedirs(media)
        self.link = ss.create_link({"name": "x", "root": media, "access": "password",
                                    "password": "geheim12"}, allowed_roots=[media])

    def tearDown(self):
        ratelimit.LIMITER = share_worker.LIMITER = self._lim
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_loop_writes_snapshot_with_link_lock_and_drains(self):
        lim = share_worker.LIMITER
        lim.lock(f"authfail:link:{self.link['id']}", 900, 3600)
        lim.record("authfail:ip:5.5.5.5")
        stop = threading.Event()
        t = threading.Thread(target=share_worker._snapshot_loop,
                             args=(self.tmp, lambda k, d=None: d, stop), daemon=True)
        t.start()
        try:
            snap = None
            for _ in range(120):
                snap = sb.read_snapshot(self.tmp)
                if snap and snap.get("link_locked"):
                    break
                _SLEEP(0.05)
            self.assertIsNotNone(snap, "worker never wrote a snapshot")
            self.assertIn(self.link["id"], snap["link_locked"])
            # an admin unlock request is drained and clears the lock
            sb.queue_unlock(self.tmp, f"authfail:link:{self.link['id']}")
            cleared = False
            for _ in range(80):
                if not lim.banned(f"authfail:link:{self.link['id']}"):
                    cleared = True
                    break
                _SLEEP(0.05)
            self.assertTrue(cleared)
        finally:
            stop.set()


class AdminHelperTest(unittest.TestCase):
    """The admin endpoints must read the right source in each mode."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (nas.DATA_DIR, dict(nas._SHARE), dict(nas._SNAP_CACHE))
        nas.DATA_DIR = self.tmp
        nas._SHARE.update(sandboxed=False, proc=None)
        nas._SNAP_CACHE.update(ts=0.0, snap=None)
        self._lim = ratelimit.LIMITER
        nas.LIMITER = ratelimit.LIMITER = ratelimit.Limiter()

    def tearDown(self):
        nas.DATA_DIR, share, cache = self._saved
        nas._SHARE.update(share); nas._SNAP_CACHE.update(cache)
        nas.LIMITER = ratelimit.LIMITER = self._lim

    def test_not_sandboxed_uses_limiter(self):
        self.assertFalse(nas._share_sandboxed())
        nas.LIMITER.lock("authfail:link:L1", 900, 3600)
        self.assertTrue(nas._link_locked("L1"))
        self.assertFalse(nas._link_locked("L2"))
        snap = nas._lock_snapshot()
        self.assertIsNone(snap["link_locked"])          # signal: ask limiter per link
        # unlock goes straight to the limiter
        nas._do_unlock("authfail:link:L1")
        self.assertFalse(nas._link_locked("L1"))

    def test_sandboxed_reads_snapshot_file_and_queues_unlock(self):
        class FakeProc:
            def poll(self): return None
        nas._SHARE.update(sandboxed=True, proc=FakeProc())
        self.assertTrue(nas._share_sandboxed())
        self.assertTrue(nas._share_running())
        sb.write_snapshot(self.tmp, {"locks": [], "counters": [], "link_locked": ["L9"]})
        nas._SNAP_CACHE.update(ts=0.0, snap=None)
        self.assertTrue(nas._link_locked("L9"))
        self.assertFalse(nas._link_locked("L8"))
        # in sandbox mode the limiter here is untouched; the request is queued
        nas._do_unlock("authfail:ip:1.1.1.1")
        self.assertEqual(sb.drain_unlocks(self.tmp), ["authfail:ip:1.1.1.1"])

    def test_dead_proc_is_not_running(self):
        class DeadProc:
            returncode = 1
            def poll(self): return 1
        nas._SHARE.update(sandboxed=True, proc=DeadProc())
        self.assertFalse(nas._share_sandboxed())
        self.assertFalse(nas._share_running())


class FallbackServeTest(unittest.TestCase):
    """serve_share (used by the in-process fallback) really serves the app."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "media"))
        ss.init(self.tmp, share_worker._load_json, share_worker._save_json)
        self._lim = ratelimit.LIMITER
        ratelimit.LIMITER = ratelimit.Limiter()
        import share_web
        share_web.LIMITER = ratelimit.LIMITER

    def tearDown(self):
        ratelimit.LIMITER = self._lim
        import share_web
        share_web.RUNNING = False          # don't leak "running" into later tests
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_healthz(self):
        import socket
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        opts = {"share_bind": "127.0.0.1", "share_port": port, "share_max_upload_mb": 16}
        srv = share_worker.serve_share(lambda k, d=None: opts.get(k, d), lambda: [],
                                       lambda: [os.path.join(self.tmp, "media")], self.tmp,
                                       blocking=False, sandboxed=False)
        t = threading.Thread(target=srv.run, daemon=True); t.start()
        try:
            for _ in range(50):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as r:
                        self.assertEqual(r.status, 200)
                        self.assertEqual(r.read(), b"ok")
                        break
                except Exception:
                    _SLEEP(0.1)
            else:
                self.fail("healthz never answered")
        finally:
            srv.close()


if __name__ == "__main__":
    unittest.main()
