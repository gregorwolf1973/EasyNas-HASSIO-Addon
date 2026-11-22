#!/usr/bin/env python3
"""FTP: Samba logins, the same share rules as smb.conf, virtual root, recycle bin, lockouts."""
import ftplib
import io
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

import ftp  # noqa: E402
import webdav  # noqa: E402
from ratelimit import Limiter  # noqa: E402


def pdbedit_line(user, password):
    return f"{user}:1000:XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX:{webdav.nt_hash(password)}:[U          ]:LCT-6800AAAA:"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FtpServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        ftp.ROOT_DIR = os.path.join(self.tmp, "ftproot")
        for d in ("media", "media/camera", "docs", "privat", "outside"):
            os.makedirs(os.path.join(self.tmp, d))
        with open(os.path.join(self.tmp, "docs", "readme.txt"), "w") as f:
            f.write("hallo")
        with open(os.path.join(self.tmp, "outside", "secret.txt"), "w") as f:
            f.write("geheim")
        os.symlink(os.path.join(self.tmp, "outside"), os.path.join(self.tmp, "media", "escape"))
        ftp_on = {"services": {"ftp": True}}
        self.shares = [
            {"name": "medien", "path": os.path.join(self.tmp, "media"), "writable": True, "users": ["anna"], **ftp_on},
            {"name": "kamera", "path": os.path.join(self.tmp, "media", "camera"), "writable": True,
             "users": ["cam"], **ftp_on},
            {"name": "docs", "path": os.path.join(self.tmp, "docs"), "writable": False, "public": True, **ftp_on},
            {"name": "privat", "path": os.path.join(self.tmp, "privat"), "users": ["bob"], **ftp_on},
            {"name": "weg", "path": os.path.join(self.tmp, "fehlt"), "public": True, **ftp_on},
            {"name": "nurSMB", "path": os.path.join(self.tmp, "docs"), "public": True},   # FTP not ticked
        ]
        self.shares_file = os.path.join(self.tmp, "shares.json")
        with open(self.shares_file, "w") as f:
            json.dump(self.shares, f)
        users = "\n".join(pdbedit_line(u, u + "-pw") for u in ("anna", "bob", "cam"))
        self.limiter = Limiter()
        self.recycle = [True]
        self.events = []
        share_set = ftp.ShareSet(self.shares_file, lambda: json.load(open(self.shares_file)), lambda: [])
        auth = ftp.SambaAuthorizer(share_set, webdav.SambaPasswords(lambda: users), limiter=self.limiter,
                                   log=lambda e, **k: self.events.append((e, k)),
                                   recycle_on=lambda: self.recycle[0])
        self.log_file = os.path.join(self.tmp, "ftp.log")
        self.transfer_log = ftp.TransferLog(self.log_file)
        self.addon_lines = []
        handler = ftp.make_handler(auth, list(range(free_port(), free_port() + 1)),
                                   transfer_log=self.transfer_log, session_log=self.addon_lines.append)
        handler.auth_failed_timeout = 0.01
        self.port = free_port()
        self.server = ftp.serve(handler, self.port, host="127.0.0.1")
        self.addCleanup(self.server.close_all)

    def client(self, user, password=None):
        c = ftplib.FTP()
        self.addCleanup(c.close)
        try:
            c.connect("127.0.0.1", self.port, timeout=10)
            c.login(user, password if password is not None else user + "-pw")
        except Exception:
            c.close()
            raise
        return c

    def upload(self, c, path, data=b"jpg"):
        return c.storbinary(f"STOR {path}", io.BytesIO(data))

    def test_root_lists_only_allowed_and_available_shares(self):
        self.assertEqual(sorted(self.client("anna").nlst("/")), ["docs", "medien"])  # not nurSMB
        self.assertEqual(sorted(self.client("cam").nlst("/")), ["docs", "kamera"])
        lines = []
        self.client("bob").retrlines("LIST /", lines.append)
        self.assertEqual(sorted(line.split()[-1] for line in lines), ["docs", "privat"])
        self.assertTrue(all(line.startswith("d") for line in lines))

    def test_upload_like_the_camera(self):
        c = self.client("cam")
        c.cwd("/kamera")
        self.assertEqual(c.pwd(), "/kamera")
        c.mkd("20261103")
        c.mkd("20261103/0434D164")
        self.upload(c, "20261103/0434D164/185354_person.jpg", b"x" * 1000)
        target = os.path.join(self.tmp, "media", "camera", "20261103", "0434D164", "185354_person.jpg")
        self.assertEqual(os.path.getsize(target), 1000)
        c.cwd("20261103/0434D164")
        self.assertEqual(c.pwd(), "/kamera/20261103/0434D164")
        c.cwd("/")
        self.assertEqual(sorted(c.nlst()), ["docs", "kamera"])

    def test_nested_share_keeps_the_browsed_name(self):
        c = self.client("anna")
        c.cwd("/medien/camera")
        self.assertEqual(c.pwd(), "/medien/camera")

    def test_read_only_share_and_root_refuse_writes(self):
        c = self.client("anna")
        self.assertEqual(c.nlst("/docs"), ["readme.txt"])
        buf = io.BytesIO()
        c.retrbinary("RETR /docs/readme.txt", buf.write)
        self.assertEqual(buf.getvalue(), b"hallo")
        for action in (lambda: self.upload(c, "/docs/neu.txt"), lambda: c.delete("/docs/readme.txt"),
                       lambda: c.mkd("/docs/x"), lambda: self.upload(c, "/neu.txt"), lambda: c.mkd("/neu"),
                       lambda: c.rename("/medien", "/umbenannt"), lambda: c.rmd("/medien")):
            with self.assertRaises(ftplib.error_perm):
                action()
        self.assertEqual(sorted(os.listdir(os.path.join(self.tmp, "docs"))), ["readme.txt"])

    def test_foreign_share_and_traversal_are_unreachable(self):
        c = self.client("anna")
        for path in ("/privat", "/kamera", "/medien/../../outside", "/../outside", "/weg", "/nurSMB"):
            with self.assertRaises(ftplib.error_perm, msg=path):
                c.cwd(path)
        with self.assertRaises(ftplib.error_perm):
            c.cwd("/medien/escape")             # symlink out of the share
        with self.assertRaises(ftplib.error_perm):
            c.retrbinary("RETR /medien/escape/secret.txt", io.BytesIO().write)
        with self.assertRaises(ftplib.error_perm):
            self.upload(c, "/privat/x.txt")
        self.assertEqual(os.listdir(os.path.join(self.tmp, "privat")), [])

    def test_delete_goes_to_recycle_bin(self):
        c = self.client("anna")
        self.upload(c, "/medien/a.jpg")
        c.delete("/medien/a.jpg")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "media", ".recycle", "a.jpg")))
        self.upload(c, "/medien/a.jpg")
        c.delete("/medien/a.jpg")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "media", ".recycle", "Copy #1 of a.jpg")))
        self.recycle[0] = False
        self.upload(c, "/medien/b.jpg")
        c.delete("/medien/b.jpg")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "media", ".recycle", "b.jpg")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "media", "b.jpg")))

    def test_wrong_password_and_anonymous_fail_then_lock(self):
        with self.assertRaises(ftplib.error_perm):
            self.client("anna", "falsch")
        with self.assertRaises(ftplib.error_perm):
            self.client("anonymous", "x@y")
        self.assertEqual(self.events[0][0], "auth_fail")
        for _ in range(10):
            try:
                self.client("anna", "falsch")
            except (ftplib.error_perm, EOFError, ConnectionError):
                pass
        with self.assertRaises(ftplib.error_perm):
            self.client("anna")                 # right password, but locked
        self.assertIn("rate_limited", [e for e, _ in self.events])
        self.limiter.clear(prefix="authfail:")
        self.client("anna")

    def test_new_share_appears_without_restart(self):
        self.shares.append({"name": "neu", "path": os.path.join(self.tmp, "privat"), "public": True,
                            "services": {"ftp": True}})
        with open(self.shares_file, "w") as f:
            json.dump(self.shares, f)
        os.utime(self.shares_file, ns=(1, 1))   # a new mtime even within the same clock tick
        self.assertIn("neu", self.client("anna").nlst("/"))

    def wait_for_session(self, count=1):
        """on_disconnect runs a moment after the client has gone."""
        deadline = time.time() + 5
        while time.time() < deadline:
            sessions = [e for e in self.transfer_log.entries() if e["ev"] == "session"]
            if len(sessions) >= count:
                return sessions
            time.sleep(0.05)
        self.fail("no session summary in the FTP log")

    def test_activity_log(self):
        c = self.client("anna")
        self.upload(c, "/medien/a.jpg", b"x" * 1000)
        c.retrbinary("RETR /docs/readme.txt", lambda b: None)
        c.mkd("/medien/neu")
        with self.assertRaises(ftplib.error_perm):
            c.mkd("/medien/neu")                # exists: a probe, not an error
        with self.assertRaises(ftplib.error_perm):
            c.cwd("/medien/gibtsnicht")         # a real error
        c.delete("/medien/a.jpg")
        c.quit()
        session = self.wait_for_session()[0]
        events = self.transfer_log.entries()
        kinds = [e["ev"] for e in events]
        self.assertEqual(kinds[0], "login")
        self.assertEqual(events[0]["user"], "anna")
        self.assertEqual(events[0]["ip"], "127.0.0.1")
        up = next(e for e in events if e["ev"] == "upload")
        self.assertEqual((up["bytes"], up["ok"]), (1000, True))
        self.assertTrue(up["path"].endswith("/media/a.jpg"))
        down = next(e for e in events if e["ev"] == "download")
        self.assertEqual(down["bytes"], 5)
        self.assertIn("mkdir", kinds)
        self.assertIn("delete", kinds)
        errors = [e for e in events if e["ev"] == "error"]
        self.assertEqual(sorted(e["cmd"] for e in errors), ["CWD", "MKD"])
        self.assertTrue(all(e["code"] == 550 for e in errors))
        self.assertEqual((session["up_n"], session["up_bytes"], session["down_n"], session["down_bytes"]),
                         (1, 1000, 1, 5))
        self.assertEqual(session["errors"], 1)  # the existing folder does not count
        self.assertEqual(len(self.addon_lines), 1)
        self.assertIn("anna@127.0.0.1", self.addon_lines[0])
        with open(self.log_file) as f:
            self.assertNotIn("anna-pw", f.read())   # never the password

    def test_camera_session_stays_out_of_the_addon_log(self):
        os.makedirs(os.path.join(self.tmp, "media", "camera", "20261122"))
        c = self.client("cam")
        with self.assertRaises(ftplib.error_perm):
            c.mkd("/kamera/20261122")           # the camera creates its folder every time
        self.upload(c, "/kamera/20261122/1_person.jpg")
        c.quit()
        session = self.wait_for_session()[0]
        self.assertEqual((session["up_n"], session["errors"]), (1, 0))
        self.assertEqual(self.addon_lines, [])
        summary = self.transfer_log.summary(0)
        self.assertEqual([(r["user"], r["up_n"], r["up_bytes"], r["logins"]) for r in summary], [("cam", 1, 3, 1)])

    def test_failed_login_writes_no_password(self):
        with self.assertRaises(ftplib.error_perm):
            self.client("anna", "geheim123")
        time.sleep(0.3)
        text = open(self.log_file).read() if os.path.exists(self.log_file) else ""
        self.assertNotIn("geheim123", text)


