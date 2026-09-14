#!/usr/bin/env python3
"""Deleting on the share site (allow_delete) and where uploads land (upload_subdir)."""
import datetime
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)

import share_web  # noqa: E402
import sharing_store as ss  # noqa: E402
import wopi  # noqa: E402
from test_share_site import Base  # noqa: E402


class DeleteTest(Base):
    def post_delete(self, link, path, with_csrf=True):
        tok = self.csrf(link["token"])
        data = {"path": path}
        if with_csrf:
            data["csrf"] = tok
        return self.c.post(f"/s/{link['token']}/delete", data=data)

    def test_buttons_and_confirmation_only_when_allowed(self):
        off = self.link()
        on = self.link(allow_delete=True, name="Aufraeumen")
        self.assertNotIn("/delete", self.c.get(f"/s/{off['token']}/").get_data(as_text=True))
        html = self.c.get(f"/s/{on['token']}/").get_data(as_text=True)
        self.assertIn(f"/s/{on['token']}/delete", html)
        self.assertIn("confirm(", html, "Rueckfrage vor dem Loeschen")
        self.assertIn('value="2026"', html, "Ordner haben ebenfalls einen Loeschknopf")

    def test_delete_file_and_folder(self):
        l = self.link(allow_delete=True)
        r = self.post_delete(l, "notes.txt")
        self.assertEqual(r.status_code, 302)
        self.assertFalse(os.path.exists(os.path.join(self.fotos, "notes.txt")))
        with open(os.path.join(self.fotos, "2026", "x.jpg"), "wb") as f:
            f.write(b"x")
        r = self.post_delete(l, "2026/x.jpg")
        self.assertTrue(r.headers["Location"].endswith(f"/s/{l['token']}/b/2026"), "zurueck in den Ordner")
        self.assertEqual(self.post_delete(l, "2026").status_code, 302)
        self.assertFalse(os.path.exists(os.path.join(self.fotos, "2026")))
        rows = [e for e in __import__("accesslog").tail(10) if e["event"] == "delete"]
        self.assertEqual({e["path"] for e in rows}, {"notes.txt", "2026/x.jpg", "2026"})

    def test_refused_without_permission_or_csrf(self):
        off = self.link()
        self.assertEqual(self.post_delete(off, "notes.txt").status_code, 403)
        on = self.link(allow_delete=True, name="b")
        self.assertEqual(self.post_delete(on, "notes.txt", with_csrf=False).status_code, 403)
        pw = self.link(access="password", password="geheim123", allow_delete=True, name="c")
        self.assertEqual(self.post_delete(pw, "notes.txt").status_code, 302)
        self.assertTrue(os.path.exists(os.path.join(self.fotos, "notes.txt")), "ohne Anmeldung nur Umleitung")

    def test_confinement(self):
        l = self.link(allow_delete=True)
        for bad in ("", "/", ".", "../secret.txt", "..", ".hidden", "up.part", "gibtsnicht.txt", "2026/../../secret.txt"):
            self.assertIn(self.post_delete(l, bad).status_code, (404,), bad)
        self.assertTrue(os.path.exists(self.secret))
        self.assertTrue(os.path.isdir(self.fotos), "die Link-Wurzel selbst bleibt")
        if hasattr(os, "symlink"):
            try:
                os.symlink(self.secret, os.path.join(self.fotos, "link.txt"))
            except (OSError, NotImplementedError):
                return
            self.assertEqual(self.post_delete(l, "link.txt").status_code, 404)
            self.assertTrue(os.path.exists(self.secret))

    def test_link_kinds_that_cannot_delete(self):
        up = self.link(mode="upload", allow_delete=True)
        single = self.link(file="a.jpg", allow_delete=True, name="e")
        self.assertFalse(ss.get_link(up["id"])["allow_delete"])
        self.assertFalse(ss.get_link(single["id"])["allow_delete"])
        self.assertEqual(self.post_delete(up, "notes.txt").status_code, 403)
        self.assertEqual(self.post_delete(single, "a.jpg").status_code, 403)
        nosub = self.link(allow_delete=True, allow_subdirs=False, name="f")
        with open(os.path.join(self.fotos, "2026", "y.txt"), "w") as f:
            f.write("y")
        self.assertEqual(self.post_delete(nosub, "2026/y.txt").status_code, 404)

    def test_file_open_in_collabora_is_not_deleted(self):
        l = self.link(allow_delete=True)
        target = os.path.join(os.path.realpath(self.fotos), "notes.txt")
        wopi.LockTable(self.tmp).lock(target, "L1")
        self.assertEqual(self.post_delete(l, "notes.txt").status_code, 409)
        self.assertEqual(self.post_delete(l, "").status_code, 404)
        self.assertTrue(os.path.exists(target))


class UploadPlacementTest(Base):
    def upload(self, link, sub_dir=""):
        tok = self.csrf(link["token"])
        r = self.c.post(f"/s/{link['token']}/upload?name=t.txt&dir={sub_dir}", data=b"hi",
                        headers={"X-CSRF-Token": tok})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["path"]

    def test_each_setting_is_honoured(self):
        today = datetime.date.today().isoformat()
        self.assertEqual(self.upload(self.link(mode="both", upload_subdir="none")), "t.txt")
        self.assertEqual(self.upload(self.link(mode="both", upload_subdir="by-date", name="b")), f"{today}/t.txt")
        self.assertEqual(self.upload(self.link(mode="upload", upload_subdir="none", name="c"), "2026"), "2026/t.txt")

    def test_link_without_stored_value_behaves_as_the_dialog_shows(self):
        # links saved before upload_subdir existed: the dialog shows "none" for
        # browse+upload and "by-date" for a drop box - the server must agree
        l = self.link(mode="both")
        links = ss._raw_links()
        for x in links:
            x.pop("upload_subdir", None)
        ss._save_links(links)
        self.assertEqual(ss.upload_subdir_of(ss.get_link(l["id"])), "none")
        self.assertEqual(self.upload(l), "t.txt")
        self.assertEqual(ss.upload_subdir_of({"mode": "upload"}), "by-date")

    def test_by_user_needs_accounts(self):
        with self.assertRaises(ss.ShareError):
            self.link(mode="both", access="password", password="geheim123", upload_subdir="by-user")
        with self.assertRaises(ss.ShareError):
            self.link(mode="upload", access="public", upload_subdir="by-user")
        ss.create_account("anna", "annapass1")
        l = self.link(mode="both", access="users", users=["anna"], upload_subdir="by-user")
        tok = self.csrf(l["token"])
        self.c.post(f"/s/{l['token']}/auth", data={"username": "anna", "password": "annapass1", "csrf": tok})
        self.assertEqual(self.upload(l), "anna/t.txt")


if __name__ == "__main__":
    unittest.main()
