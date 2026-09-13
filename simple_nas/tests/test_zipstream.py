#!/usr/bin/env python3
"""zipstream: the archive must always come out valid, whatever happens
underneath it - and the routes using it must stay inside their roots."""
import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import zipstream  # noqa: E402


def collect(gen):
    buf = io.BytesIO()
    for chunk in gen:
        buf.write(chunk)
    buf.seek(0)
    return buf


class ZipStreamTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "urlaub")
        os.makedirs(os.path.join(self.root, "tag1"))
        os.makedirs(os.path.join(self.root, ".versteckt"))
        self.files = {"a.jpg": b"A" * 5000, "tag1/b.mp4": b"B" * 70000, "tag1/Grüße.txt": "hallo".encode()}
        for rel, data in self.files.items():
            with open(os.path.join(self.root, rel), "wb") as f:
                f.write(data)
        with open(os.path.join(self.root, "upload.part"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(self.root, ".versteckt", "s.txt"), "wb") as f:
            f.write(b"x")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip_is_valid_and_complete(self):
        total, count = zipstream.preflight(self.root, 10**9, 1000)
        self.assertEqual(count, 3)
        self.assertEqual(total, sum(len(v) for v in self.files.values()))
        buf = collect(zipstream.stream(self.root, total))
        with zipfile.ZipFile(buf) as z:
            self.assertIsNone(z.testzip())
            names = set(z.namelist())
            self.assertEqual(names, {"urlaub/a.jpg", "urlaub/tag1/b.mp4", "urlaub/tag1/Grüße.txt"})
            for rel, data in self.files.items():
                self.assertEqual(z.read("urlaub/" + rel), data)

    def test_dotfiles_and_part_files_are_skipped(self):
        buf = collect(zipstream.stream(self.root))
        with zipfile.ZipFile(buf) as z:
            for n in z.namelist():
                self.assertNotIn(".versteckt", n)
                self.assertNotIn(".part", n)

    def test_preflight_refuses_too_big_and_too_many(self):
        with self.assertRaises(zipstream.TooBig):
            zipstream.preflight(self.root, limit_bytes=1000, limit_files=1000)
        with self.assertRaises(zipstream.TooBig):
            zipstream.preflight(self.root, limit_bytes=10**9, limit_files=2)

    def test_vanishing_file_yields_missing_list_not_corruption(self):
        real_open = zipstream.open if hasattr(zipstream, "open") else open
        import builtins
        victim = os.path.join(self.root, "tag1", "b.mp4")
        orig = builtins.open

        def flaky(path, *a, **k):
            if str(path) == victim:
                raise FileNotFoundError(path)
            return orig(path, *a, **k)
        builtins.open = flaky
        try:
            buf = collect(zipstream.stream(self.root))
        finally:
            builtins.open = orig
        with zipfile.ZipFile(buf) as z:
            self.assertIsNone(z.testzip())
            self.assertIn("_MISSING.txt", z.namelist())
            self.assertIn("urlaub/tag1/b.mp4", z.read("_MISSING.txt").decode())
            self.assertNotIn("urlaub/tag1/b.mp4", z.namelist())

    def test_growth_beyond_estimate_closes_cleanly(self):
        # estimate says 5000 bytes, the tree has ~75 KB -> stop after the cap
        buf = collect(zipstream.stream(self.root, estimate_bytes=5000, margin=1.0))
        with zipfile.ZipFile(buf) as z:
            self.assertIsNone(z.testzip())
            self.assertIn("ZIP-INCOMPLETE.txt", z.namelist())
            self.assertLess(len(z.namelist()), 4)

    @unittest.skipUnless(os.name == "posix", "symlinks")
    def test_symlink_escape_is_not_followed(self):
        secret = os.path.join(self.tmp, "secret.txt")
        with open(secret, "w") as f:
            f.write("geheim")
        os.symlink(secret, os.path.join(self.root, "link.txt"))
        os.symlink(self.tmp, os.path.join(self.root, "escape"))
        buf = collect(zipstream.stream(self.root))
        with zipfile.ZipFile(buf) as z:
            self.assertNotIn("urlaub/link.txt", z.namelist())
            self.assertFalse(any("secret" in n for n in z.namelist()))

    def test_chunks_are_streamed_not_buffered_whole(self):
        chunks = list(zipstream.stream(self.root))
        self.assertGreater(len(chunks), 1)


if __name__ == "__main__":
    unittest.main()
