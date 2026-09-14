#!/usr/bin/env python3
"""File-type icons: classification, one source for both web apps."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)

import fileicons  # noqa: E402
from test_share_site import Base  # noqa: E402


class KindTest(unittest.TestCase):
    def test_classification(self):
        cases = {"Bericht.DOCX": "word", "Tabelle.xlsx": "excel", "daten.csv": "excel", "Folien.pptx": "powerpoint",
                 "scan.pdf": "pdf", "IMG_0001.HEIC": "image", "film.mkv": "video", "lied.flac": "audio",
                 "backup.tar.gz": "archive", "notes.txt": "text", "app.py": "code", "setup.exe": "app",
                 "Dockerfile": "code", "ohne_endung": "file", "": "file", ".bashrc": "file"}
        for name, want in cases.items():
            self.assertEqual(fileicons.kind(name), want, name)
        self.assertEqual(fileicons.kind("Bericht.docx", is_dir=True), "folder")

    def test_every_kind_has_an_svg_and_no_external_reference(self):
        svgs = fileicons.svgs()
        self.assertTrue(set(fileicons.ext_kinds().values()) <= set(svgs))
        for k, s in svgs.items():
            self.assertTrue(s.startswith("<svg") and s.endswith("</svg>"), k)
            self.assertNotIn("http", s.replace('xmlns="http://www.w3.org/2000/svg"', ""), k)
            self.assertNotIn("href", s, k)


class ShareSiteIconTest(Base):
    def test_listing_uses_type_icons(self):
        for n in ("Kosten.xlsx", "Brief.docx"):
            open(os.path.join(self.fotos, n), "wb").close()
        html = self.c.get(f"/s/{self.link()['token']}/").get_data(as_text=True)
        for k in ("ficon-folder", "ficon-excel", "ficon-word", "ficon-image", "ficon-text"):
            self.assertIn(k, html, k)
        self.assertNotIn("&lt;svg", html, "SVG darf nicht escaped werden")


if __name__ == "__main__":
    unittest.main()
