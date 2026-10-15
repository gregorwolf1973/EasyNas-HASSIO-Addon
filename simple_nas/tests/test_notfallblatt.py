#!/usr/bin/env python3
"""Emergency sheet: the archive password is on it, provider secrets are not,
and the restore instructions fit the target."""
import datetime
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import notfallblatt  # noqa: E402

NOW = datetime.datetime(2026, 10, 15, 8, 30)


def job(**kw):
    j = {"id": "ab12cd34", "name": "Hetzner", "engine": "restic", "encrypt": True,
         "sources": ["/share", "/media/fotos"],
         "target": {"provider": "hetzner", "host": "u123.your-storagebox.de", "port": 22,
                    "user": "u123", "password": "boxpass"},
         "remote_path": "simplenas", "password": "Il1O0-x y",
         "last_backup": {"at": "2026-10-14T03:00:12", "status": "ok"}}
    j.update(kw)
    return j


class SheetTest(unittest.TestCase):
    def test_hetzner_restic(self):
        page = notfallblatt.render(job(), "de", NOW)
        self.assertIn("Notfallblatt – Hetzner", page)
        self.assertIn("15.10.2026 08:30", page)
        # the archive password, character by character with hints
        self.assertIn("Il1O0-x y", page)
        for hint in ("groß i", "klein L", "Eins", "groß O", "Null", "Leerzeichen"):
            self.assertIn(hint, page)
        self.assertIn("9 Zeichen", page)
        # the box password is NOT printed
        self.assertNotIn("boxpass", page)
        # values for the Notfall-Paket
        self.assertIn("u123.your-storagebox.de", page)
        self.assertIn("WIEDERHERSTELLEN-Windows.cmd", page)
        # manual way: config without secrets, restic over rclone
        self.assertIn("[t_nas]", page)
        self.assertIn("rclone:t_nas:simplenas", page)
        self.assertIn("--include /share", page)
        self.assertIn("simplenas / simplenas", page)
        self.assertIn("2026-10-14 03:00:12", page)

    def test_html_is_escaped(self):
        page = notfallblatt.render(job(name="<b>x</b>", password="a<b>&\"c'd!"), "de", NOW)
        self.assertNotIn("<b>x</b>", page)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)
        self.assertIn("a&lt;b&gt;&amp;&quot;c&#x27;d!", page)

    def test_s3_secrets_never_printed(self):
        j = job(target={"provider": "s3", "s3_provider": "Other", "endpoint": "fsn1.your-objectstorage.com",
                        "access_key": "AKIAPUBLIC", "secret_key": "S3CRET-KEY", "bucket": "nas"})
        page = notfallblatt.render(j, "de", NOW)
        self.assertNotIn("S3CRET-KEY", page)
        self.assertIn("AKIAPUBLIC", page)           # the key id is not secret, and needed
        self.assertIn("rclone:t_nas:nas/simplenas", page)
        self.assertIn("kennt nur restic auf einer Hetzner", page)   # no package for S3

    def test_sftp_key_not_printed_and_no_package(self):
        j = job(target={"provider": "sftp", "host": "nas.example.org", "port": 22, "user": "me",
                        "password": "", "key_pem": "-----BEGIN OPENSSH PRIVATE KEY-----\nGEHEIM\n-----END"})
        page = notfallblatt.render(j, "de", NOW)
        self.assertNotIn("GEHEIM", page)
        self.assertNotIn("WIEDERHERSTELLEN-Windows.cmd", page)

    def test_custom_rclone_conf_not_printed(self):
        j = job(target={"provider": "rclone", "rclone_conf": "[x]\ntype = drive\ntoken = GEHEIMTOKEN"})
        page = notfallblatt.render(j, "de", NOW)
        self.assertNotIn("GEHEIMTOKEN", page)
        self.assertIn("eigene rclone-Konfiguration", page)

    def test_rclone_crypt(self):
        page = notfallblatt.render(job(engine="rclone", encrypt=True), "de", NOW)
        self.assertIn("[c_nas]", page)
        self.assertIn("type = crypt", page)
        self.assertIn("rclone copy c_nas:", page)
        self.assertIn("Il1O0-x y", page)

    def test_rclone_plain_has_no_password(self):
        page = notfallblatt.render(job(engine="rclone", encrypt=False, password=""), "de", NOW)
        self.assertIn("es gibt kein Archiv-Passwort", page)
        self.assertNotIn("[c_nas]", page)

    def test_local_targets(self):
        page = notfallblatt.render(job(target={"provider": "local", "path": "/media/usb2"}), "de", NOW)
        repo = os.path.join("/media/usb2", "simplenas")      # "/" in the add-on (Linux)
        self.assertIn(f"restic -r &quot;{repo}&quot; snapshots", page)
        self.assertNotIn("RCLONE_CONFIG", page)
        page = notfallblatt.render(job(engine="rclone", encrypt=False, password="",
                                       target={"provider": "local", "path": "/media/usb2"}), "de", NOW)
        self.assertIn("einfach kopieren", page)

    def test_english(self):
        page = notfallblatt.render(job(), "en", NOW)
        self.assertIn("Emergency sheet – Hetzner", page)
        self.assertIn("2026-10-15 08:30", page)
        self.assertIn("upper i", page)
        self.assertIn('lang="en"', page)
        self.assertIn("Notfallblatt", notfallblatt.render(job(), "xx", NOW))   # unknown -> German


if __name__ == "__main__":
    unittest.main()
