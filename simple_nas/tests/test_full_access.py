#!/usr/bin/env python3
"""With "Zugriff auf alle Dateien" on, the file manager must actually get in.

Regression: with "/" as the only allowed root, /api/files?path=/ returned the
synthetic roots listing containing a single entry "/" pointing at itself, so
the UI looped forever at the top level.
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import app as nas  # noqa: E402


class FullAccessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (nas._OPTIONS, nas.DATA_DIR, nas.FILE_ACCESS_FILE)
        nas._OPTIONS = {}
        nas.DATA_DIR = self.tmp
        nas.FILE_ACCESS_FILE = os.path.join(self.tmp, "file_access.json")
        nas.app.config["TESTING"] = True
        nas.app.secret_key = "test-key"
        self.c = nas.app.test_client()
        self.c.get("/api/roots")
        with self.c.session_transaction() as sess:
            tok = sess["csrf"]
        r = self.c.post("/api/settings/file-access", json={"full_access": True},
                        headers={"X-CSRF-Token": tok})
        assert r.status_code == 200, r.get_json()

    def tearDown(self):
        nas._OPTIONS, nas.DATA_DIR, nas.FILE_ACCESS_FILE = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_root_lists_the_real_filesystem_root(self):
        r = self.c.get("/api/files", query_string={"path": "/"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertFalse(body.get("is_root"), "must not be the synthetic roots listing")
        names = [e["name"] for e in body["entries"]]
        # not a single self-referencing "/" entry
        self.assertNotEqual([e["path"] for e in body["entries"]], ["/"])
        self.assertTrue(len(names) > 1, names)
        self.assertIsNone(body["parent"])

    def test_browse_root_lists_the_real_filesystem_root(self):
        body = self.c.get("/api/browse", query_string={"path": "/"}).get_json()
        self.assertFalse(body.get("is_root"))
        self.assertTrue(len(body["entries"]) > 1)

    def test_breadcrumb_has_one_root_crumb(self):
        body = self.c.get("/api/files", query_string={"path": "/"}).get_json()
        crumbs = [c["path"] for c in body["breadcrumb"]]
        self.assertEqual(crumbs.count("/"), 1, crumbs)

    def test_subfolder_parent_leads_back_to_root(self):
        sub = os.path.join(self.tmp, "sub")
        os.makedirs(sub)
        body = self.c.get("/api/files", query_string={"path": sub}).get_json()
        self.assertEqual(body["parent"], nas.safepath.real(self.tmp))

    def test_addons_own_secrets_stay_shut(self):
        self.assertEqual(self.c.get("/api/files", query_string={"path": "/data"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
