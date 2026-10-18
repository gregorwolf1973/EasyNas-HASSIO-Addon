#!/usr/bin/env python3
"""Network recycle bin: Samba config, automatic purge and the Settings API."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(HERE, "..", "app")
sys.path.insert(0, APP_DIR)

import app as nas  # noqa: E402

DAY = 86400


def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class PurgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.share = os.path.join(self.tmp, "share")
        self.bin = os.path.join(self.share, ".recycle")
        write(os.path.join(self.share, "bleibt.txt"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_keeps_recent_files_and_removes_old_ones_with_their_folders(self):
        write(os.path.join(self.bin, "Urlaub", "2026", "a.jpg"), b"1234")
        write(os.path.join(self.bin, "b.txt"), b"12")
        self.assertEqual(nas.recycle_purge(self.share, 30), (0, 0))
        self.assertEqual(nas.recycle_usage(self.share), {"files": 2, "bytes": 6, "truncated": False})

        self.assertEqual(nas.recycle_purge(self.share, 30, now=time.time() + 31 * DAY), (2, 6))
        self.assertEqual(os.listdir(self.bin), [], "leere Ordner sind weg, der Papierkorb selbst bleibt")
        self.assertTrue(os.path.exists(os.path.join(self.share, "bleibt.txt")), "nur im Papierkorb wird geloescht")

    def test_older_content_from_an_earlier_samba_setup_stays(self):
        write(os.path.join(self.bin, "gregor", "Ahnen", "alt.jpg"))
        since = time.time() + 10                            # Papierkorb erst danach eingeschaltet
        later = since + 31 * DAY
        self.assertEqual(nas.recycle_purge(self.share, 30, now=later, since=since), (0, 0))
        self.assertEqual(nas.recycle_purge(self.share, 30, now=later, since=since - 60), (1, 1))

    def test_empty_now_removes_everything(self):
        write(os.path.join(self.bin, "x", "y.txt"))
        self.assertEqual(nas.recycle_purge(self.share), (1, 1))
        self.assertEqual(nas.recycle_usage(self.share)["files"], 0)

    def test_no_bin_is_fine(self):
        self.assertEqual(nas.recycle_purge(self.share), (0, 0))
        self.assertEqual(nas.recycle_usage("/gibt/es/nicht"), {"files": 0, "bytes": 0, "truncated": False})

    def test_links_are_never_followed(self):
        outside = os.path.join(self.tmp, "woanders")
        write(os.path.join(outside, "wichtig.txt"))
        os.symlink(outside, self.bin)                       # der Papierkorb selbst ist ein Link
        self.assertEqual(nas.recycle_purge(self.share), (0, 0))
        os.unlink(self.bin)
        os.makedirs(self.bin)
        os.symlink(outside, os.path.join(self.bin, "link"))  # ein Link im Papierkorb
        self.assertEqual(nas.recycle_purge(self.share)[0], 1)
        self.assertFalse(os.path.lexists(os.path.join(self.bin, "link")))
        self.assertTrue(os.path.exists(os.path.join(outside, "wichtig.txt")))


class SettingsApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.share = os.path.join(self.tmp, "share")
        write(os.path.join(self.share, ".recycle", "alt.txt"), b"abc")
        self._saved = (nas._OPTIONS, nas.SHARES_FILE, nas.RECYCLE_FILE, nas.reload_samba)
        nas._OPTIONS = {}
        nas.SHARES_FILE = os.path.join(self.tmp, "shares.json")
        nas.RECYCLE_FILE = os.path.join(self.tmp, "recycle.json")
        nas.save_json(nas.SHARES_FILE, [{"name": "fotos", "path": self.share}])
        self.reloads = []
        nas.reload_samba = lambda: self.reloads.append(1)   # kein smbd auf der Testmaschine
        nas.app.config["TESTING"] = True
        nas.app.secret_key = "test-key"
        self.c = nas.app.test_client()
        self.c.get("/api/settings/recycle")
        with self.c.session_transaction() as sess:
            self.csrf = sess.get("csrf", "")

    def tearDown(self):
        nas._OPTIONS, nas.SHARES_FILE, nas.RECYCLE_FILE, nas.reload_samba = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, path, body):
        return self.c.post(path, json=body, headers={"X-CSRF-Token": self.csrf})

    def test_defaults_on_with_30_days_and_usage_per_share(self):
        body = self.c.get("/api/settings/recycle").get_json()
        self.assertEqual((body["enabled"], body["days"], body["dir"]), (True, 30, ".recycle"))
        self.assertEqual(body["shares"][0]["name"], "fotos")
        self.assertEqual((body["shares"][0]["files"], body["shares"][0]["bytes"]), (1, 3))

    def test_save_reloads_samba_and_clamps_days(self):
        r = self.post("/api/settings/recycle", {"enabled": False, "days": 999})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.get_json()["enabled"], r.get_json()["days"]), (False, 365))
        saved = json.load(open(nas.RECYCLE_FILE))
        self.assertEqual((saved["enabled"], saved["days"]), (False, 365))
        self.assertAlmostEqual(saved["since"], time.time(), delta=60)
        self.assertEqual(self.reloads, [1])
        self.assertEqual(self.post("/api/settings/recycle", {"days": "viele"}).status_code, 400)

    def test_empty_one_share(self):
        r = self.post("/api/settings/recycle/empty", {"name": "fotos"})
        self.assertEqual(r.get_json(), {"ok": True, "files": 1, "bytes": 3})
        self.assertEqual(self.post("/api/settings/recycle/empty", {"name": "gibtsnicht"}).status_code, 404)

    def test_needs_csrf(self):
        r = self.c.post("/api/settings/recycle/empty", json={"name": "fotos"})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(os.path.exists(os.path.join(self.share, ".recycle", "alt.txt")))


class SmbConfTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.data)
        shares = [{"name": "rw", "path": os.path.join(self.tmp, "rw")},
                  {"name": "ro", "path": os.path.join(self.tmp, "ro"), "writable": False}]
        for share in shares:
            os.makedirs(share["path"])
        with open(os.path.join(self.data, "shares.json"), "w") as f:
            json.dump(shares, f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sections(self):
        conf_path = os.path.join(self.tmp, "smb.conf")
        env = dict(os.environ, SIMPLE_NAS_DATA=self.data, SIMPLE_NAS_SMB_CONF=conf_path)
        subprocess.run([sys.executable, os.path.join(APP_DIR, "generate_smb_conf.py"), "WG", "NAS", "445"],
                       env=env, check=True, capture_output=True)
        with open(conf_path) as f:
            parts = f.read().split("\n[")
        return {p.split("]", 1)[0].lstrip("["): p for p in parts}

    def test_writable_shares_get_the_bin_with_the_mac_modules(self):
        s = self.sections()
        self.assertIn("vfs objects = catia fruit streams_xattr recycle", s["rw"])
        self.assertIn("recycle:repository = .recycle", s["rw"])
        self.assertIn("recycle:exclude_dir = .recycle", s["rw"])
        self.assertNotIn("recycle", s["ro"])
        self.assertNotIn("recycle", s["global"])

    def test_switched_off_in_settings(self):
        with open(os.path.join(self.data, "recycle.json"), "w") as f:
            json.dump({"enabled": False, "days": 30}, f)
        self.assertNotIn("recycle", self.sections()["rw"])


if __name__ == "__main__":
    unittest.main()
