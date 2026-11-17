#!/usr/bin/env python3
"""Saved drives are found again by UUID after the USB adapter was swapped."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import devuuid  # noqa: E402


class DevUuidTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.dev = os.path.join(root, "sdb1")            # stands in for /dev/sdb1
        open(self.dev, "w").close()
        self.by_uuid = os.path.join(root, "by-uuid")
        self.by_id = os.path.join(root, "by-id")
        os.makedirs(self.by_uuid)
        os.makedirs(self.by_id)
        self.uuid = "25c674d6-2dee-4bce-8b73-2ddb107d2f51"
        os.symlink(self.dev, os.path.join(self.by_uuid, self.uuid))
        self.new_id = os.path.join(self.by_id, "usb-WD_SA510_NEWADAPTER-0:0-part1")
        os.symlink(self.dev, self.new_id)
        self.old_id = os.path.join(self.by_id, "usb-WD_SA510_OLDADAPTER-0:0-part1")  # gone
        self.blocks = {os.path.realpath(self.dev)}

    def tearDown(self):
        self.tmp.cleanup()

    def is_block(self, path):
        return os.path.exists(path) and os.path.realpath(path) in self.blocks

    def test_uuid_of_finds_link(self):
        self.assertEqual(devuuid.uuid_of(self.new_id, self.by_uuid, blkid=False), self.uuid)

    def test_uuid_of_unknown_device(self):
        other = os.path.join(self.tmp.name, "sdc1")
        open(other, "w").close()
        self.assertIsNone(devuuid.uuid_of(other, self.by_uuid, blkid=False))

    def test_saved_path_present(self):
        entry = {"device": self.new_id, "uuid": self.uuid}
        self.assertEqual(devuuid.resolve(entry, self.by_uuid, self.is_block), (self.new_id, "device"))

    def test_adapter_swapped_found_by_uuid(self):
        entry = {"device": self.old_id, "uuid": self.uuid}
        path, how = devuuid.resolve(entry, self.by_uuid, self.is_block)
        self.assertEqual(how, "uuid")
        self.assertEqual(os.path.realpath(path), os.path.realpath(self.dev))

    def test_no_uuid_saved_keeps_waiting(self):
        self.assertEqual(devuuid.resolve({"device": self.old_id}, self.by_uuid, self.is_block), (None, None))

    def test_drive_absent(self):
        os.remove(os.path.join(self.by_uuid, self.uuid))
        entry = {"device": self.old_id, "uuid": self.uuid}
        self.assertEqual(devuuid.resolve(entry, self.by_uuid, self.is_block), (None, None))

    def test_uuid_with_slash_rejected(self):
        entry = {"device": self.old_id, "uuid": "../sdb1"}
        self.assertEqual(devuuid.resolve(entry, self.by_uuid, self.is_block), (None, None))


if __name__ == "__main__":
    unittest.main()
