#!/usr/bin/env python3
"""The public share site: surface, authorization chain, identical 404s,
rate limits, browsing and downloads confined to the link root."""
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import app as nas  # noqa: E402
import accesslog  # noqa: E402
import ratelimit  # noqa: E402
import share_web  # noqa: E402
import sharing_store as ss  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.media = os.path.join(self.tmp, "media")
        self.fotos = os.path.join(self.media, "fotos")
        os.makedirs(os.path.join(self.fotos, "2026"))
        with open(os.path.join(self.fotos, "a.jpg"), "wb") as f:
            f.write(b"\xff\xd8jpegdata" * 100)
        with open(os.path.join(self.fotos, "notes.txt"), "w") as f:
            f.write("hallo")
        with open(os.path.join(self.fotos, "evil.html"), "w") as f:
            f.write("<script>alert(1)</script>")
        with open(os.path.join(self.fotos, ".hidden"), "w") as f:
            f.write("x")
        with open(os.path.join(self.fotos, "up.part"), "w") as f:
            f.write("x")
        self.secret = os.path.join(self.tmp, "secret.txt")
        with open(self.secret, "w") as f:
            f.write("geheim")

        self.clock = FakeClock()
        self._saved_limiter = ratelimit.LIMITER
        ratelimit.LIMITER = ratelimit.Limiter(clock=self.clock)
        share_web.LIMITER = ratelimit.LIMITER
        self.opts = {"share_allowed_roots": [self.media], "share_cookie_secure": False,
                     "share_trusted_proxies": ["127.0.0.1"], "share_session_hours": 8}
        ss.init(self.tmp, nas.load_json, nas.save_json)
        self._saved_nas = (nas._OPTIONS, nas.DATA_DIR)
        nas._OPTIONS, nas.DATA_DIR = dict(self.opts), self.tmp
        self.app = share_web.create_share_app(lambda k, d=None: self.opts.get(k, d), lambda: [], lambda: [self.media], self.tmp)
        self.app.config["TESTING"] = True
        self.c = self.app.test_client()

    def tearDown(self):
        ratelimit.LIMITER = self._saved_limiter
        share_web.LIMITER = self._saved_limiter
        nas._OPTIONS, nas.DATA_DIR = self._saved_nas
        ss._index["mtime"] = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def link(self, **over):
        body = {"name": "Urlaub", "root": self.fotos, "mode": "download", "access": "public"}
        body.update(over)
        return ss.create_link(body, allowed_roots=[self.media])

    def csrf(self, token):
        self.c.get(f"/s/{token}/")
        with self.c.session_transaction() as s:
            return s.get("csrf", "")


class SurfaceTest(Base):
    def test_only_expected_endpoints(self):
        self.assertTrue(share_web.assert_public_surface(self.app))
        got = {r.endpoint for r in self.app.url_map.iter_rules()} - {"static"}
        self.assertEqual(got, share_web.PUBLIC_ENDPOINTS)

    def test_admin_routes_do_not_exist_here(self):
        for path in ("/api/files?path=/etc", "/api/shares", "/api/sharing/links", "/login",
                     "/api/settings/file-access", "/api/status"):
            self.assertEqual(self.c.get(path).status_code, 404, path)

    def test_assert_refuses_extra_route(self):
        @self.app.route("/api/oops")
        def oops():
            return "x"
        with self.assertRaises(SystemExit):
            share_web.assert_public_surface(self.app)

    def test_healthz_and_root(self):
        r = self.c.get("/healthz")
        self.assertEqual((r.status_code, r.get_data(as_text=True)), (200, "ok"))
        self.assertEqual(self.c.get("/").status_code, 404)

    def test_security_headers(self):
        r = self.c.get("/healthz")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", r.headers["Content-Security-Policy"])
        self.assertNotIn("Strict-Transport-Security", r.headers)      # plain http in tests
        self.assertNotIn("Server", r.headers)


class NotFoundTest(Base):
    def body(self, token):
        r = self.c.get(f"/s/{token}/")
        return r.status_code, r.get_data()

    def test_every_pre_auth_failure_is_byte_identical(self):
        unknown = self.body(ss.new_token())
        disabled = self.link(enabled=False)
        expired = self.link(expires=int(time.time()) - 5)
        limited = self.link(max_downloads=1)
        ss.record_download(limited["id"])
        gone = self.link()
        shutil.rmtree(self.fotos)          # root vanished (drive unplugged)
        results = [unknown, self.body(disabled["token"]), self.body(expired["token"]),
                   self.body(limited["token"]), self.body(gone["token"]),
                   self.body("kurz"), self.body("../../etc/passwd")]
        for status, body in results:
            self.assertEqual(status, 404)
            self.assertEqual(body, unknown[1], "404-Seiten muessen byteweise gleich sein")
        self.assertNotIn(gone["token"].encode(), unknown[1])


