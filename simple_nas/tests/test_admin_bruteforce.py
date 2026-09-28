#!/usr/bin/env python3
"""Brute-Force-Schutz des Admin-Logins, echte Besucher-IP hinter Cloudflare,
admin_ingress_only."""
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import app as nas  # noqa: E402
import ratelimit  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

_REAL_SLEEP = time.sleep
LAN = "192.168.1.50"
NPM = "172.30.33.5"          # Nginx Proxy Manager im Supervisor-Netz


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (nas._OPTIONS, nas.DATA_DIR, dict(nas._admin_auth), nas.ADMIN_LIMITER)
        nas._OPTIONS = {"file_allowed_roots": [self.tmp]}
        nas.DATA_DIR = self.tmp
        nas._admin_auth.clear()
        nas._admin_auth.update({"enabled": True, "username": "admin",
                                "password_hash": generate_password_hash("richtig123")})
        self.clock = FakeClock()
        nas.ADMIN_LIMITER = ratelimit.Limiter(clock=self.clock)
        nas.time.sleep = lambda s: None
        nas.app.config["TESTING"] = True
        nas.app.secret_key = "test-key"
        self.c = nas.app.test_client()

    def tearDown(self):
        nas.time.sleep = _REAL_SLEEP
        nas._OPTIONS, nas.DATA_DIR, auth, nas.ADMIN_LIMITER = self._saved
        nas._admin_auth.clear()
        nas._admin_auth.update(auth)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def login(self, pw, ip=LAN, user="admin", headers=None):
        return self.c.post("/login", data={"username": user, "password": pw},
                           environ_base={"REMOTE_ADDR": ip}, headers=headers or {})


class BruteForceTest(Base):
    def test_lock_after_ten_failures_even_with_right_password(self):
        for _ in range(10):
            self.assertEqual(self.login("falsch").status_code, 200)
        r = self.login("richtig123")
        self.assertEqual(r.status_code, 429)
        self.assertTrue(int(r.headers["Retry-After"]) > 0)

    def test_lock_expires_and_escalates(self):
        for _ in range(10):
            self.login("falsch")
        self.clock.t += 15 * 60 + 1
        self.assertEqual(self.login("richtig123").status_code, 302)
        for _ in range(10):
            self.login("falsch", ip="192.168.1.51")
        self.assertEqual(self.login("x", ip="192.168.1.51").status_code, 429)

    def test_success_resets_own_counter(self):
        for _ in range(9):
            self.login("falsch")
        self.assertEqual(self.login("richtig123").status_code, 302)
        self.c = nas.app.test_client()
        self.assertEqual(self.login("falsch").status_code, 200)   # counter was reset

    def test_forged_forwarded_for_from_lan_does_not_help(self):
        for i in range(10):
            self.login("falsch", headers={"X-Forwarded-For": f"10.0.0.{i}"})
        self.assertEqual(self.login("richtig123", headers={"X-Forwarded-For": "10.9.9.9"}).status_code, 429)

    def test_cloudflare_visitor_ip_is_used_behind_trusted_proxy(self):
        # Zwei Besucher kommen beide ueber NPM - nur der Angreifer wird gesperrt.
        for _ in range(10):
            self.login("falsch", ip=NPM, headers={"CF-Connecting-IP": "203.0.113.7"})
        self.assertEqual(self.login("richtig123", ip=NPM,
                                    headers={"CF-Connecting-IP": "203.0.113.7"}).status_code, 429)
        self.assertEqual(self.login("richtig123", ip=NPM,
                                    headers={"CF-Connecting-IP": "198.51.100.2"}).status_code, 302)

    def test_distributed_attack_locks_the_username(self):
        for i in range(20):
            self.login("falsch", ip=NPM, headers={"CF-Connecting-IP": f"203.0.113.{i}"})
        self.assertEqual(self.login("richtig123", ip=NPM,
                                    headers={"CF-Connecting-IP": "198.51.100.2"}).status_code, 429)

    def test_ingress_is_never_locked(self):
        for i in range(20):
            self.login("falsch", ip=NPM, headers={"CF-Connecting-IP": f"203.0.113.{i}"})
        self.assertEqual(self.login("richtig123", ip=nas.INGRESS_IP).status_code, 302)


class IngressOnlyTest(Base):
    def test_off_by_default(self):
        self.assertNotEqual(self.c.get("/login", environ_base={"REMOTE_ADDR": LAN}).status_code, 403)

    def test_on_blocks_lan_and_proxy_but_not_ingress(self):
        nas._OPTIONS["admin_ingress_only"] = True
        self.assertEqual(self.c.get("/login", environ_base={"REMOTE_ADDR": LAN}).status_code, 403)
        self.assertEqual(self.c.get("/api/status", environ_base={"REMOTE_ADDR": NPM}).status_code, 403)
        self.assertEqual(self.c.get("/login", environ_base={"REMOTE_ADDR": nas.INGRESS_IP}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
