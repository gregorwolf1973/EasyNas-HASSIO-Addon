#!/usr/bin/env python3
"""3.13.0: Time Machine je Freigabe (#11), Ingress-Port von Home Assistant (#10), XFS-Meldung (#16)."""
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import app as nas  # noqa: E402


class TimeMachineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = (nas._OPTIONS, nas.SHARES_FILE, nas.reload_samba, nas._safe, nas.subprocess.Popen, dict(nas._ADISK))
        nas._OPTIONS = {}
        nas.SHARES_FILE = os.path.join(self.tmp, "shares.json")
        nas.save_json(nas.SHARES_FILE, [])
        self.reloads = []
        nas.reload_samba = lambda: self.reloads.append(1)  # kein smbd auf der Testmaschine
        nas._safe = lambda path, roots=None: path
        nas.app.config["TESTING"] = True
        nas.app.secret_key = "test-key"
        self.c = nas.app.test_client()
        self.c.get("/api/shares")
        with self.c.session_transaction() as sess:
            self.csrf = sess.get("csrf", "")

    def tearDown(self):
        nas._OPTIONS, nas.SHARES_FILE, nas.reload_samba, nas._safe, nas.subprocess.Popen, adisk = self.saved
        nas._ADISK.clear()
        nas._ADISK.update(adisk)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def send(self, method, path, body):
        return self.c.open(path, method=method, json=body, headers={"X-CSRF-Token": self.csrf})

    def test_share_fields_are_stored_and_cleaned(self):
        path = os.path.join(self.tmp, "backup")
        r = self.send("POST", "/api/shares", {"name": "Mac", "path": path, "timemachine": 1, "timemachine_max_gb": "500"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual((r.get_json()["timemachine"], r.get_json()["timemachine_max_gb"]), (True, 500))
        r = self.send("PUT", "/api/shares/Mac", {"timemachine_max_gb": "viel"})
        self.assertEqual(r.get_json()["timemachine_max_gb"], 0, "Unsinn wird zu unbegrenzt")
        r = self.send("PUT", "/api/shares/Mac", {"timemachine": False, "timemachine_max_gb": -5})
        self.assertEqual((r.get_json()["timemachine"], r.get_json()["timemachine_max_gb"]), (False, 0))
        plain = self.send("POST", "/api/shares", {"name": "Fotos", "path": os.path.join(self.tmp, "f")}).get_json()
        self.assertEqual((plain["timemachine"], plain["timemachine_max_gb"]), (False, 0))

    def test_bonjour_records_for_time_machine_shares_only(self):
        shares = [{"name": "Mac", "timemachine": True}, {"name": "Fotos"},
                  {"name": "Alt", "timemachine": True, "writable": False}, {"name": "Mac2", "timemachine": True}]
        self.assertEqual(nas.timemachine_txt(shares),
                         ["sys=waMa=0,adVF=0x100", "dk0=adVN=Mac,adVF=0x82", "dk1=adVN=Mac2,adVF=0x82"])
        self.assertEqual(nas.timemachine_txt([{"name": "Fotos"}]), [])

    def test_publish_restarts_only_on_change(self):
        started = []

        class Proc:
            def __init__(self, args, **_kw):
                self.args, self.stopped = args, False
                started.append(self)

            def poll(self):
                return 0 if self.stopped else None

            def terminate(self):
                self.stopped = True

            def wait(self, timeout=None):
                return 0

        nas.subprocess.Popen = Proc
        nas._ADISK.update(proc=None, txt=None)
        nas.publish_timemachine()
        self.assertEqual(started, [], "ohne Time-Machine-Freigabe kein Eintrag")
        nas.save_json(nas.SHARES_FILE, [{"name": "Mac", "timemachine": True}])
        nas.publish_timemachine()
        nas.publish_timemachine()
        self.assertEqual(len(started), 1, "unverändert: läuft weiter")
        self.assertEqual(started[0].args[:5], ["avahi-publish", "-s", nas.NAS_NAME, "_adisk._tcp", "9"])
        nas.save_json(nas.SHARES_FILE, [])
        nas.publish_timemachine()
        self.assertTrue(started[0].stopped)
        self.assertIsNone(nas._ADISK["proc"])

    def test_recycle_overview_marks_time_machine(self):
        path = os.path.join(self.tmp, "mac")
        os.makedirs(path)
        nas.save_json(nas.SHARES_FILE, [{"name": "Mac", "path": path, "timemachine": True}])
        self.assertTrue(self.c.get("/api/settings/recycle").get_json()["shares"][0]["timemachine"])


class MountErrorTest(unittest.TestCase):
    def test_unknown_filesystem_gets_a_clear_message(self):
        body, status = nas.mount_error("mount: /media/platte: unknown filesystem type 'xfs'.")
        self.assertEqual((status, body["code"], body["fstype"]), (422, "fs_unsupported", "xfs"))
        self.assertIn("ext4", body["error"])

    def test_other_errors_pass_through(self):
        self.assertEqual(nas.mount_error("wrong fs type, bad option"), ({"error": "wrong fs type, bad option"}, 500))
        self.assertEqual(nas.mount_error(""), ({"error": "Mount fehlgeschlagen"}, 500))


class IngressPortTest(unittest.TestCase):
    def test_ports(self):
        self.assertEqual(nas.listen_ports(8100, 0), [8100])
        self.assertEqual(nas.listen_ports(8100, 8100), [8100])
        self.assertEqual(nas.listen_ports(8100, 63412), [8100, 63412])

    def test_env_port(self):
        for value, expected in (("63412", 63412), ("", 0), ("null", 0), (None, 0)):
            if value is None:
                os.environ.pop("SIMPLE_NAS_TEST_PORT", None)
            else:
                os.environ["SIMPLE_NAS_TEST_PORT"] = value
            self.assertEqual(nas._env_port("SIMPLE_NAS_TEST_PORT"), expected)
        os.environ.pop("SIMPLE_NAS_TEST_PORT", None)

    def test_busy_web_port_still_serves_ingress(self):
        from waitress import create_server

        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        ingress = free.getsockname()[1]
        free.close()
        try:
            server, ports = nas.make_server(nas.app, busy.getsockname()[1], ingress, create_server, host="127.0.0.1")
            self.assertEqual(ports, [ingress])
            server.close()
            with self.assertRaises(OSError):  # ohne Ingress-Port bleibt es ein Fehler
                nas.make_server(nas.app, busy.getsockname()[1], 0, create_server, host="127.0.0.1")
        finally:
            busy.close()

    def test_both_ports(self):
        from waitress import create_server

        ports = []
        for _ in range(2):
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            ports.append(probe.getsockname()[1])
            probe.close()
        server, used = nas.make_server(nas.app, ports[0], ports[1], create_server, host="127.0.0.1")
        self.assertEqual(used, ports)
        server.close()


if __name__ == "__main__":
    unittest.main()