class PublicLinkTest(Base):
    def test_listing_hides_dotfiles_parts_and_offers_downloads(self):
        l = self.link()
        r = self.c.get(f"/s/{l['token']}/")
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("a.jpg", html)
        self.assertIn("2026", html)
        self.assertNotIn(".hidden", html)
        self.assertNotIn("up.part", html)
        self.assertNotIn(self.fotos, html, "absolute Pfade duerfen nicht erscheinen")

    def test_download_counts_and_confines(self):
        l = self.link()
        r = self.c.get(f"/s/{l['token']}/d/a.jpg")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertEqual(ss.get_link(l["id"])["download_count"], 1)
        # a Range continuation must not count again
        self.c.get(f"/s/{l['token']}/d/a.jpg", headers={"Range": "bytes=100-"})
        self.assertEqual(ss.get_link(l["id"])["download_count"], 1)
        for bad in ("../secret.txt", "..%2Fsecret.txt", ".hidden", "up.part", "nope.txt"):
            self.assertEqual(self.c.get(f"/s/{l['token']}/d/{bad}").status_code, 404, bad)

    def test_html_is_never_served_inline(self):
        l = self.link()
        r = self.c.get(f"/s/{l['token']}/v/evil.html")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "application/octet-stream")
        self.assertIn("attachment", r.headers["Content-Disposition"])
        r = self.c.get(f"/s/{l['token']}/v/a.jpg")
        self.assertEqual(r.mimetype, "image/jpeg")
        self.assertIn("inline", r.headers["Content-Disposition"])

    def test_subfolder_browse_and_allow_subdirs_false(self):
        l = self.link()
        self.assertEqual(self.c.get(f"/s/{l['token']}/b/2026").status_code, 200)
        self.assertEqual(self.c.get(f"/s/{l['token']}/b/../").status_code, 404)
        l2 = self.link(allow_subdirs=False)
        self.assertEqual(self.c.get(f"/s/{l2['token']}/b/2026").status_code, 404)

    def test_single_file_link(self):
        l = self.link(file="a.jpg")
        html = self.c.get(f"/s/{l['token']}/").get_data(as_text=True)
        self.assertIn("a.jpg", html)
        self.assertNotIn("notes.txt", html)
        self.assertEqual(self.c.get(f"/s/{l['token']}/d/a.jpg").status_code, 200)
        self.assertEqual(self.c.get(f"/s/{l['token']}/d/notes.txt").status_code, 404)
        self.assertEqual(self.c.get(f"/s/{l['token']}/b/2026").status_code, 404)

    def test_upload_mode_link_blocks_reading(self):
        l = self.link(mode="upload")
        self.assertEqual(self.c.get(f"/s/{l['token']}/").status_code, 200)
        self.assertEqual(self.c.get(f"/s/{l['token']}/d/a.jpg").status_code, 403)
        self.assertEqual(self.c.get(f"/s/{l['token']}/b/2026").status_code, 403)

    def test_unicode_filename_disposition(self):
        with open(os.path.join(self.fotos, "Grüße.txt"), "w") as f:
            f.write("x")
        l = self.link()
        r = self.c.get(f"/s/{l['token']}/d/Gr%C3%BC%C3%9Fe.txt")
        self.assertEqual(r.status_code, 200)
        self.assertIn("filename*=UTF-8''Gr%C3%BC%C3%9Fe.txt", r.headers["Content-Disposition"])


