#!/usr/bin/env python3
"""Session hardening, CSRF and the file-access settings."""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import app as nas  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "media")
        os.makedirs(self.root)
        self._saved = (nas._OPTIONS, nas.DATA_DIR, nas.FILE_ACCESS_FILE, dict(nas._admin_auth))
        nas._OPTIONS = {"file_allowed_roots": [self.root]}
        nas.DATA_DIR = self.tmp
        nas.FILE_ACCESS_FILE = os.path.join(self.tmp, "file_access.json")
        nas.app.config["TESTING"] = True
        nas.app.secret_key = "test-key"
        self.c = nas.app.test_client()

    def tearDown(self):
        nas._OPTIONS, nas.DATA_DIR, nas.FILE_ACCESS_FILE, auth = self._saved
        nas._admin_auth.clear()
        nas._admin_auth.update(auth)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def token(self):
        """A GET issues the token; read it back out of the session cookie."""
        self.c.get("/api/roots")
        with self.c.session_transaction() as sess:
            return sess.get("csrf", "")


class CsrfTest(Base):
    def test_get_is_never_blocked(self):
        self.assertEqual(self.c.get("/api/roots").status_code, 200)

    def test_post_without_token_is_refused(self):
        r = self.c.post("/api/mkdir", json={"path": os.path.join(self.root, "neu")})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(os.path.exists(os.path.join(self.root, "neu")))

    def test_post_with_wrong_token_is_refused(self):
        self.token()
        r = self.c.post("/api/mkdir", json={"path": os.path.join(self.root, "neu")},
                        headers={"X-CSRF-Token": "falsch"})
        self.assertEqual(r.status_code, 403)

    def test_post_with_token_passes(self):
        tok = self.token()
        r = self.c.post("/api/mkdir", json={"path": os.path.join(self.root, "neu")},
                        headers={"X-CSRF-Token": tok})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(os.path.isdir(os.path.join(self.root, "neu")))

    def test_status_hands_out_the_token(self):
        body = self.c.get("/api/status").get_json()
        self.assertTrue(body["csrf"])
        with self.c.session_transaction() as sess:
            self.assertEqual(body["csrf"], sess["csrf"])


class SetupLockdownTest(Base):
    def test_locked_when_password_missing(self):
        nas._admin_auth.clear()
        nas._admin_auth.update({"enabled": True, "setup_required": True, "username": "admin"})
        self.assertEqual(self.c.get("/api/roots").status_code, 503)
        self.assertEqual(self.c.get("/api/files", query_string={"path": "/"}).status_code, 503)
        r = self.c.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/setup-required", r.headers["Location"])
        page = self.c.get("/setup-required")
        self.assertEqual(page.status_code, 503)
        self.assertIn("admin_password", page.get_data(as_text=True))

    def test_no_password_means_no_secret_leak_either(self):
        nas._admin_auth.clear()
        nas._admin_auth.update({"enabled": True, "setup_required": True})
        self.assertEqual(self.c.get("/api/status").status_code, 503)


class SessionEpochTest(Base):
    def test_session_dies_when_the_password_changes(self):
        pw_hash = "hash-eins"
        nas._admin_auth.clear()
        nas._admin_auth.update({"enabled": True, "username": "admin", "password_hash": pw_hash})
        with self.c.session_transaction() as sess:
            sess["authenticated"] = True
            sess["auth_epoch"] = nas._auth_epoch(pw_hash)
        self.assertEqual(self.c.get("/api/roots").status_code, 200)

        nas._admin_auth["password_hash"] = "hash-zwei"      # password changed in the options
        r = self.c.get("/api/roots")
        self.assertEqual(r.status_code, 401)
        with self.c.session_transaction() as sess:
            self.assertNotIn("authenticated", sess)


class FileAccessTest(Base):
    def test_defaults_come_from_the_option(self):
        body = self.c.get("/api/settings/file-access").get_json()
        self.assertEqual(body["roots"], [self.root])
        self.assertFalse(body["full_access"])
        self.assertIn("/data", body["always_blocked"])

    def test_adding_a_root_widens_access(self):
        extra = os.path.join(self.tmp, "extra")
        os.makedirs(extra)
        self.assertEqual(self.c.get("/api/files", query_string={"path": extra}).status_code, 403)
        r = self.c.post("/api/settings/file-access", json={"roots": [self.root, extra]},
                        headers={"X-CSRF-Token": self.token()})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/api/files", query_string={"path": extra}).status_code, 200)

    def test_full_access_button(self):
        outside = os.path.join(self.tmp, "woanders")
        os.makedirs(outside)
        self.assertEqual(self.c.get("/api/files", query_string={"path": outside}).status_code, 403)

        r = self.c.post("/api/settings/file-access", json={"full_access": True},
                        headers={"X-CSRF-Token": self.token()})
        self.assertEqual(r.status_code, 200)
        body = self.c.get("/api/settings/file-access").get_json()
        self.assertTrue(body["full_access"])
        self.assertTrue(body["unlock_system"])
        self.assertEqual(self.c.get("/api/files", query_string={"path": outside}).status_code, 200)

    def test_full_access_still_refuses_the_addons_own_secrets(self):
        self.c.post("/api/settings/file-access", json={"full_access": True},
                    headers={"X-CSRF-Token": self.token()})
        # /data holds admin_auth.json with the password hash
        self.assertEqual(self.c.get("/api/files", query_string={"path": "/data"}).status_code, 403)
        self.assertEqual(self.c.get("/api/files", query_string={"path": "/proc"}).status_code, 403)

    def test_reset_narrows_again(self):
        outside = os.path.join(self.tmp, "woanders")
        os.makedirs(outside)
        tok = self.token()
        self.c.post("/api/settings/file-access", json={"full_access": True}, headers={"X-CSRF-Token": tok})
        self.assertEqual(self.c.get("/api/files", query_string={"path": outside}).status_code, 200)
        self.c.post("/api/settings/file-access", json={"reset": True}, headers={"X-CSRF-Token": tok})
        self.assertEqual(self.c.get("/api/files", query_string={"path": outside}).status_code, 403)
        self.assertEqual(self.c.get("/api/settings/file-access").get_json()["roots"], nas.DEFAULT_FILE_ROOTS)

    def test_relative_root_is_refused(self):
        r = self.c.post("/api/settings/file-access", json={"roots": ["media"]},
                        headers={"X-CSRF-Token": self.token()})
        self.assertEqual(r.status_code, 400)

    def test_empty_root_list_is_refused(self):
        r = self.c.post("/api/settings/file-access", json={"roots": []},
                        headers={"X-CSRF-Token": self.token()})
        self.assertEqual(r.status_code, 400)

    def test_setting_survives_a_restart(self):
        extra = os.path.join(self.tmp, "extra")
        os.makedirs(extra)
        self.c.post("/api/settings/file-access", json={"roots": [self.root, extra]},
                    headers={"X-CSRF-Token": self.token()})
        with open(nas.FILE_ACCESS_FILE) as f:
            self.assertIn(extra, json.load(f)["roots"])
        # a fresh read (as after a restart) must see it
        self.assertIn(extra, nas.file_roots())


if __name__ == "__main__":
    unittest.main()
