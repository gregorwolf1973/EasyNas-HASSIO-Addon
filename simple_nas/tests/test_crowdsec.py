#!/usr/bin/env python3
"""CrowdSec integration: exported log, RFC3339 time field, shipped YAML, installer."""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import yaml  # noqa: E402

import accesslog  # noqa: E402
import app as nas  # noqa: E402


class LogExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        accesslog.init(os.path.join(self.tmp, "x.log"))
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_time_field_and_export_copy(self):
        main = os.path.join(self.tmp, "share_access.log")
        export = os.path.join(self.tmp, "share", "simplenas", "share_access.log")
        accesslog.init(main, 1, export)
        accesslog.log("auth_fail", ip="203.0.113.7", link_id="abc", user="x")
        for p in (main, export):
            with open(p, encoding="utf-8") as f:
                rec = json.loads(f.readline())
            self.assertEqual(rec["event"], "auth_fail")
            self.assertEqual(rec["ip"], "203.0.113.7")
            self.assertRegex(rec["time"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(accesslog.export_path(), export)

    def test_unwritable_export_does_not_break_logging(self):
        main = os.path.join(self.tmp, "share_access.log")
        bad = os.path.join(self.tmp, "nofile.txt", "sub", "x.log")
        open(os.path.join(self.tmp, "nofile.txt"), "w").close()      # a file where a dir is needed
        accesslog.init(main, 1, bad)
        accesslog.log("view", ip="1.2.3.4")
        self.assertTrue(os.path.getsize(main) > 0)
        self.assertIsNone(accesslog.export_path())


class ExportSlotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        accesslog.init(os.path.join(self.tmp, "main.log"), 1)

    def tearDown(self):
        for slot in ("main", "crowdsec"):
            accesslog.set_export("", slot=slot)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_two_slots_write_two_files(self):
        a = os.path.join(self.tmp, "a", "x.log")
        b = os.path.join(self.tmp, "b", "y.log")
        self.assertTrue(accesslog.set_export(a, 1, slot="main"))
        self.assertTrue(accesslog.set_export(b, 1, slot="crowdsec"))
        accesslog.log("view", ip="9.9.9.9")
        for h in accesslog._logger.handlers:
            h.flush()
        for p in (a, b):
            self.assertIn("9.9.9.9", open(p, encoding="utf-8").read())
        self.assertTrue(accesslog.writes_to(a) and accesslog.writes_to(b))

    def test_same_path_is_only_opened_once(self):
        p = os.path.join(self.tmp, "same.log")
        accesslog.set_export(p, 1, slot="main")
        accesslog.set_export(p, 1, slot="crowdsec")
        handlers = [h for h in accesslog._logger.handlers
                    if getattr(h, "baseFilename", "") == os.path.abspath(p)]
        self.assertEqual(len(handlers), 1)
        self.assertTrue(accesslog.writes_to(p))
        accesslog.log("view", ip="8.8.8.8")
        for h in accesslog._logger.handlers:
            h.flush()
        self.assertEqual(open(p, encoding="utf-8").read().count("8.8.8.8"), 1)

    def test_empty_path_closes_a_slot(self):
        p = os.path.join(self.tmp, "gone.log")
        accesslog.set_export(p, 1, slot="crowdsec")
        self.assertTrue(accesslog.writes_to(p))
        accesslog.set_export("", slot="crowdsec")
        self.assertFalse(accesslog.writes_to(p))
        self.assertIsNone(accesslog.export_path("crowdsec"))


class ShippedYamlTest(unittest.TestCase):
    def test_files_parse_and_reference_each_other(self):
        d = os.path.join(HERE, "..", "app", "crowdsec")
        parser = yaml.safe_load(open(os.path.join(d, "simplenas-share-parser.yaml"), encoding="utf-8"))
        scen = list(yaml.safe_load_all(open(os.path.join(d, "simplenas-share-scenarios.yaml"), encoding="utf-8")))
        acq = yaml.safe_load(open(os.path.join(d, "simplenas-share-acquis.yaml"), encoding="utf-8"))
        self.assertEqual(acq["labels"]["type"], "simplenas-share")
        self.assertIn("simplenas-share", parser["filter"])
        metas = {s["meta"] for s in parser["statics"] if "meta" in s}
        self.assertTrue({"source_ip", "event", "log_type"} <= metas)
        self.assertEqual([s["name"] for s in scen], ["simplenas/share-bf", "simplenas/share-scan", "simplenas/share-locked"])
        for sc in scen:
            self.assertIn("simplenas_share", sc["filter"])
            self.assertEqual(sc["groupby"], "evt.Meta.source_ip")
            self.assertTrue(sc["labels"]["remediation"])


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (nas.CROWDSEC_DIR, nas._OPTIONS)
        nas.CROWDSEC_DIR = os.path.join(self.tmp, "crowdsec", "config")
        nas._OPTIONS = {"share_log_export_path": "/share/simplenas/share_access.log"}
        nas.app.config["TESTING"] = True
        nas.app.secret_key = "k"
        self.c = nas.app.test_client()
        self.c.get("/api/roots")
        with self.c.session_transaction() as s:
            self.h = {"X-CSRF-Token": s["csrf"]}

    def tearDown(self):
        for slot in ("main", "crowdsec"):
            nas.accesslog.set_export("", slot=slot)
        nas.CROWDSEC_DIR, nas._OPTIONS = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_status_without_crowdsec(self):
        st = self.c.get("/api/sharing/crowdsec/status").get_json()
        self.assertFalse(st["crowdsec_config_found"])
        self.assertEqual(self.c.post("/api/sharing/crowdsec/install", headers=self.h).status_code, 404)

    def test_install_writes_files_and_a_readable_acquisition_path(self):
        os.makedirs(nas.CROWDSEC_DIR)
        blind = os.path.join(self.tmp, "share", "log.jsonl")      # not under /config
        nas._OPTIONS["share_log_export_path"] = blind
        saved_default = nas.DEFAULT_EXPORT_PATH
        nas.DEFAULT_EXPORT_PATH = os.path.join(self.tmp, "cfg", "share_access.log")
        try:
            r = self.c.post("/api/sharing/crowdsec/install", headers=self.h)
            self.assertEqual(r.status_code, 200, r.get_json())
            for rel in nas.CROWDSEC_FILES:
                p = os.path.join(nas.CROWDSEC_DIR, rel)
                self.assertTrue(os.path.exists(p), rel)
                list(yaml.safe_load_all(open(p, encoding="utf-8")))
            acq = yaml.safe_load(open(os.path.join(nas.CROWDSEC_DIR, "acquis.d/simplenas-share.yaml"),
                                      encoding="utf-8"))
            # the configured path is not one CrowdSec can open, so the
            # acquisition names the copy that it can
            self.assertEqual(acq["filenames"], [nas.DEFAULT_EXPORT_PATH])
            st = self.c.get("/api/sharing/crowdsec/status").get_json()
            self.assertTrue(st["all_installed"])
            self.assertTrue(st["option_path_blind"])
        finally:
            nas.DEFAULT_EXPORT_PATH = saved_default

    def test_install_without_option_uses_default_and_starts_export(self):
        os.makedirs(nas.CROWDSEC_DIR)
        nas._OPTIONS["share_log_export_path"] = ""
        saved = (nas.CROWDSEC_SETUP_FILE, nas.DEFAULT_EXPORT_PATH)
        nas.CROWDSEC_SETUP_FILE = os.path.join(self.tmp, "crowdsec_setup.json")
        nas.DEFAULT_EXPORT_PATH = os.path.join(self.tmp, "export", "share_access.log")
        try:
            r = self.c.post("/api/sharing/crowdsec/install", headers=self.h)
            self.assertEqual(r.status_code, 200, r.get_json())
            self.assertEqual(r.get_json()["export_path"], nas.DEFAULT_EXPORT_PATH)
            self.assertTrue(r.get_json()["export_active"])
            acq = yaml.safe_load(open(os.path.join(nas.CROWDSEC_DIR, "acquis.d/simplenas-share.yaml"), encoding="utf-8"))
            self.assertEqual(acq["filenames"], [nas.DEFAULT_EXPORT_PATH])
            # the choice survives a restart: it is persisted in /data, not in options.json
            self.assertEqual(json.load(open(nas.CROWDSEC_SETUP_FILE))["export_path"], nas.DEFAULT_EXPORT_PATH)
            self.assertEqual(nas._share_export_path(), nas.DEFAULT_EXPORT_PATH)
            st = self.c.get("/api/sharing/crowdsec/status").get_json()
            self.assertTrue(st["all_installed"] and st["export_active"])
            # and the log really lands there
            nas.accesslog.log("view", link_id="abc")
            for h in nas.accesslog._logger.handlers:
                h.flush()
            self.assertIn('"link_id": "abc"', open(nas.DEFAULT_EXPORT_PATH, encoding="utf-8").read())
            # an explicit option still wins over the saved choice
            nas._OPTIONS["share_log_export_path"] = "/share/x/y.log"
            self.assertEqual(nas._share_export_path(), "/share/x/y.log")
        finally:
            for slot in ("main", "crowdsec"):
                nas.accesslog.set_export("", slot=slot)
            nas.CROWDSEC_SETUP_FILE, nas.DEFAULT_EXPORT_PATH = saved



class BlindOptionTest(unittest.TestCase):
    """share_log_export_path may point at /share, which the CrowdSec add-on
    does not map. The configured file is still written, but CrowdSec has to be
    given one it can actually open."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (nas.CROWDSEC_DIR, nas._OPTIONS, nas.CROWDSEC_SETUP_FILE,
                       nas.DATA_DIR, nas.DEFAULT_EXPORT_PATH)
        nas.CROWDSEC_DIR = os.path.join(self.tmp, "crowdsec", "config")
        nas.DATA_DIR = self.tmp
        nas.CROWDSEC_SETUP_FILE = os.path.join(self.tmp, "crowdsec_setup.json")
        nas.DEFAULT_EXPORT_PATH = os.path.join(self.tmp, "config", "share_access.log")
        self.blind = os.path.join(self.tmp, "share", "share_access.log")
        nas._OPTIONS = {"share_log_export_path": self.blind}
        os.makedirs(nas.CROWDSEC_DIR)
        nas.app.config["TESTING"] = True
        nas.app.secret_key = "k"
        self.c = nas.app.test_client()
        self.c.get("/api/roots")
        with self.c.session_transaction() as sess:
            self.h = {"X-CSRF-Token": sess["csrf"]}
        nas.accesslog.init(os.path.join(self.tmp, "main.log"), 1)

    def tearDown(self):
        for slot in ("main", "crowdsec"):
            nas.accesslog.set_export("", slot=slot)
        (nas.CROWDSEC_DIR, nas._OPTIONS, nas.CROWDSEC_SETUP_FILE,
         nas.DATA_DIR, nas.DEFAULT_EXPORT_PATH) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_config_paths_are_readable_share_paths_are_not(self):
        self.assertTrue(nas._crowdsec_can_read("/config/.simplenas/share_access.log"))
        self.assertFalse(nas._crowdsec_can_read("/share/simplenas/share_access.log"))
        self.assertFalse(nas._crowdsec_can_read("/media/x.log"))
        self.assertFalse(nas._crowdsec_can_read(""))

    def test_install_writes_both_files_and_points_crowdsec_at_the_readable_one(self):
        r = self.c.post("/api/sharing/crowdsec/install", headers=self.h)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["export_path"], nas.DEFAULT_EXPORT_PATH)
        acq = yaml.safe_load(open(os.path.join(nas.CROWDSEC_DIR, "acquis.d/simplenas-share.yaml"),
                                  encoding="utf-8"))
        self.assertEqual(acq["filenames"], [nas.DEFAULT_EXPORT_PATH])
        nas.accesslog.log("auth_fail", ip="203.0.113.9", link_id="abc")
        for h in nas.accesslog._logger.handlers:
            h.flush()
        # the configured file keeps being served, and CrowdSec gets its own
        for p in (self.blind, nas.DEFAULT_EXPORT_PATH):
            self.assertIn('"ip": "203.0.113.9"', open(p, encoding="utf-8").read(), p)
        st = self.c.get("/api/sharing/crowdsec/status").get_json()
        self.assertTrue(st["option_path_blind"])
        self.assertTrue(st["export_active"])
        self.assertEqual(st["crowdsec_path"], nas.DEFAULT_EXPORT_PATH)

    def test_a_readable_option_is_used_as_is(self):
        good = os.path.join(self.tmp, "config", "eigener.log")
        nas._OPTIONS["share_log_export_path"] = good
        real = nas._crowdsec_can_read
        nas._crowdsec_can_read = lambda p: bool(p) and p.startswith(os.path.join(self.tmp, "config"))
        try:
            r = self.c.post("/api/sharing/crowdsec/install", headers=self.h)
            self.assertEqual(r.status_code, 200, r.get_json())
            self.assertEqual(r.get_json()["export_path"], good)
            st = self.c.get("/api/sharing/crowdsec/status").get_json()
            self.assertFalse(st["option_path_blind"])
            nas.accesslog.log("view", ip="1.1.1.1")
            for h in nas.accesslog._logger.handlers:
                h.flush()
            self.assertIn('"ip": "1.1.1.1"', open(good, encoding="utf-8").read())
        finally:
            nas._crowdsec_can_read = real


class ExportPathMigrationTest(unittest.TestCase):
    """The first default lived under /share, which is not mapped into the
    CrowdSec add-on - CrowdSec logged "No matching files for pattern" and no
    scenario could ever fire. Existing installs have to move by themselves."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (nas.CROWDSEC_DIR, nas._OPTIONS, nas.CROWDSEC_SETUP_FILE, nas.DATA_DIR)
        nas.CROWDSEC_DIR = os.path.join(self.tmp, "crowdsec", "config")
        nas.DATA_DIR = self.tmp
        nas.CROWDSEC_SETUP_FILE = os.path.join(self.tmp, "crowdsec_setup.json")
        nas._OPTIONS = {}
        os.makedirs(nas.CROWDSEC_DIR)

    def tearDown(self):
        (nas.CROWDSEC_DIR, nas._OPTIONS, nas.CROWDSEC_SETUP_FILE, nas.DATA_DIR) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_lives_under_config(self):
        # /config is mapped into both add-ons; /share only into this one
        self.assertTrue(nas.DEFAULT_EXPORT_PATH.startswith("/config/"))
        acq = yaml.safe_load(open(os.path.join(HERE, "..", "app", "crowdsec",
                                               "simplenas-share-acquis.yaml"), encoding="utf-8"))
        self.assertEqual(acq["filenames"], [nas.DEFAULT_EXPORT_PATH])

    def test_saved_legacy_path_is_migrated(self):
        nas.save_json(nas.CROWDSEC_SETUP_FILE, {"export_path": "/share/simplenas/share_access.log"})
        self.assertEqual(nas._share_export_path(), nas.DEFAULT_EXPORT_PATH)

    def test_explicit_option_still_wins(self):
        nas.save_json(nas.CROWDSEC_SETUP_FILE, {"export_path": "/share/simplenas/share_access.log"})
        nas._OPTIONS["share_log_export_path"] = "/share/eigener/pfad.log"
        self.assertEqual(nas._share_export_path(), "/share/eigener/pfad.log")

    def test_sync_rewrites_an_outdated_acquisition(self):
        dest = os.path.join(nas.CROWDSEC_DIR, "acquis.d", "simplenas-share.yaml")
        os.makedirs(os.path.dirname(dest))
        with open(dest, "w", encoding="utf-8") as f:
            f.write("filenames:\n  - /share/simplenas/share_access.log\nlabels:\n  type: simplenas-share\n")
        self.assertTrue(nas._sync_crowdsec_acquis(nas.DEFAULT_EXPORT_PATH))
        acq = yaml.safe_load(open(dest, encoding="utf-8"))
        self.assertEqual(acq["filenames"], [nas.DEFAULT_EXPORT_PATH])
        self.assertEqual(acq["labels"]["type"], "simplenas-share")
        # second call changes nothing
        self.assertFalse(nas._sync_crowdsec_acquis(nas.DEFAULT_EXPORT_PATH))

    def test_sync_does_nothing_without_an_installed_file(self):
        self.assertFalse(nas._sync_crowdsec_acquis(nas.DEFAULT_EXPORT_PATH))
        self.assertFalse(nas._sync_crowdsec_acquis(""))


if __name__ == "__main__":
    unittest.main()