class PasswordLinkTest(Base):
    def test_unlock_flow(self):
        l = self.link(access="password", password="geheim123")
        r = self.c.get(f"/s/{l['token']}/")
        self.assertEqual(r.status_code, 200, "Formular mit 200, nie 401")
        self.assertIn('name="password"', r.get_data(as_text=True))
        self.assertEqual(self.c.get(f"/s/{l['token']}/d/a.jpg").status_code, 302)
        tok = self.csrf(l["token"])
        r = self.c.post(f"/s/{l['token']}/auth", data={"password": "falsch", "csrf": tok})
        self.assertEqual(r.status_code, 200)
        self.assertIn('class="err"', r.get_data(as_text=True))   # denied notice, either language
        r = self.c.post(f"/s/{l['token']}/auth", data={"password": "geheim123", "csrf": tok})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get(f"/s/{l['token']}/d/a.jpg").status_code, 200)
        self.c.get(f"/s/{l['token']}/logout")
        self.assertEqual(self.c.get(f"/s/{l['token']}/d/a.jpg").status_code, 302)

    def test_csrf_required(self):
        l = self.link(access="password", password="geheim123")
        self.csrf(l["token"])
        r = self.c.post(f"/s/{l['token']}/auth", data={"password": "geheim123"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get(f"/s/{l['token']}/d/a.jpg").status_code, 302)

    def test_password_change_kicks_session(self):
        l = self.link(access="password", password="geheim123")
        tok = self.csrf(l["token"])
        self.c.post(f"/s/{l['token']}/auth", data={"password": "geheim123", "csrf": tok})
        self.assertEqual(self.c.get(f"/s/{l['token']}/d/a.jpg").status_code, 200)
        ss.update_link(l["id"], {"password": "anders123"}, allowed_roots=[self.media])
        self.assertEqual(self.c.get(f"/s/{l['token']}/d/a.jpg").status_code, 302)

    def test_lockout_per_link_and_unlock(self):
        l = self.link(access="password", password="geheim123")
        tok = self.csrf(l["token"])
        share_web.time.sleep = lambda s: None
        for i in range(ratelimit.AUTHFAIL_LINK[0]):
            self.c.post(f"/s/{l['token']}/auth", data={"password": "x", "csrf": tok},
                        environ_base={"REMOTE_ADDR": f"10.0.0.{i}"})   # many IPs, one link
        r = self.c.post(f"/s/{l['token']}/auth", data={"password": "geheim123", "csrf": tok},
                        environ_base={"REMOTE_ADDR": "10.0.1.1"})
        self.assertIn('class="warn"', r.get_data(as_text=True))   # locked notice, either language
        self.assertEqual(self.c.get(f"/s/{l['token']}/d/a.jpg").status_code, 302)
        ratelimit.LIMITER.clear(key=f"authfail:link:{l['id']}")
        r = self.c.post(f"/s/{l['token']}/auth", data={"password": "geheim123", "csrf": tok},
                        environ_base={"REMOTE_ADDR": "10.0.1.1"})
        self.assertEqual(r.status_code, 302)

    def test_lockout_expires_with_the_clock(self):
        l = self.link(access="password", password="geheim123")
        tok = self.csrf(l["token"])
        share_web.time.sleep = lambda s: None
        for _ in range(ratelimit.AUTHFAIL_IP[0]):
            self.c.post(f"/s/{l['token']}/auth", data={"password": "x", "csrf": tok})
        r = self.c.post(f"/s/{l['token']}/auth", data={"password": "geheim123", "csrf": tok})
        self.assertIn('class="warn"', r.get_data(as_text=True))   # locked notice, either language
        self.clock.t += ratelimit.AUTHFAIL_IP[1] + 1
        r = self.c.post(f"/s/{l['token']}/auth", data={"password": "geheim123", "csrf": tok})
        self.assertEqual(r.status_code, 302)


class UsersLinkTest(Base):
    def setUp(self):
        super().setUp()
        ss.create_account("anna", "annapass1")
        ss.create_account("bob", "bobpass12")

    def test_login_and_membership(self):
        l = self.link(access="users", users=["anna"])
        tok = self.csrf(l["token"])
        r = self.c.post(f"/s/{l['token']}/auth", data={"username": "bob", "password": "bobpass12", "csrf": tok})
        self.assertEqual(r.status_code, 200, "bob ist nicht freigegeben")
        r = self.c.post(f"/s/{l['token']}/auth", data={"username": "anna", "password": "annapass1", "csrf": tok})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get(f"/s/{l['token']}/d/a.jpg").status_code, 200)
        self.assertGreater(ss.get_account("anna")["last_login"], 0)

    def test_disabled_account_loses_access(self):
        l = self.link(access="users")
        tok = self.csrf(l["token"])
        self.c.post(f"/s/{l['token']}/auth", data={"username": "anna", "password": "annapass1", "csrf": tok})
        self.assertEqual(self.c.get(f"/s/{l['token']}/d/a.jpg").status_code, 200)
        ss.update_account("anna", {"enabled": False})
        self.assertEqual(self.c.get(f"/s/{l['token']}/d/a.jpg").status_code, 302)


class RateAndLogTest(Base):
    def test_request_flood_gets_429(self):
        l = self.link()
        for _ in range(ratelimit.REQ_PER_IP[0]):
            self.c.get("/healthz")
        r = self.c.get(f"/s/{l['token']}/")
        self.assertEqual(r.status_code, 429)
        self.assertIn("Retry-After", r.headers)

    def test_forwarded_ip_only_from_trusted_proxy(self):
        l = self.link()
        for _ in range(ratelimit.REQ_PER_IP[0]):
            self.c.get("/healthz", headers={"X-Forwarded-For": "8.8.8.8"}, environ_base={"REMOTE_ADDR": "192.168.1.50"})
        # untrusted source: XFF ignored, the flood counts against 192.168.1.50
        self.assertEqual(self.c.get("/healthz", environ_base={"REMOTE_ADDR": "192.168.1.50"}).status_code, 429)
        self.assertEqual(self.c.get("/healthz", environ_base={"REMOTE_ADDR": "192.168.1.51"}).status_code, 200)

    def test_access_log_has_no_token_or_absolute_path(self):
        l = self.link()
        self.c.get(f"/s/{l['token']}/d/a.jpg")
        rows = accesslog.tail(10)
        self.assertTrue(rows)
        dl = [r for r in rows if r["event"] == "download"][0]
        self.assertEqual(dl["link_id"], l["id"])
        self.assertEqual(dl["path"], "a.jpg")
        blob = repr(rows)
        self.assertNotIn(l["token"], blob)
        self.assertNotIn(self.fotos, blob)


if __name__ == "__main__":
    unittest.main()
