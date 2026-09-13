#!/usr/bin/env python3
"""Tests for the path confinement helper.

Run from the add-on folder:  python -m unittest discover -s tests
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import safepath  # noqa: E402
from safepath import PathError  # noqa: E402


def _symlinks_available():
    tmp = tempfile.mkdtemp()
    try:
        os.symlink(tmp, os.path.join(tmp, "link"))
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


SYMLINKS = _symlinks_available()


class SafePathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "media")
        self.outside = os.path.join(self.tmp, "secret")
        os.makedirs(os.path.join(self.root, "fotos", "2026"))
        os.makedirs(self.outside)
        with open(os.path.join(self.root, "fotos", "a.jpg"), "w") as f:
            f.write("x")
        with open(os.path.join(self.outside, "passwords.txt"), "w") as f:
            f.write("secret")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── resolve_within ───────────────────────────────────────────────
    def test_plain_subpath(self):
        got = safepath.resolve_within(self.root, "fotos/2026")
        self.assertEqual(got, safepath.real(os.path.join(self.root, "fotos", "2026")))

    def test_empty_and_slash_give_the_root(self):
        for rel in ("", "/", ".", "./"):
            self.assertEqual(safepath.resolve_within(self.root, rel), safepath.real(self.root))

    def test_dotdot_is_rejected(self):
        for rel in ("..", "../", "../secret", "fotos/../../secret", "a/../../b"):
            with self.assertRaises(PathError, msg=rel):
                safepath.resolve_within(self.root, rel)

    def test_backslash_traversal_is_rejected(self):
        with self.assertRaises(PathError):
            safepath.resolve_within(self.root, "..\\secret")

    def test_nul_is_rejected(self):
        with self.assertRaises(PathError):
            safepath.resolve_within(self.root, "fotos\x00.jpg")

    def test_absolute_rel_stays_inside(self):
        # A leading slash must not escape - it is stripped, not honoured.
        got = safepath.resolve_within(self.root, "/fotos/a.jpg")
        self.assertEqual(got, safepath.real(os.path.join(self.root, "fotos", "a.jpg")))

    def test_missing_root_is_rejected(self):
        with self.assertRaises(PathError):
            safepath.resolve_within(os.path.join(self.tmp, "nope"), "x")

    @unittest.skipUnless(SYMLINKS, "symlinks not permitted on this machine")
    def test_symlink_escape_is_rejected(self):
        os.symlink(self.outside, os.path.join(self.root, "escape"))
        with self.assertRaises(PathError):
            safepath.resolve_within(self.root, "escape/passwords.txt")

    @unittest.skipUnless(SYMLINKS, "symlinks not permitted on this machine")
    def test_symlink_inside_root_is_fine(self):
        os.symlink(os.path.join(self.root, "fotos"), os.path.join(self.root, "bilder"))
        got = safepath.resolve_within(self.root, "bilder/a.jpg")
        self.assertEqual(got, safepath.real(os.path.join(self.root, "fotos", "a.jpg")))

    # ── the prefix trap ──────────────────────────────────────────────
    def test_sibling_with_shared_prefix_is_not_inside(self):
        # /media/foobar must not count as being under /media/foo
        foo = os.path.join(self.tmp, "foo")
        foobar = os.path.join(self.tmp, "foobar")
        os.makedirs(foo)
        os.makedirs(foobar)
        self.assertFalse(safepath.is_within(safepath.real(foo), safepath.real(foobar)))
        with self.assertRaises(PathError):
            safepath.resolve_in_roots([foo], foobar)

    # ── resolve_in_roots ─────────────────────────────────────────────
    def test_in_roots_accepts_root_itself(self):
        self.assertEqual(safepath.resolve_in_roots([self.root], self.root), safepath.real(self.root))

    def test_in_roots_rejects_outside(self):
        with self.assertRaises(PathError):
            safepath.resolve_in_roots([self.root], self.outside)

    def test_in_roots_collapses_dotdot(self):
        sneaky = os.path.join(self.root, "fotos", "..", "..", "secret")
        with self.assertRaises(PathError):
            safepath.resolve_in_roots([self.root], sneaky)

    def test_in_roots_rejects_empty(self):
        for bad in ("", "   ", None):
            with self.assertRaises(PathError):
                safepath.resolve_in_roots([self.root], bad)

    @unittest.skipUnless(os.name == "posix", "absolute unix roots only mean something on posix")
    def test_denied_roots_win_over_configuration(self):
        # Even if someone lists / as an allowed root, these stay shut.
        for denied in ("/etc", "/etc/passwd", "/proc/self/environ", "/data/admin_auth.json"):
            with self.assertRaises(PathError, msg=denied):
                safepath.resolve_in_roots(["/"], denied)

    def test_deny_list_beats_an_allowed_root(self):
        # Same rule, expressed without unix paths so it runs everywhere.
        denied_dir = os.path.join(self.root, "fotos")
        original = safepath.DENY_ROOTS
        safepath.DENY_ROOTS = (denied_dir,)
        try:
            safepath.resolve_in_roots([self.root], self.root)          # still fine
            with self.assertRaises(PathError):
                safepath.resolve_in_roots([self.root], denied_dir)
            with self.assertRaises(PathError):
                safepath.resolve_in_roots([self.root], os.path.join(denied_dir, "a.jpg"))
        finally:
            safepath.DENY_ROOTS = original

    def test_several_roots(self):
        other = os.path.join(self.tmp, "mnt")
        os.makedirs(other)
        self.assertEqual(safepath.resolve_in_roots([self.root, other], other), safepath.real(other))

    # ── resolve_new ──────────────────────────────────────────────────
    def test_new_file_in_root(self):
        got = safepath.resolve_new(self.root, "fotos", "neu.txt")
        self.assertEqual(got, os.path.join(safepath.real(os.path.join(self.root, "fotos")), "neu.txt"))

    def test_new_rejects_separator_in_leaf(self):
        for leaf in ("a/b", "a\\b", "..", ".", "", "a\x00b"):
            with self.assertRaises(PathError, msg=repr(leaf)):
                safepath.resolve_new(self.root, "", leaf)

    def test_new_rejects_escaping_dir(self):
        with self.assertRaises(PathError):
            safepath.resolve_new(self.root, "../secret", "x.txt")

    @unittest.skipUnless(SYMLINKS, "symlinks not permitted on this machine")
    def test_new_rejects_symlink_destination(self):
        os.symlink(os.path.join(self.outside, "passwords.txt"), os.path.join(self.root, "link.txt"))
        with self.assertRaises(PathError):
            safepath.resolve_new(self.root, "", "link.txt")

    # ── open_new_file ────────────────────────────────────────────────
    def test_open_new_file_refuses_existing(self):
        dest = os.path.join(self.root, "fotos", "a.jpg")
        with self.assertRaises(FileExistsError):
            safepath.open_new_file(dest)

    def test_open_new_file_creates(self):
        dest = os.path.join(self.root, "neu.bin")
        fd = safepath.open_new_file(dest)
        try:
            os.write(fd, b"hallo")
        finally:
            os.close(fd)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"hallo")


if __name__ == "__main__":
    unittest.main()
