#!/usr/bin/env python3
"""The admin file API must not reach outside the allowed roots.

Before this was added, every endpoint took an absolute host path straight from
the browser while running as root.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import app as nas  # noqa: E402


class FileApiConfinementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "media")
        self.outside = os.path.join(self.tmp, "secret")
        os.makedirs(os.path.join(self.root, "fotos"))
        os.makedirs(self.outside)
        with open(os.path.join(self.root, "fotos", "a.txt"), "w") as f:
            f.write("drin")
        self.secret_file = os.path.join(self.outside, "passwords.txt")
        with open(self.secret_file, "w") as f:
            f.write("geheim")

        # Point the add-on at our temp root instead of /media, /mnt, ...
        self._saved_options = nas._OPTIONS
        nas._OPTIONS = {"file_allowed_roots": [self.root]}
        nas.app.config["TESTING"] = True
        nas.app.secret_key = "test-key"
        self.c = nas.app.test_client()
        self.c.get("/api/roots")                 # issues the CSRF token
        with self.c.session_transaction() as sess:
            self.csrf = sess.get("csrf", "")

    def post(self, path, **kw):
        """POST with the CSRF header the real UI sends."""
        kw.setdefault("headers", {})["X-CSRF-Token"] = self.csrf
        return self.c.post(path, **kw)

    def tearDown(self):
        nas._OPTIONS = self._saved_options
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── reading ──────────────────────────────────────────────────────
    def test_listing_inside_root_works(self):
        r = self.c.get("/api/files", query_string={"path": os.path.join(self.root, "fotos")})
        self.assertEqual(r.status_code, 200)
        self.assertIn("a.txt", [e["name"] for e in r.get_json()["entries"]])

    def test_listing_outside_root_is_refused(self):
        r = self.c.get("/api/files", query_string={"path": self.outside})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["error"], "Pfad nicht erlaubt")

    def test_traversal_out_of_root_is_refused(self):
        sneaky = os.path.join(self.root, "fotos", "..", "..", "secret")
        r = self.c.get("/api/files", query_string={"path": sneaky})
        self.assertEqual(r.status_code, 403)

    def test_container_root_shows_the_allowed_roots_only(self):
        r = self.c.get("/api/files", query_string={"path": "/"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["is_root"])
        self.assertEqual([e["path"] for e in body["entries"]], [nas.safepath.real(self.root)])

    def test_browse_is_confined_too(self):
        self.assertEqual(self.c.get("/api/browse", query_string={"path": self.outside}).status_code, 403)
        self.assertEqual(self.c.get("/api/browse", query_string={"path": "/"}).status_code, 200)

    def test_breadcrumb_stops_at_the_root(self):
        r = self.c.get("/api/files", query_string={"path": os.path.join(self.root, "fotos")})
        crumbs = [c["path"] for c in r.get_json()["breadcrumb"]]
        # Only "/" (the roots listing), the root itself and the subfolder
        self.assertEqual(crumbs[0], "/")
        self.assertEqual(len(crumbs), 3)
        self.assertNotIn(nas.safepath.real(self.tmp), crumbs)

    def test_roots_endpoint(self):
        r = self.c.get("/api/roots")
        self.assertEqual(r.get_json()["roots"], [nas.safepath.real(self.root)])

    # ── file content ─────────────────────────────────────────────────
    def test_download_outside_is_refused(self):
        self.assertEqual(
            self.c.get("/api/files/download", query_string={"path": self.secret_file}).status_code, 403)

    def test_view_outside_is_refused(self):
        self.assertEqual(
            self.c.get("/api/files/view", query_string={"path": self.secret_file}).status_code, 403)

    def test_content_outside_is_refused(self):
        self.assertEqual(
            self.c.get("/api/files/content", query_string={"path": self.secret_file}).status_code, 403)

    def test_content_inside_works(self):
        r = self.c.get("/api/files/content",
                       query_string={"path": os.path.join(self.root, "fotos", "a.txt")})
        self.assertEqual(r.get_json()["content"], "drin")

    def test_write_outside_is_refused(self):
        r = self.post("/api/files/write", json={"path": self.secret_file, "content": "pwned"})
        self.assertEqual(r.status_code, 403)
        with open(self.secret_file) as f:
            self.assertEqual(f.read(), "geheim")

    # ── mutating ─────────────────────────────────────────────────────
    def test_delete_outside_is_refused(self):
        r = self.post("/api/files/delete", json={"path": self.secret_file})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(os.path.exists(self.secret_file))

    def test_delete_of_a_root_is_refused(self):
        r = self.post("/api/files/delete", json={"path": self.root})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(os.path.isdir(self.root))

    def test_delete_inside_works(self):
        victim = os.path.join(self.root, "weg.txt")
        with open(victim, "w") as f:
            f.write("x")
        self.assertEqual(self.post("/api/files/delete", json={"path": victim}).status_code, 200)
        self.assertFalse(os.path.exists(victim))

    def test_rename_rejects_separator_in_new_name(self):
        src = os.path.join(self.root, "fotos", "a.txt")
        r = self.post("/api/files/rename", json={"path": src, "new_name": "../../ausbruch.txt"})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(os.path.exists(src))

    def test_rename_of_a_root_is_refused(self):
        r = self.post("/api/files/rename", json={"path": self.root, "new_name": "x"})
        self.assertEqual(r.status_code, 400)

    def test_copy_outside_is_refused(self):
        src = os.path.join(self.root, "fotos", "a.txt")
        r = self.post("/api/files/copy", json={"src": src, "dst": os.path.join(self.outside, "kopie.txt")})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(os.path.exists(os.path.join(self.outside, "kopie.txt")))

    def test_move_out_of_root_is_refused(self):
        src = os.path.join(self.root, "fotos", "a.txt")
        r = self.post("/api/files/move", json={"src": src, "dst": os.path.join(self.outside, "a.txt")})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(os.path.exists(src))

    def test_mkdir_outside_is_refused(self):
        r = self.post("/api/mkdir", json={"path": os.path.join(self.outside, "neu")})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(os.path.exists(os.path.join(self.outside, "neu")))

    # ── upload ───────────────────────────────────────────────────────
    def test_upload_filename_cannot_traverse(self):
        data = {"path": self.root, "file": (io.BytesIO(b"x"), "../../../ausbruch.txt")}
        r = self.post("/api/files/upload", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200)
        # It landed under the root as a plain name, not above it
        self.assertEqual(os.path.dirname(r.get_json()["path"]), nas.safepath.real(self.root))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ausbruch.txt")))

    def test_upload_into_forbidden_dir_is_refused(self):
        data = {"path": self.outside, "file": (io.BytesIO(b"x"), "y.txt")}
        r = self.post("/api/files/upload", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 403)

    def test_upload_does_not_silently_overwrite(self):
        data = {"path": os.path.join(self.root, "fotos"), "file": (io.BytesIO(b"neu"), "a.txt")}
        r = self.post("/api/files/upload", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 409)
        with open(os.path.join(self.root, "fotos", "a.txt")) as f:
            self.assertEqual(f.read(), "drin")

    # ── upload in pieces (v3.9.5) ────────────────────────────────────
    # One request per file was refused above 100 MB behind Cloudflare.
    def start(self, name, size, folder=None):
        return self.post("/api/files/upload/start",
                         json={"path": folder or self.root, "name": name, "size": size})

    def chunk(self, uid, offset, data):
        return self.post("/api/files/upload/chunk", query_string={"id": uid, "offset": offset},
                         data=data, content_type="application/octet-stream")

    def parts(self, folder=None):
        return [n for n in os.listdir(folder or self.root) if n.endswith(".part")]

    def test_pieces_are_joined_in_order(self):
        r = self.start("film.mov", 10)
        self.assertEqual(r.status_code, 200)
        uid = r.get_json()["id"]
        self.assertEqual(self.chunk(uid, 0, b"0123").get_json()["written"], 4)
        self.assertEqual(self.chunk(uid, 4, b"4567").get_json()["written"], 8)
        last = self.chunk(uid, 8, b"89")
        self.assertEqual(last.status_code, 200)
        self.assertTrue(last.get_json()["done"])
        with open(os.path.join(self.root, "film.mov"), "rb") as f:
            self.assertEqual(f.read(), b"0123456789")
        self.assertEqual(self.parts(), [])

    def test_retried_piece_reports_the_position(self):
        uid = self.start("film.mov", 8).get_json()["id"]
        self.chunk(uid, 0, b"0123")
        again = self.chunk(uid, 0, b"0123")        # the answer got lost, browser resends
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.get_json()["written"], 4)
        self.assertTrue(self.chunk(uid, 4, b"4567").get_json()["done"])
        with open(os.path.join(self.root, "film.mov"), "rb") as f:
            self.assertEqual(f.read(), b"01234567")

    def test_piece_beyond_the_declared_size_is_refused(self):
        uid = self.start("film.mov", 4).get_json()["id"]
        self.assertEqual(self.chunk(uid, 0, b"012345").status_code, 400)

    def test_unknown_upload_is_404(self):
        self.assertEqual(self.chunk("gibtsnicht", 0, b"x").status_code, 404)

    def test_start_refuses_existing_forbidden_and_traversing_names(self):
        self.assertEqual(self.start("a.txt", 3, os.path.join(self.root, "fotos")).status_code, 409)
        self.assertEqual(self.start("y.txt", 3, self.outside).status_code, 403)
        r = self.start("../../../ausbruch.txt", 1)
        uid = r.get_json()["id"]
        path = self.chunk(uid, 0, b"x").get_json()["path"]
        self.assertEqual(os.path.dirname(path), nas.safepath.real(self.root))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ausbruch.txt")))

    def test_file_appearing_meanwhile_is_not_overwritten(self):
        uid = self.start("neu.txt", 3).get_json()["id"]
        with open(os.path.join(self.root, "neu.txt"), "w") as f:
            f.write("samba")                        # someone else was faster
        self.assertEqual(self.chunk(uid, 0, b"abc").status_code, 409)
        with open(os.path.join(self.root, "neu.txt")) as f:
            self.assertEqual(f.read(), "samba")
        self.assertEqual(self.parts(), [])

    def test_empty_file_is_done_at_start(self):
        r = self.start("leer.txt", 0)
        self.assertTrue(r.get_json()["done"])
        self.assertEqual(os.path.getsize(os.path.join(self.root, "leer.txt")), 0)

    def test_abort_removes_the_part_file(self):
        uid = self.start("film.mov", 8).get_json()["id"]
        self.chunk(uid, 0, b"0123")
        self.assertEqual(len(self.parts()), 1)
        self.post("/api/files/upload/abort", json={"id": uid})
        self.assertEqual(self.parts(), [])
        self.assertEqual(self.chunk(uid, 4, b"4567").status_code, 404)

    def test_pieces_need_the_csrf_token(self):
        uid = self.start("film.mov", 4).get_json()["id"]
        r = self.c.post("/api/files/upload/chunk", query_string={"id": uid, "offset": 0},
                        data=b"0123", content_type="application/octet-stream")
        self.assertEqual(r.status_code, 403)

    # ── folder picker handed a file ──────────────────────────────────
    def test_browse_on_a_file_is_a_clean_400(self):
        r = self.c.get("/api/browse", query_string={"path": os.path.join(self.root, "fotos", "a.txt")})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()


class AdminZipTest(FileApiConfinementTest):
    def test_zip_inside_root_streams_a_valid_archive(self):
        import io as _io
        import zipfile
        r = self.c.get("/api/files/zip", query_string={"path": os.path.join(self.root, "fotos")})
        self.assertEqual(r.status_code, 200)
        with zipfile.ZipFile(_io.BytesIO(r.get_data())) as z:
            self.assertIsNone(z.testzip())
            self.assertIn("fotos/a.txt", z.namelist())

    def test_zip_outside_root_is_refused(self):
        self.assertEqual(self.c.get("/api/files/zip", query_string={"path": self.outside}).status_code, 403)
