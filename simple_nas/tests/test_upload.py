#!/usr/bin/env python3
"""Uploads through the public share site: names, limits, quotas, collisions,
extension policy, confinement, and the streaming write."""
import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import app as nas  # noqa: E402
import ratelimit  # noqa: E402
import share_web  # noqa: E402
import sharing_store as ss  # noqa: E402


class NameTest(unittest.TestCase):
    def test_umlauts_survive(self):
        self.assertEqual(ss.safe_upload_name("Grüße.pdf"), "Grüße.pdf")
        self.assertEqual(ss.safe_upload_name("Fotos vom Straßenfest 2026.jpg"), "Fotos vom Straßenfest 2026.jpg")

    def test_directory_parts_and_dots_are_stripped(self):
        self.assertEqual(ss.safe_upload_name("../../etc/passwd"), "passwd")
        self.assertEqual(ss.safe_upload_name("..\\..\\win.ini"), "win.ini")
        self.assertEqual(ss.safe_upload_name(".htaccess"), "htaccess")
        self.assertEqual(ss.safe_upload_name("  spaced   name .txt "), "spaced name .txt".replace(" .", ".") if False else "spaced name .txt")

    def test_control_chars_and_reserved_names(self):
        self.assertEqual(ss.safe_upload_name("bad\x00\x1fname.txt"), "badname.txt")
        self.assertEqual(ss.safe_upload_name("CON.txt"), "CON_.txt")
        self.assertEqual(ss.safe_upload_name("com1"), "com1_")

    def test_empty_gets_a_fallback(self):
        n = ss.safe_upload_name("")
        self.assertTrue(n.startswith("upload-") and n.endswith(".bin"))
        self.assertTrue(ss.safe_upload_name("...").startswith("upload-"))

    def test_long_stem_is_cut_but_extension_kept(self):
        n = ss.safe_upload_name("a" * 300 + ".jpeg")
        self.assertTrue(n.endswith(".jpeg"))
        self.assertLessEqual(len(n), 106)

    def test_extension_policy(self):
        self.assertFalse(ss.extension_allowed("virus.exe"))
        self.assertFalse(ss.extension_allowed("page.html"))
        self.assertFalse(ss.extension_allowed("x.html.txt"), "jede Endung der Kette zaehlt")
        self.assertFalse(ss.extension_allowed("logo.SVG"))
        self.assertTrue(ss.extension_allowed("bild.jpg"))
        self.assertTrue(ss.extension_allowed("README"))
        self.assertTrue(ss.extension_allowed("bild.jpg", allowed=["jpg", "png"]))
        self.assertFalse(ss.extension_allowed("doc.pdf", allowed=["jpg", "png"]))
        self.assertFalse(ss.extension_allowed("README", allowed=["jpg"]))


class UploadRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.media = os.path.join(self.tmp, "media")
        self.drop = os.path.join(self.media, "ablage")
        os.makedirs(self.drop)
        self.opts = {"share_allowed_roots": [self.media], "share_cookie_secure": False,
                     "share_trusted_proxies": ["127.0.0.1"], "share_max_upload_mb": 1}
        self._saved_limiter = ratelimit.LIMITER
        ratelimit.LIMITER = ratelimit.Limiter()
        share_web.LIMITER = ratelimit.LIMITER
        ss.init(self.tmp, nas.load_json, nas.save_json)
        self._saved_nas = (nas._OPTIONS, nas.DATA_DIR)
        nas._OPTIONS, nas.DATA_DIR = dict(self.opts), self.tmp
        self.app = share_web.create_share_app(lambda k, d=None: self.opts.get(k, d), lambda: [], lambda: [self.media], self.tmp)
        self.app.config["TESTING"] = True
        self.c = self.app.test_client()
        share_web.SCAN_HOOK = None

    def tearDown(self):
        ratelimit.LIMITER = self._saved_limiter
        share_web.LIMITER = self._saved_limiter
        nas._OPTIONS, nas.DATA_DIR = self._saved_nas
        ss._index["mtime"] = None
        share_web.SCAN_HOOK = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def link(self, **over):
        body = {"name": "Ablage", "root": self.drop, "mode": "upload", "access": "public", "upload_subdir": "none"}
        body.update(over)
        return ss.create_link(body, allowed_roots=[self.media])

    def csrf(self, token):
        self.c.get(f"/s/{token}/")
        with self.c.session_transaction() as s:
            return s.get("csrf", "")

    def up(self, link, name, data, dir="", length=None, csrf=True, extra_headers=None):
        tok = self.csrf(link["token"]) if csrf else ""
        headers = {"X-CSRF-Token": tok, "Content-Type": "application/octet-stream"}
        if extra_headers:
            headers.update(extra_headers)
        # the test client derives Content-Length from the body; a declared
        # value has to be forced in at environ level
        env = {"CONTENT_LENGTH": str(length)} if length is not None else {}
        return self.c.post(f"/s/{link['token']}/upload", query_string={"name": name, "dir": dir},
                           data=data, headers=headers, environ_overrides=env)

    def files(self, folder=None):
        folder = folder or self.drop
        return sorted(f for f in os.listdir(folder) if not f.startswith("."))

    def test_happy_path_stores_file_and_counts_bytes(self):
        l = self.link()
        r = self.up(l, "Grüße.pdf", b"%PDF-hallo")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["name"], "Grüße.pdf")
        with open(os.path.join(self.drop, "Grüße.pdf"), "rb") as f:
            self.assertEqual(f.read(), b"%PDF-hallo")
        self.assertEqual(ss.get_link(l["id"])["uploaded_bytes"], 10)
        self.assertEqual([f for f in os.listdir(self.drop) if f.endswith(".part")], [], "keine .part-Reste")

    def test_upload_page_is_shown(self):
        l = self.link(mode="both")
        h = self.c.get(f"/s/{l['token']}/").get_data(as_text=True)
        self.assertIn('id="upfile"', h)
        self.assertNotIn('id="upfile"', self.c.get(f"/s/{self.link(mode='download')['token']}/").get_data(as_text=True))

    def test_download_link_refuses_upload(self):
        self.assertEqual(self.up(self.link(mode="download"), "x.txt", b"x").status_code, 403)

    def test_csrf_required(self):
        self.assertEqual(self.up(self.link(), "x.txt", b"x", csrf=False).status_code, 403)

    def test_collision_never_overwrites(self):
        l = self.link()
        self.up(l, "a.txt", b"eins")
        self.up(l, "a.txt", b"zwei")
        self.up(l, "a.txt", b"drei")
        self.assertEqual(self.files(), ["a (2).txt", "a (3).txt", "a.txt"])
        with open(os.path.join(self.drop, "a.txt")) as f:
            self.assertEqual(f.read(), "eins")

    def test_traversal_in_name_and_dir(self):
        l = self.link(allow_subdirs=True)
        r = self.up(l, "../../escape.txt", b"x")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.files(), ["escape.txt"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.txt")))
        self.assertEqual(self.up(l, "x.txt", b"x", dir="../../").status_code, 404)

    def test_subdirs_forced_to_root_when_disallowed(self):
        l = self.link(allow_subdirs=False)
        os.makedirs(os.path.join(self.drop, "sub"))
        self.assertEqual(self.up(l, "x.txt", b"x", dir="sub").status_code, 200)
        self.assertEqual(self.files(), ["sub", "x.txt"])

    def test_by_date_subfolder(self):
        import datetime
        l = self.link(upload_subdir="by-date")
        self.assertEqual(self.up(l, "x.txt", b"x").status_code, 200)
        today = datetime.date.today().isoformat()
        self.assertEqual(self.files(), [today])
        self.assertEqual(self.files(os.path.join(self.drop, today)), ["x.txt"])

    def test_blocked_extensions(self):
        l = self.link()
        for bad in ("virus.exe", "page.html", "x.html.txt", "logo.svg", "run.sh"):
            r = self.up(l, bad, b"x")
            self.assertEqual(r.status_code, 415, bad)
        self.assertEqual(self.files(), [])

    def test_too_big_is_refused_before_reading(self):
        l = self.link()
        r = self.up(l, "big.bin", b"", length=2 * 1024 * 1024)      # declared 2 MB, limit 1 MB
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.get_json()["error"], "err_too_big")
        self.assertEqual(self.files(), [])

    def test_body_beyond_declared_length_is_never_read(self):
        # WSGI servers hand the app at most Content-Length bytes; a client that
        # declares 100 and sends more gets exactly 100 stored and no .part left.
        l = self.link()
        r = self.up(l, "small.bin", b"x" * (1024 * 1024 + 5000), length=100)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(os.path.getsize(os.path.join(self.drop, "small.bin")), 100)
        self.assertEqual([f for f in os.listdir(self.drop) if f.endswith(".part")], [])

    def test_missing_length_is_411(self):
        l = self.link()
        tok = self.csrf(l["token"])
        r = self.c.post(f"/s/{l['token']}/upload", query_string={"name": "x.txt"},
                        headers={"X-CSRF-Token": tok}, data=b"x", environ_overrides={"CONTENT_LENGTH": ""})
        self.assertEqual(r.status_code, 411)

    def test_quota(self):
        l = self.link(upload_quota_mb=1)
        self.assertEqual(self.up(l, "a.bin", b"a" * 600 * 1024).status_code, 200)
        r = self.up(l, "b.bin", b"b" * 600 * 1024)
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.get_json()["error"], "err_quota")
        self.assertEqual(self.files(), ["a.bin"])

    def test_per_link_max_file_mb_caps_below_site_limit(self):
        self.opts["share_max_upload_mb"] = 10
        l = self.link(max_file_mb=1)
        self.assertEqual(self.up(l, "x.bin", b"", length=2 * 1024 * 1024).status_code, 413)

    def test_upload_rate_limit(self):
        l = self.link()
        for i in range(ratelimit.UPLOAD_PER_IP[0]):
            self.assertEqual(self.up(l, f"f{i}.txt", b"x").status_code, 200)
        self.assertEqual(self.up(l, "one-more.txt", b"x").status_code, 429)

    def test_scan_hook_rejects_and_cleans_up(self):
        share_web.SCAN_HOOK = lambda path: ("infected", "Eicar-Test-Signature")
        r = self.up(self.link(), "e.txt", b"X5O!")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.files(), [])
        share_web.SCAN_HOOK = lambda path: ("error", "connection refused")
        self.assertEqual(self.up(self.link(), "e.txt", b"x").status_code, 503)
        share_web.SCAN_HOOK = lambda path: ("clean", "")
        self.assertEqual(self.up(self.link(), "ok.txt", b"x").status_code, 200)

    def test_multipart_fallback(self):
        l = self.link()
        tok = self.csrf(l["token"])
        r = self.c.post(f"/s/{l['token']}/upload-form", data={"csrf": tok, "dir": "",
                        "file": (io.BytesIO(b"formdata"), "form.txt")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.files(), ["form.txt"])

    def test_single_file_link_cannot_receive_uploads(self):
        with open(os.path.join(self.drop, "only.txt"), "w") as f:
            f.write("x")
        l = self.link(mode="download", file="only.txt")
        self.assertEqual(self.up(l, "x.txt", b"x").status_code, 403)


if __name__ == "__main__":
    unittest.main()


class SubdirDefaultTest(UploadRouteTest):
    def test_both_mode_defaults_to_current_folder(self):
        l = ss.create_link({"name": "x", "root": self.drop, "mode": "both", "access": "public"}, allowed_roots=[self.media])
        self.assertEqual(l["upload_subdir"], "none")
        os.makedirs(os.path.join(self.drop, "Gregor"))
        self.assertEqual(self.up(l, "p.txt", b"x", dir="Gregor").status_code, 200)
        self.assertEqual(self.files(os.path.join(self.drop, "Gregor")), ["p.txt"])

    def test_upload_only_defaults_to_by_date(self):
        l = ss.create_link({"name": "x", "root": self.drop, "mode": "upload", "access": "public"}, allowed_roots=[self.media])
        self.assertEqual(l["upload_subdir"], "by-date")

    def test_response_tells_where_the_file_went(self):
        l = self.link(mode="both", upload_subdir="by-date")
        os.makedirs(os.path.join(self.drop, "Gregor"))
        r = self.up(l, "p.txt", b"x", dir="Gregor")
        import datetime
        self.assertEqual(r.get_json()["path"], f"Gregor/{datetime.date.today().isoformat()}/p.txt")
