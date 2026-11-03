#!/usr/bin/env python3
"""Services per share (SMB, WebDAV, FTP): defaults keep existing setups unchanged."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import services  # noqa: E402


class ServicesTest(unittest.TestCase):
    def test_defaults_for_old_shares(self):
        for share in ({}, {"services": None}, {"services": "kaputt"}):
            self.assertEqual(services.share_services(share), {"smb": True, "webdav": True, "ftp": False})

    def test_saved_values_win(self):
        share = {"services": {"smb": False, "ftp": True}}
        self.assertFalse(services.offers(share, "smb"))
        self.assertTrue(services.offers(share, "webdav"))
        self.assertTrue(services.offers(share, "ftp"))

    def test_clean_drops_unknown_and_casts(self):
        self.assertEqual(services.clean({"ftp": 1, "smb": 0, "telnet": True}),
                         {"smb": False, "webdav": True, "ftp": True})
        self.assertEqual(services.clean(None), services.DEFAULTS)


if __name__ == "__main__":
    unittest.main()
