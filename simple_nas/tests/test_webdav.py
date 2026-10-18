#!/usr/bin/env python3
"""WebDAV (issue #12): Samba logins, the same share rules as smb.conf, recycle bin, lockouts."""
import base64
import http.client
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import webdav  # noqa: E402
from ratelimit import Limiter  # noqa: E402
from werkzeug.test import Client  # noqa: E402


def pdbedit_line(user, password, flags="[U          ]"):
    return f"{user}:1000:XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX:{webdav.nt_hash(password)}:{flags}:LCT-6800AAAA:"


class HashTest(unittest.TestCase):
    def test_md4_reference_values(self):
        # RFC 1320, Anhang A.5
        for text, digest in (("", "31d6cfe0d16ae931b73c59d7e0c089c0"),
                             ("a", "bde52cb31de33e46245e05fbdbd6fb24"),
                             ("abc", "a448017aaf21d8525fc10ae87aa6729d"),
                             ("message digest", "d9130a8164549fe818874806e1c7014b"),
                             ("1234567890" * 8, "e33b4ddc9c38f2199c3e7b164fcc0536")):
            self.assertEqual(webdav.md4(text.encode()).hex(), digest, text)

    def test_nt_hash_like_samba(self):
        self.assertEqual(webdav.nt_hash("password"), "8846F7EAEE8FB117AD06BDD830B7586C")
        self.assertEqual(webdav.nt_hash(""), "31D6CFE0D16AE931B73C59D7E0C089C0")

    def test_pdbedit_parsing_skips_disabled_and_broken(self):
        out = "\n".join([pdbedit_line("anna", "x"), pdbedit_line("alt", "y", "[DU         ]"),
                         "kaputt:1000:XXX:NO PASSWORDXXXXXXXXXXXXXXXXXXXXX:[U  ]:LCT-0:", "Muell"])
        self.assertEqual(list(webdav.parse_pdbedit(out)), ["anna"])

    def test_password_cache(self):
        calls = []
        now = [0.0]

        def reader():
            calls.append(1)
            return pdbedit_line("anna", "geheim")

        pw = webdav.SambaPasswords(reader, ttl=30, clock=lambda: now[0])
        self.assertTrue(pw.check("anna", "geheim"))
        self.assertFalse(pw.check("anna", "falsch"))
        self.assertFalse(pw.check("niemand", "geheim"))
        self.assertEqual(len(calls), 1)
        now[0] = 31
        pw.check("anna", "geheim")
        pw.invalidate()
        pw.check("anna", "geheim")
        self.assertEqual(len(calls), 3)


class RulesTest(unittest.TestCase):
    def test_share_rules_like_smb_conf(self):
        groups = [{"name": "familie", "members": ["ben"]}]
        self.assertTrue(webdav.share_allows({}, "anna", groups), "nichts eingetragen: alle Samba-Benutzer")
        self.assertTrue(webdav.share_allows({"public": True, "users": ["x"]}, "anna", groups))
        self.assertTrue(webdav.share_allows({"users": ["anna"]}, "anna", groups))
        self.assertFalse(webdav.share_allows({"users": ["anna"]}, "ben", groups))
        self.assertTrue(webdav.share_allows({"groups": ["familie"]}, "ben", groups))
        self.assertFalse(webdav.share_allows({"groups": ["familie"]}, "anna", groups))

    def test_client_ip_only_trusts_listed_proxies(self):
        nets = webdav.trusted_networks(["172.30.32.0/23", "kaputt"])
        env = {"REMOTE_ADDR": "172.30.33.5", "HTTP_X_FORWARDED_FOR": "1.2.3.4, 9.9.9.9"}
        self.assertEqual(webdav.client_ip(env, nets), "9.9.9.9")
        self.assertEqual(webdav.client_ip(dict(env, HTTP_CF_CONNECTING_IP="5.6.7.8"), nets), "5.6.7.8")
        self.assertEqual(webdav.client_ip(dict(env, REMOTE_ADDR="192.168.1.9"), nets), "192.168.1.9")

    def test_mount_points_only_count_when_mounted(self):
        tmp = tempfile.mkdtemp()
        try:
            self.assertTrue(webdav.share_available(tmp, mounts=set()))
            self.assertFalse(webdav.share_available(os.path.join(tmp, "fehlt"), mounts=set()))
        finally:
            shutil.rmtree(tmp)
        self.assertFalse(webdav.share_available("/mnt", mounts=set()))


class DavTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.paths = {}
        shares = []
        for name, extra in (("Fotos", {"users": ["anna"]}), ("Alle", {"writable": False}), ("Mac", {"timemachine": True})):
            path = os.path.join(self.tmp, name.lower())
            os.makedirs(path)
            self.paths[name] = path
            shares.append({"name": name, "path": path, **extra})
        self.shares_file = os.path.join(self.tmp, "shares.json")
        with open(self.shares_file, "w") as f:
            json.dump(shares, f)
        with open(os.path.join(self.paths["Alle"], "info.txt"), "w") as f:
            f.write("hallo")
        self.recycle = [True]
        self.limiter = Limiter()
        self.logged = []
        passwords = webdav.SambaPasswords(lambda: "\n".join([pdbedit_line("anna", "pw-anna"),
                                                            pdbedit_line("ben", "pw-ben")]))
        self.app = webdav.ReloadingApp(
            self.shares_file, lambda: json.load(open(self.shares_file)),
            passwords=passwords, load_groups=lambda: [], recycle_on=lambda: self.recycle[0],
            limiter=self.limiter, log=lambda event, **f: self.logged.append((event, f)))
        self.c = Client(self.app)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def req(self, method, path, user="anna", password="pw-anna", **kw):
        headers = dict(kw.pop("headers", {}))
        if user:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        return self.c.open(path, method=method, headers=headers, **kw)

    def test_login_required(self):
        self.assertEqual(self.req("GET", "/Alle/info.txt", user=None).status_code, 401)
        self.assertEqual(self.req("GET", "/Alle/info.txt", password="falsch").status_code, 401)
        r = self.req("GET", "/Alle/info.txt")
        self.assertEqual((r.status_code, r.data), (200, b"hallo"))

    def test_root_lists_only_allowed_shares(self):
        def names(user, password):
            body = self.req("PROPFIND", "/", user, password, headers={"Depth": "1"}).get_data(as_text=True)
            return {n for n in ("Fotos", "Alle", "Mac") if f"/{n}/" in body or f"/{n}<" in body}
        self.assertEqual(names("anna", "pw-anna"), {"Fotos", "Alle", "Mac"})
        self.assertEqual(names("ben", "pw-ben"), {"Alle", "Mac"})
        self.assertEqual(self.req("GET", "/Fotos/", "ben", "pw-ben").status_code, 401)
        self.assertEqual(self.req("GET", "/Gibtsnicht/", "ben", "pw-ben").status_code, 404)
        self.assertEqual(self.limiter.count("authfail:ip:127.0.0.1", 900), 0, "richtiges Passwort sperrt nie")

    def test_write_read_only_and_recycle(self):
        self.assertEqual(self.req("PUT", "/Fotos/neu.txt", data=b"1").status_code, 201)
        self.assertEqual(self.req("PUT", "/Alle/neu.txt", data=b"1").status_code, 403)
        self.assertEqual(self.req("DELETE", "/Fotos/neu.txt").status_code, 204)
        bin_ = os.path.join(self.paths["Fotos"], ".recycle")
        self.assertTrue(os.path.isfile(os.path.join(bin_, "neu.txt")))
        self.req("PUT", "/Fotos/neu.txt", data=b"2")
        self.req("DELETE", "/Fotos/neu.txt")
        self.assertTrue(os.path.isfile(os.path.join(bin_, "Copy #1 of neu.txt")))
        # Ordner wandern als Ganzes in den Papierkorb
        self.assertEqual(self.req("MKCOL", "/Fotos/Urlaub").status_code, 201)
        self.req("PUT", "/Fotos/Urlaub/a.jpg", data=b"x")
        self.assertEqual(self.req("DELETE", "/Fotos/Urlaub").status_code, 204)
        self.assertTrue(os.path.isfile(os.path.join(bin_, "Urlaub", "a.jpg")))
        # im Papierkorb, temporäre Dateien und ohne Papierkorb: endgültig
        self.req("DELETE", "/Fotos/.recycle/neu.txt")
        self.assertFalse(os.path.exists(os.path.join(bin_, "neu.txt")))
        self.req("PUT", "/Fotos/x.tmp", data=b"t")
        self.req("DELETE", "/Fotos/x.tmp")
        self.assertFalse(os.path.exists(os.path.join(bin_, "x.tmp")))
        self.recycle[0] = False
        self.req("PUT", "/Fotos/weg.txt", data=b"w")
        self.req("DELETE", "/Fotos/weg.txt")
        self.assertFalse(os.path.exists(os.path.join(bin_, "weg.txt")))

    def test_time_machine_share_has_no_bin(self):
        self.req("PUT", "/Mac/band", data=b"b")
        self.req("DELETE", "/Mac/band")
        self.assertFalse(os.path.exists(os.path.join(self.paths["Mac"], ".recycle")))

    def test_symlinks_and_traversal_stay_inside(self):
        outside = os.path.join(self.tmp, "geheim")
        os.makedirs(outside)
        with open(os.path.join(outside, "pw.txt"), "w") as f:
            f.write("geheim")
        os.symlink(outside, os.path.join(self.paths["Alle"], "link"))
        listing = self.req("PROPFIND", "/Alle/", headers={"Depth": "1"}).get_data(as_text=True)
        self.assertNotIn("link", listing)
        self.assertIn(self.req("GET", "/Alle/link/pw.txt").status_code, (403, 404))
        self.assertIn(self.req("GET", "/Alle/../geheim/pw.txt").status_code, (403, 404))

    def test_lockout_after_failures(self):
        for _ in range(10):
            self.req("GET", "/Alle/info.txt", password="falsch")
        self.assertEqual(self.req("GET", "/Alle/info.txt").status_code, 401, "gesperrt, auch mit richtigem Passwort")
        events = [e for e, _ in self.logged]
        self.assertIn("auth_fail", events)
        self.assertIn("rate_limited", events)

    def test_new_share_appears_without_restart(self):
        path = os.path.join(self.tmp, "neu")
        os.makedirs(path)
        shares = json.load(open(self.shares_file))
        shares.append({"name": "Neu", "path": path})
        time.sleep(0.01)
        with open(self.shares_file, "w") as f:
            json.dump(shares, f)
        self.assertEqual(self.req("PROPFIND", "/Neu/", headers={"Depth": "0"}).status_code, 207)
        self.assertIn("Neu", self.app.names())