class TransferLogTest(unittest.TestCase):
    def test_rotation_summary_and_clear(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        log = ftp.TransferLog(os.path.join(tmp, "ftp.log"), max_bytes=300)
        for i in range(10):
            log.write(ev="download", ip="192.168.178.50", user="gregor", path=f"/media/film{i}.mkv", bytes=1000)
        log.write(ev="upload", ip="192.168.178.118", user="kamera", path="/media/camera/x.jpg", bytes=10)
        self.assertTrue(os.path.exists(os.path.join(tmp, "ftp.log.1")))
        self.assertLess(len(log.entries()), 11)         # the oldest are gone
        self.assertEqual(log.tail(1)[0]["user"], "kamera")
        self.assertEqual(log.tail(5, event="upload")[0]["bytes"], 10)
        top = log.summary(0)[0]
        self.assertEqual((top["ip"], top["user"]), ("192.168.178.50", "gregor"))
        self.assertEqual(top["down_bytes"], 1000 * top["down_n"])
        self.assertEqual(log.summary(time.time() + 60), [])
        log.clear()
        self.assertEqual(log.entries(), [])

    def test_human_bytes(self):
        self.assertEqual(ftp.human_bytes(0), "0 B")
        self.assertEqual(ftp.human_bytes(1536), "1.5 KB")
        self.assertEqual(ftp.human_bytes(5 * 1024 ** 3), "5.0 GB")


class PassivePortsTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(ftp.parse_passive_ports("30000-30002"), [30000, 30001, 30002])
        self.assertEqual(len(ftp.parse_passive_ports("")), 11)
        self.assertEqual(len(ftp.parse_passive_ports("80-90")), 11)        # privileged: default
        self.assertEqual(len(ftp.parse_passive_ports("40000-30000")), 11)  # reversed: default


if __name__ == "__main__":
    unittest.main()
