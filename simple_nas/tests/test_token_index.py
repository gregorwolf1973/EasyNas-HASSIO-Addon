#!/usr/bin/env python3
"""The token index must resolve a link the instant it is created or rotated.

Regression: the index was cached on whole-second file mtime, so a link created
and used within the same second (and, across processes, the worker seeing an
admin edit) 404'd. This drove an intermittent test flake and a real first-
visitor miss."""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import sharing_store as ss  # noqa: E402


def _load(path, default):
    import json
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data, mirror=True):
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


class TokenIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.media = os.path.join(self.tmp, "media")
        os.makedirs(self.media)
        ss.init(self.tmp, _load, _save)
        ss._index["mtime"] = None

    def tearDown(self):
        ss._index["mtime"] = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _link(self):
        return ss.create_link({"name": "x", "root": self.media, "access": "public"},
                              allowed_roots=[self.media])

    def test_create_then_resolve_immediately_many_times(self):
        # tight loop: create and resolve in the same instant, repeatedly. With
        # the old whole-second cache this failed whenever two iterations shared
        # a second.
        for _ in range(60):
            link = self._link()
            got = ss.resolve_token(link["token"])
            self.assertIsNotNone(got, "freshly created link did not resolve")
            self.assertEqual(got["id"], link["id"])
            ss.delete_link(link["id"])

    def test_rotate_then_resolve_immediately(self):
        link = self._link()
        for _ in range(30):
            old = link["token"]
            rotated = ss.rotate_token(link["id"])
            self.assertNotEqual(rotated["token"], old)
            self.assertIsNone(ss.resolve_token(old), "old token still resolves after rotate")
            got = ss.resolve_token(rotated["token"])
            self.assertIsNotNone(got, "rotated token did not resolve at once")
            self.assertEqual(got["id"], link["id"])
            link = rotated

    def test_second_process_sees_a_new_link_via_the_file(self):
        # simulate the sandbox split: writer adds a link, a reader that shares
        # only the file (its own index cache) must see it without a restart.
        link = self._link()
        ss._index["mtime"] = None                 # reader starts cold
        self.assertIsNotNone(ss.resolve_token(link["token"]))
        # writer adds another; reader's cache must invalidate on the file change
        link2 = self._link()
        self.assertIsNotNone(ss.resolve_token(link2["token"]))
        self.assertIsNotNone(ss.resolve_token(link["token"]))


if __name__ == "__main__":
    unittest.main()
