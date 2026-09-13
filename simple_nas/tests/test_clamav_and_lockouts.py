#!/usr/bin/env python3
"""ClamAV protocol against a fake clamd, and the brute-force hardening:
escalating lockouts, scanner bans, minimum link password length."""
import io
import os
import shutil
import socketserver
import struct
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import app as nas  # noqa: E402
import clamav  # noqa: E402
import ratelimit  # noqa: E402
import share_web  # noqa: E402
import sharing_store as ss  # noqa: E402


class FakeClamd(socketserver.ThreadingTCPServer):
    """Speaks just enough clamd: zPING, zINSTREAM, zSCAN. Verdict rules:
    EICAR -> FOUND, stream > max_stream -> size limit, path with 'bad' -> FOUND."""
    allow_reuse_address = True
    max_stream = 10 * 1024 * 1024

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            cmd = b""
            while not cmd.endswith(b"\0"):
                b = self.rfile.read(1)
                if not b:
                    return
                cmd += b
            cmd = cmd.rstrip(b"\0")
            if cmd == b"zPING":
                self.wfile.write(b"PONG\0")
            elif cmd == b"zINSTREAM":
                data, total = b"", 0
                while True:
                    hdr = self.rfile.read(4)
                    n = struct.unpack("!I", hdr)[0]
                    if n == 0:
                        break
                    chunk = self.rfile.read(n)
                    total += n
                    if total > self.server.max_stream:
                        self.wfile.write(b"INSTREAM size limit exceeded. ERROR\0")
                        self.wfile.flush()
                        # Real clamd stops reading here; draining keeps the
                        # reply out of a reset so this test stays decidable.
                        self.connection.settimeout(0.5)
                        try:
                            while self.rfile.read(4096):
                                pass
                        except OSError:
                            pass
                        return
                    data += chunk
                self.wfile.write(b"stream: Eicar-Test-Signature FOUND\0" if clamav.EICAR in data else b"stream: OK\0")
            elif cmd.startswith(b"zSCAN "):
                path = cmd[6:].decode()
                self.wfile.write((path + ": Eicar-Test-Signature FOUND\0").encode() if "bad" in path else (path + ": OK\0").encode())
            else:
                self.wfile.write(b"UNKNOWN COMMAND\0")

    def __init__(self):
        super().__init__(("127.0.0.1", 0), self.Handler)


class ClamavProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = FakeClamd()
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_ping(self):
        self.assertTrue(clamav.ping("127.0.0.1", self.port))
        self.assertFalse(clamav.ping("127.0.0.1", 1))

    def test_stream_verdicts(self):
        self.assertEqual(clamav.scan_stream(io.BytesIO(b"harmless"), "127.0.0.1", self.port), ("clean", ""))
        v, d = clamav.scan_stream(io.BytesIO(clamav.EICAR), "127.0.0.1", self.port)
        self.assertEqual((v, d), ("infected", "Eicar-Test-Signature"))

    def test_self_test(self):
        ok, msg = clamav.self_test("127.0.0.1", self.port)
        self.assertTrue(ok, msg)
        ok, msg = clamav.self_test("127.0.0.1", 1)
        self.assertFalse(ok)

    def test_large_file_falls_back_to_path_scan(self):
        self.srv.max_stream = 100
        try:
            tmp = tempfile.mkdtemp()
            good = os.path.join(tmp, "big.bin")
            with open(good, "wb") as f:
                f.write(b"x" * 500)
            self.assertEqual(clamav.scan_file(good, "127.0.0.1", self.port)[0], "clean")
            bad = os.path.join(tmp, "bad.bin")
            with open(bad, "wb") as f:
                f.write(b"x" * 500)
            self.assertEqual(clamav.scan_file(bad, "127.0.0.1", self.port)[0], "infected")
            self.assertEqual(clamav.scan_file(good, "127.0.0.1", self.port, path_fallback=False)[0], "too_large")
        finally:
            self.srv.max_stream = 10 * 1024 * 1024
            shutil.rmtree(tmp, ignore_errors=True)

    def test_verdict_survives_a_hangup_mid_stream(self):
        """clamd answers and closes the moment StreamMaxLength is exceeded,
        while we are still sending. On a reset the reply can be lost with the
        receive buffer - the upload must still end in a real verdict via the
        path scan, not in a generic error that on_error=reject would turn into
        a refused upload."""
        class RudeClamd(socketserver.ThreadingTCPServer):
            allow_reuse_address = True

            class Handler(socketserver.StreamRequestHandler):
                def handle(self):
                    cmd = b""
                    while not cmd.endswith(b"\0"):
                        ch = self.rfile.read(1)
                        if not ch:
                            return
                        cmd += ch
                    cmd = cmd.rstrip(b"\0")
                    if cmd == b"zINSTREAM":
                        self.rfile.read(4)                      # one length header
                        self.wfile.write(b"INSTREAM size limit exceeded. ERROR\0")
                        self.wfile.flush()
                        self.connection.close()                 # hang up mid-stream
                    elif cmd.startswith(b"zSCAN "):
                        path = cmd[6:].decode()
                        verdict = ": Eicar-Test-Signature FOUND\0" if "bad" in path else ": OK\0"
                        self.wfile.write((path + verdict).encode())

            def __init__(self):
                super().__init__(("127.0.0.1", 0), self.Handler)

        srv = RudeClamd()
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        tmp = tempfile.mkdtemp()
        try:
            for name, expected in (("good.bin", "clean"), ("bad.bin", "infected")):
                p = os.path.join(tmp, name)
                with open(p, "wb") as f:
                    f.write(b"x" * (clamav.CHUNK * 4))
                self.assertEqual(clamav.scan_file(p, "127.0.0.1", port, timeout=5)[0], expected)
            # Without the fallback the answer depends on whether the reply
            # survived the reset - both outcomes are honest, and neither may
            # be a verdict about the file itself.
            p = os.path.join(tmp, "good.bin")
            v = clamav.scan_file(p, "127.0.0.1", port, timeout=5, path_fallback=False)[0]
            self.assertIn(v, ("error", "too_large"))
        finally:
            srv.shutdown()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_hook_policy(self):
        # Policy mapping is tested with a stubbed transport: writing EICAR to
        # disk makes the developer machine's own antivirus grab the file.
        opts = {"share_clamav_enabled": True, "share_clamav_host": "127.0.0.1", "share_clamav_port": self.port}
        real = clamav.scan_file
        try:
            for verdict, on_error, on_large, expect in (
                ("infected", "reject", "accept", "infected"),
                ("clean", "reject", "accept", "clean"),
                ("error", "reject", "accept", "error"),
                ("error", "accept", "accept", "clean"),
                ("too_large", "reject", "accept", "clean"),
                ("too_large", "reject", "reject", "error"),
            ):
                clamav.scan_file = lambda *a, v=verdict, **k: (v, "detail")
                opts["share_clamav_on_error"], opts["share_clamav_large_file"] = on_error, on_large
                hook = clamav.make_hook(lambda k, d=None: opts.get(k, d))
                self.assertEqual(hook("/x")[0], expect, (verdict, on_error, on_large))
        finally:
            clamav.scan_file = real
        self.assertIsNone(clamav.make_hook(lambda k, d=None: {"share_clamav_enabled": False}.get(k, d)))
        # the real transport end to end, with a harmless file on disk
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "ok.txt")
        with open(p, "wb") as f:
            f.write(b"harmless")
        self.assertEqual(clamav.make_hook(lambda k, d=None: opts.get(k, d))(p)[0], "clean")
        shutil.rmtree(tmp, ignore_errors=True)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class LockoutTest(unittest.TestCase):
    def test_escalation_and_decay(self):
        clk = FakeClock()
        lim = ratelimit.Limiter(clock=clk)
        d1 = lim.lock("k", 900, 86400)
        self.assertEqual(d1, 900)
        self.assertTrue(lim.banned("k"))
        clk.t += 901
        self.assertFalse(lim.banned("k"))
        self.assertEqual(lim.lock("k", 900, 86400), 1800)       # second offence doubles
        clk.t += 1801
        self.assertEqual(lim.lock("k", 900, 86400), 3600)
        for _ in range(10):
            clk.t += 100000
            dur = lim.lock("k", 900, 86400)
        self.assertEqual(dur, 900, "nach 24 h Ruhe faengt die Eskalation von vorn an")
        lim.clear(key="k")
        self.assertFalse(lim.banned("k"))

    def test_cap(self):
        lim = ratelimit.Limiter(clock=FakeClock())
        for _ in range(12):
            dur = lim.lock("k", 900, 86400)
        self.assertEqual(dur, 86400)


class SiteHardeningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.media = os.path.join(self.tmp, "media")
        self.fotos = os.path.join(self.media, "fotos")
        os.makedirs(self.fotos)
        with open(os.path.join(self.fotos, "a.txt"), "w") as f:
            f.write("x")
        self.clock = FakeClock()
        self._saved = (ratelimit.LIMITER, nas._OPTIONS, nas.DATA_DIR)
        ratelimit.LIMITER = ratelimit.Limiter(clock=self.clock)
        share_web.LIMITER = ratelimit.LIMITER
        opts = {"share_allowed_roots": [self.media], "share_cookie_secure": False, "share_trusted_proxies": ["127.0.0.1"]}
        ss.init(self.tmp, nas.load_json, nas.save_json)
        nas._OPTIONS, nas.DATA_DIR = dict(opts), self.tmp
        self.app = share_web.create_share_app(lambda k, d=None: opts.get(k, d), lambda: [], lambda: [self.media], self.tmp)
        self.app.config["TESTING"] = True
        self.c = self.app.test_client()
        share_web.time.sleep = lambda s: None

    def tearDown(self):
        ratelimit.LIMITER, nas._OPTIONS, nas.DATA_DIR = self._saved
        share_web.LIMITER = ratelimit.LIMITER
        ss._index["mtime"] = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_link_password_minimum_length(self):
        with self.assertRaises(ss.ShareError):
            ss.create_link({"name": "x", "root": self.fotos, "access": "password", "password": "1234"}, allowed_roots=[self.media])
        ss.create_link({"name": "x", "root": self.fotos, "access": "password", "password": "12345678"}, allowed_roots=[self.media])

    def test_token_scanner_gets_banned(self):
        for _ in range(ratelimit.SCAN_PER_IP[0]):
            self.assertEqual(self.c.get(f"/s/{ss.new_token()}/").status_code, 404)
        r = self.c.get("/healthz")
        self.assertEqual(r.status_code, 429, "nach 20 unbekannten Links ist die Adresse gesperrt")
        self.assertIn("Retry-After", r.headers)
        self.clock.t += ratelimit.LOCK_BASE + 1
        self.assertEqual(self.c.get("/healthz").status_code, 200)

    def test_repeat_offender_waits_longer_each_time(self):
        l = ss.create_link({"name": "x", "root": self.fotos, "access": "password", "password": "geheim123"}, allowed_roots=[self.media])
        self.c.get(f"/s/{l['token']}/")
        with self.c.session_transaction() as s:
            tok = s["csrf"]

        def burst():
            for _ in range(ratelimit.AUTHFAIL_IP[0]):
                self.c.post(f"/s/{l['token']}/auth", data={"password": "x", "csrf": tok})
        burst()
        self.assertTrue(ratelimit.LIMITER.banned("authfail:ip:127.0.0.1"))
        self.clock.t += ratelimit.LOCK_BASE + 1               # 15 min: first lock over
        self.assertFalse(ratelimit.LIMITER.banned("authfail:ip:127.0.0.1"))
        burst()
        self.assertEqual(ratelimit.LIMITER.ban_remaining("authfail:ip:127.0.0.1"), 2 * ratelimit.LOCK_BASE)
        # correct password during the lock is still refused
        r = self.c.post(f"/s/{l['token']}/auth", data={"password": "geheim123", "csrf": tok})
        self.assertIn('class="warn"', r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