class ServerTest(unittest.TestCase):
    def test_chunked_upload_like_macos_finder(self):
        tmp = tempfile.mkdtemp()
        try:
            share = os.path.join(tmp, "s")
            os.makedirs(share)
            shares_file = os.path.join(tmp, "shares.json")
            with open(shares_file, "w") as f:
                json.dump([{"name": "S", "path": share}], f)
            app = webdav.ReloadingApp(shares_file, lambda: json.load(open(shares_file)),
                                      passwords=webdav.SambaPasswords(lambda: pdbedit_line("anna", "pw")),
                                      load_groups=lambda: [], recycle_on=lambda: True, limiter=Limiter())
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            probe.close()
            server = webdav.serve(app, port, host="127.0.0.1")
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                auth = "Basic " + base64.b64encode(b"anna:pw").decode()
                conn.putrequest("PUT", "/S/gross.bin")
                conn.putheader("Authorization", auth)
                conn.putheader("Transfer-Encoding", "chunked")
                conn.endheaders()
                for part in (b"a" * 70000, b"b" * 12345):
                    conn.send(f"{len(part):x}\r\n".encode() + part + b"\r\n")
                conn.send(b"0\r\n\r\n")
                self.assertEqual(conn.getresponse().status, 201)
                with open(os.path.join(share, "gross.bin"), "rb") as f:
                    data = f.read()
                self.assertEqual(data, b"a" * 70000 + b"b" * 12345)
            finally:
                server.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class AppIntegrationTest(unittest.TestCase):
    def setUp(self):
        import app as nas
        self.nas = nas
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "f"))
        self.saved = (nas._OPTIONS, nas.SHARES_FILE, dict(nas._WEBDAV))
        nas.SHARES_FILE = os.path.join(self.tmp, "shares.json")
        nas.save_json(nas.SHARES_FILE, [{"name": "Fotos", "path": os.path.join(self.tmp, "f")}])
        nas.app.config["TESTING"] = True
        nas.app.secret_key = "test-key"
        self.c = nas.app.test_client()

    def tearDown(self):
        nas = self.nas
        if nas._WEBDAV["server"] is not None:
            nas._WEBDAV["server"].close()
        nas._OPTIONS, nas.SHARES_FILE, webdav_state = self.saved
        nas._WEBDAV.clear()
        nas._WEBDAV.update(webdav_state)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def free_port(self):
        probe = socket.socket()
        probe.bind(("0.0.0.0", 0))
        port = probe.getsockname()[1]
        probe.close()
        return port

    def test_status_off_on_and_busy_port(self):
        nas = self.nas
        nas._OPTIONS = {}
        self.assertEqual(self.c.get("/api/settings/webdav").get_json()["enabled"], False)
        port = self.free_port()
        nas._OPTIONS = {"webdav_enabled": True, "webdav_port": port}
        self.assertTrue(nas.start_webdav())
        body = self.c.get("/api/settings/webdav").get_json()
        self.assertEqual((body["running"], body["port"], body["shares"]), (True, port, ["Fotos"]))
        nas._webdav_passwords_changed()                 # darf nicht scheitern
        nas._WEBDAV["server"].close()
        nas._WEBDAV.update(server=None, app=None)

        busy = socket.socket()
        busy.bind(("0.0.0.0", 0))
        busy.listen()
        try:
            nas._OPTIONS["webdav_port"] = busy.getsockname()[1]
            self.assertFalse(nas.start_webdav())
            body = self.c.get("/api/settings/webdav").get_json()
            self.assertFalse(body["running"])
            self.assertIn("belegt", body["error"])
        finally:
            busy.close()


if __name__ == "__main__":
    unittest.main()
