#!/usr/bin/env python3
"""Sharing store and the admin-side /api/sharing/* endpoints."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import app as nas  # noqa: E402
import sharing_store as ss  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.media = os.path.join(self.tmp, "media")
        self.fotos = os.path.join(self.media, "fotos")
        os.makedirs(self.fotos)
        with open(os.path.join(self.fotos, "a.jpg"), "w") as f:
            f.write("x")
        self.outside = os.path.join(self.tmp, "config")
        os.makedirs(self.outside)

        self._saved = (nas._OPTIONS, nas.DATA_DIR, nas.SHARES_FILE, nas.FILE_ACCESS_FILE, ss._data_dir)
        nas._OPTIONS = {"file_allowed_roots": [self.media], "share_allowed_roots": [self.media],
                        "share_port": 8101}
        nas.DATA_DIR = self.tmp
        nas.SHARES_FILE = os.path.join(self.tmp, "shares.json")
        nas.FILE_ACCESS_FILE = os.path.join(self.tmp, "file_access.json")
        ss.init(self.tmp, nas.load_json, nas.save_json)
        nas.save_json(nas.SHARES_FILE, [{"name": "Fotos", "path": self.media}])
        nas.app.config["TESTING"] = True
        nas.app.secret_key = "test-key"
        self._reload = nas.reload_samba
        nas.reload_samba = lambda: None          # no smbd on the test machine
        self.c = nas.app.test_client()
        self.c.get("/api/roots")
        with self.c.session_transaction() as sess:
            self.tok = sess["csrf"]
        self.h = {"X-CSRF-Token": self.tok}

    def tearDown(self):
        nas.reload_samba = self._reload
        nas._OPTIONS, nas.DATA_DIR, nas.SHARES_FILE, nas.FILE_ACCESS_FILE, d = self._saved
        ss.init(d, nas.load_json, nas.save_json)
        ss._index["mtime"] = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mklink(self, **over):
        body = {"name": "Urlaub", "root": self.fotos, "mode": "download",
                "access": "password", "password": "geheim123"}
        body.update(over)
        r = self.c.post("/api/sharing/links", json=body, headers=self.h)
        return r


class LinkTest(Base):
    def test_create_and_list(self):
        r = self.mklink()
        self.assertEqual(r.status_code, 201, r.get_json())
        l = r.get_json()
        self.assertEqual(len(l["token"]), ss.TOKEN_LEN)
        self.assertTrue(l["has_password"])
        self.assertNotIn("password_hash", l)
        self.assertTrue(l["url"].endswith("/s/" + l["token"]))
        self.assertTrue(l["url_is_fallback"])
        self.assertEqual(len(self.c.get("/api/sharing/links").get_json()), 1)

    def test_token_alphabet_has_no_ambiguous_chars(self):
        for ch in "l1I0OB8":
            self.assertNotIn(ch, ss.TOKEN_ALPHABET)
        for _ in range(50):
            self.assertTrue(ss.token_shape_ok(ss.new_token()))
        self.assertFalse(ss.token_shape_ok("kurz"))
        self.assertFalse(ss.token_shape_ok("0" * ss.TOKEN_LEN))   # 0 is not in the alphabet

    def test_root_outside_allowed_is_refused(self):
        r = self.mklink(root=self.outside)
        self.assertEqual(r.status_code, 400)
        self.assertIn("außerhalb", r.get_json()["error"])

    def test_filesystem_root_is_refused(self):
        r = self.mklink(root="/")
        self.assertEqual(r.status_code, 400)

    def test_password_required_for_password_links(self):
        r = self.mklink(password="")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Passwort", r.get_json()["error"])

    def test_public_link_needs_no_password(self):
        r = self.mklink(access="public", password="")
        self.assertEqual(r.status_code, 201)
        self.assertFalse(r.get_json()["has_password"])

    def test_users_link_requires_known_accounts(self):
        r = self.mklink(access="users", password="", users=["niemand"])
        self.assertEqual(r.status_code, 400)
        self.c.post("/api/sharing/accounts", json={"username": "anna", "password": "annapass1"}, headers=self.h)
        r = self.mklink(access="users", password="", users=["anna"])
        self.assertEqual(r.status_code, 201, r.get_json())

    def test_single_file_link_must_exist_and_be_download(self):
        self.assertEqual(self.mklink(file="nope.jpg").status_code, 400)
        self.assertEqual(self.mklink(file="a.jpg", mode="both").status_code, 400)
        self.assertEqual(self.mklink(file="a.jpg").status_code, 201)

    def test_share_binding(self):
        self.assertEqual(self.mklink(share="Fotos").status_code, 201)
        self.assertEqual(self.mklink(share="Gibtsnicht").status_code, 400)

    def test_resolve_token_and_sha_index(self):
        l = self.mklink().get_json()
        self.assertEqual(ss.resolve_token(l["token"])["id"], l["id"])
        self.assertIsNone(ss.resolve_token(ss.new_token()))
        self.assertIsNone(ss.resolve_token("../etc"))

    def test_rotate_invalidates_old_token_and_bumps_epoch(self):
        l = self.mklink().get_json()
        r = self.c.post(f"/api/sharing/links/{l['id']}/rotate", headers=self.h).get_json()
        self.assertNotEqual(r["token"], l["token"])
        self.assertEqual(r["auth_epoch"], 2)
        self.assertIsNone(ss.resolve_token(l["token"]))
        self.assertIsNotNone(ss.resolve_token(r["token"]))

    def test_password_change_bumps_epoch_but_name_change_does_not(self):
        l = self.mklink().get_json()
        r = self.c.put(f"/api/sharing/links/{l['id']}", json={"name": "Neu"}, headers=self.h).get_json()
        self.assertEqual(r["auth_epoch"], 1)
        r = self.c.put(f"/api/sharing/links/{l['id']}", json={"password": "anders123"}, headers=self.h).get_json()
        self.assertEqual(r["auth_epoch"], 2)

    def test_live_state(self):
        l = self.mklink().get_json()
        self.assertTrue(l["live"])
        r = self.c.put(f"/api/sharing/links/{l['id']}", json={"expires": int(time.time()) - 10}, headers=self.h).get_json()
        self.assertFalse(r["live"])
        r = self.c.put(f"/api/sharing/links/{l['id']}", json={"expires": 0, "max_downloads": 1}, headers=self.h).get_json()
        self.assertTrue(r["live"])
        ss.record_download(l["id"], "1.2.3.4")
        self.assertFalse(self.c.get(f"/api/sharing/links").get_json()[0]["live"])
        self.assertEqual(self.c.get(f"/api/sharing/links").get_json()[0]["download_count"], 1)
        r = self.c.post(f"/api/sharing/links/{l['id']}/reset-counters", headers=self.h).get_json()
        self.assertEqual(r["download_count"], 0)
        self.assertTrue(r["live"])

    def test_counters_do_not_mirror_but_links_do(self):
        calls = []
        real = nas._auto_backup
        nas._auto_backup = lambda: calls.append(1)
        try:
            l = self.mklink().get_json()
            n = len(calls)
            ss.record_download(l["id"])
            ss.record_download(l["id"])
            self.assertEqual(len(calls), n, "Zaehler duerfen keinen Voll-Backup ausloesen")
        finally:
            nas._auto_backup = real

    def test_delete_link(self):
        l = self.mklink().get_json()
        self.assertEqual(self.c.delete(f"/api/sharing/links/{l['id']}", headers=self.h).status_code, 200)
        self.assertEqual(self.c.get("/api/sharing/links").get_json(), [])
        self.assertEqual(self.c.delete(f"/api/sharing/links/{l['id']}", headers=self.h).status_code, 400)

    def test_deleting_samba_share_disables_its_links(self):
        l = self.mklink(share="Fotos").get_json()
        r = self.c.delete("/api/shares/Fotos", headers=self.h)
        self.assertEqual(r.get_json()["links_disabled"], 1)
        self.assertFalse(self.c.get("/api/sharing/links").get_json()[0]["enabled"])

    def test_csrf_applies(self):
        r = self.c.post("/api/sharing/links", json={"name": "x"})
        self.assertEqual(r.status_code, 403)

    def test_status(self):
        self.mklink()
        st = self.c.get("/api/sharing/status").get_json()
        self.assertEqual(st["links"], 1)
        self.assertFalse(st["sharing_enabled"])
        self.assertFalse(st["public_site_running"])
        self.assertTrue(st["url_is_fallback"])

    def test_public_url_from_option(self):
        nas._OPTIONS["share_public_url"] = "https://files.example.com/"
        l = self.mklink().get_json()
        self.assertEqual(l["url"], "https://files.example.com/s/" + l["token"])
        self.assertFalse(l["url_is_fallback"])


class AccountTest(Base):
    def test_create_list_no_hash_leak(self):
        r = self.c.post("/api/sharing/accounts", json={"username": "anna", "password": "annapass1",
                                                       "display_name": "Anna"}, headers=self.h)
        self.assertEqual(r.status_code, 201, r.get_json())
        accs = self.c.get("/api/sharing/accounts").get_json()
        self.assertEqual(accs[0]["username"], "anna")
        self.assertNotIn("password_hash", accs[0])

    def test_validation(self):
        self.assertEqual(self.c.post("/api/sharing/accounts", json={"username": "a", "password": "annapass1"}, headers=self.h).status_code, 400)
        self.assertEqual(self.c.post("/api/sharing/accounts", json={"username": "anna", "password": "kurz"}, headers=self.h).status_code, 400)
        self.assertEqual(self.c.post("/api/sharing/accounts", json={"username": "an na", "password": "annapass1"}, headers=self.h).status_code, 400)

    def test_duplicate_is_case_insensitive(self):
        self.c.post("/api/sharing/accounts", json={"username": "Anna", "password": "annapass1"}, headers=self.h)
        self.assertEqual(self.c.post("/api/sharing/accounts", json={"username": "anna", "password": "annapass1"}, headers=self.h).status_code, 400)

    def test_password_check_and_disable(self):
        self.c.post("/api/sharing/accounts", json={"username": "anna", "password": "annapass1"}, headers=self.h)
        self.assertIsNotNone(ss.check_account_password("anna", "annapass1"))
        self.assertIsNone(ss.check_account_password("anna", "falsch"))
        self.c.put("/api/sharing/accounts/anna", json={"enabled": False}, headers=self.h)
        self.assertIsNone(ss.check_account_password("anna", "annapass1"))
        self.assertEqual(ss.get_account("anna")["auth_epoch"], 2)

    def test_delete_removes_from_links(self):
        self.c.post("/api/sharing/accounts", json={"username": "anna", "password": "annapass1"}, headers=self.h)
        self.mklink(access="users", password="", users=["anna"])
        self.assertEqual(self.c.delete("/api/sharing/accounts/anna", headers=self.h).status_code, 200)
        self.assertEqual(self.c.get("/api/sharing/links").get_json()[0]["users"], [])

    def test_accounts_file_is_private(self):
        self.c.post("/api/sharing/accounts", json={"username": "anna", "password": "annapass1"}, headers=self.h)
        p = os.path.join(self.tmp, "share_accounts.json")
        self.assertTrue(os.path.exists(p))
        if os.name == "posix":
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
        with open(p) as f:
            self.assertIn("password_hash", json.load(f)[0])


if __name__ == "__main__":
    unittest.main()
