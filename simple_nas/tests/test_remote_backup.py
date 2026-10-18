#!/usr/bin/env python3
"""External backups (restic / rclone): validation, secrets, generated
commands, schedule, the empty-source guard and the API.

restic and rclone are not installed on the test box; subprocess is replaced
by fakes that print what the real tools would.
"""
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import app as nas  # noqa: E402
import remote_backup as rb  # noqa: E402
import safepath  # noqa: E402
import sharesandbox  # noqa: E402


def fake_obscure(secret):
    return "OBSC(" + secret + ")"


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        self.media = os.path.join(self.tmp, "media")
        self.share = os.path.join(self.tmp, "share")
        for d in (self.data, os.path.join(self.media, "fotos"), self.share):
            os.makedirs(d)
        with open(os.path.join(self.media, "fotos", "a.jpg"), "w") as f:
            f.write("x")
        with open(os.path.join(self.share, "doc.txt"), "w") as f:
            f.write("y")
        roots = [self.media, self.share]
        self._saved_cfg = dict(rb._cfg)
        rb.init(self.data, lambda p: safepath.resolve_in_roots(roots, p, deny=()))
        rb._state.clear()
        self.media = safepath.real(self.media)
        self.share = safepath.real(self.share)

    def tearDown(self):
        rb._cfg.clear()
        rb._cfg.update(self._saved_cfg)
        rb._state.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hetzner(self, **kw):
        body = {"name": "Hetzner", "engine": "restic", "sources": [self.share, self.media],
                "target": {"provider": "hetzner", "host": "u123.your-storagebox.de",
                           "user": "u123", "password": "boxpass"},
                "remote_path": "simplenas", "password": "archivpasswort"}
        body.update(kw)
        return body


class ValidationTest(_Base):
    def test_valid_hetzner_job(self):
        job = rb.normalize(self.hetzner())
        self.assertEqual(job["sources"], [self.share, self.media])
        self.assertEqual(job["target"]["host"], "u123.your-storagebox.de")
        self.assertTrue(job["encrypt"])                     # restic always encrypts
        self.assertEqual(job["retention"], rb.DEFAULT_RETENTION)
        self.assertEqual(job["excludes"], rb.DEFAULT_EXCLUDES)
        self.assertEqual(job["schedule"]["type"], "manual")

    def test_rejects_url_as_host(self):
        body = self.hetzner()
        body["target"]["host"] = "https://u123.your-storagebox.de"
        with self.assertRaises(rb.JobError):
            rb.normalize(body)

    def test_rejects_newline_in_fields(self):
        # A newline would add arbitrary lines to the generated rclone.conf.
        body = self.hetzner()
        body["target"]["user"] = "u123\ntype = local"
        with self.assertRaises(rb.JobError):
            rb.normalize(body)
        body = self.hetzner(remote_path="x\n[evil]")
        with self.assertRaises(rb.JobError):
            rb.normalize(body)

    def test_rejects_traversal_in_remote_path(self):
        with self.assertRaises(rb.JobError):
            rb.normalize(self.hetzner(remote_path="a/../b"))

    def test_source_outside_roots_is_refused(self):
        with self.assertRaises(safepath.PathError):
            rb.normalize(self.hetzner(sources=[self.tmp]))

    def test_nested_sources_rejected(self):
        with self.assertRaises(rb.JobError):
            rb.normalize(self.hetzner(sources=[self.media, os.path.join(self.media, "fotos")]))

    def test_needs_a_source(self):
        with self.assertRaises(rb.JobError):
            rb.normalize(self.hetzner(sources=[]))
        # ...except for the connection test
        job = rb.normalize(self.hetzner(sources=[]), require_sources=False)
        self.assertEqual(job["sources"], [])

    def test_archive_password_min_length(self):
        with self.assertRaises(rb.JobError):
            rb.normalize(self.hetzner(password="kurz"))

    def test_rclone_without_encryption_needs_no_password(self):
        job = rb.normalize(self.hetzner(engine="rclone", password=""))
        self.assertFalse(job["encrypt"])
        with self.assertRaises(rb.JobError):
            rb.normalize(self.hetzner(engine="rclone", encrypt=True, password=""))

    def test_local_target_must_not_overlap_sources(self):
        body = self.hetzner(target={"provider": "local", "path": os.path.join(self.media, "backup")})
        with self.assertRaises(rb.JobError):
            rb.normalize(body)

    def test_local_target_outside_roots_refused(self):
        body = self.hetzner(sources=[self.share], target={"provider": "local", "path": self.data})
        with self.assertRaises(safepath.PathError):
            rb.normalize(body)

    def test_s3_needs_endpoint_unless_aws(self):
        t = {"provider": "s3", "s3_provider": "Other", "access_key": "AK",
             "secret_key": "SK", "bucket": "nas"}
        with self.assertRaises(rb.JobError):
            rb.normalize(self.hetzner(target=t))
        t["s3_provider"] = "AWS"
        self.assertEqual(rb.normalize(self.hetzner(target=t))["target"]["bucket"], "nas")

    def test_sftp_key_must_look_like_a_key(self):
        t = {"provider": "sftp", "host": "nas.example.org", "user": "bk", "key_pem": "hallo"}
        with self.assertRaises(rb.JobError):
            rb.normalize(self.hetzner(target=t))
        t["key_pem"] = "-----BEGIN OPENSSH PRIVATE KEY-----\r\nabc\r\n-----END OPENSSH PRIVATE KEY-----"
        job = rb.normalize(self.hetzner(target=t))
        self.assertEqual(job["target"]["port"], 22)
        self.assertNotIn("\r", job["target"]["key_pem"])

    def test_custom_rclone_conf_single_section(self):
        t = {"provider": "rclone", "rclone_conf": "[od]\ntype = onedrive\ntoken = {\"a\":1}\n[x]\ntype=local"}
        with self.assertRaises(rb.JobError):
            rb.normalize(self.hetzner(target=t))
        t["rclone_conf"] = "drive_id = 1"
        with self.assertRaises(rb.JobError):
            rb.normalize(self.hetzner(target=t))
        t["rclone_conf"] = "[od]\ntype = onedrive\ntoken = {\"a\":1}\ndrive_id = abc"
        rb.normalize(self.hetzner(target=t))

    def test_schedule_validation(self):
        with self.assertRaises(rb.JobError):
            rb.normalize(self.hetzner(schedule={"type": "daily", "time": "25:00"}))
        with self.assertRaises(rb.JobError):
            rb.normalize(self.hetzner(schedule={"type": "hourly"}))
        job = rb.normalize(self.hetzner(schedule={"type": "weekly", "time": "02:30", "weekday": 6}))
        self.assertEqual(job["schedule"], {"type": "weekly", "time": "02:30", "weekday": 6})
        self.assertTrue(job["last_slot"])          # no run right after saving

    def test_secrets_survive_an_edit_with_blank_fields(self):
        ex = rb.normalize(self.hetzner())
        ex["id"] = "abcd1234"
        body = self.hetzner(password="")
        body["target"]["password"] = ""
        job = rb.normalize(body, ex)
        self.assertEqual(job["target"]["password"], "boxpass")
        self.assertEqual(job["password"], "archivpasswort")

    def test_password_locked_after_first_backup(self):
        ex = rb.normalize(self.hetzner())
        ex.update(id="abcd1234", initialized=True)
        with self.assertRaises(rb.JobError):
            rb.normalize(self.hetzner(password="neuespasswort"), ex)
        # a new target is a new archive: then it may change
        body = self.hetzner(password="neuespasswort", remote_path="anderer-ordner")
        job = rb.normalize(body, ex)
        self.assertFalse(job["initialized"])
        self.assertEqual(job["password"], "neuespasswort")

    def test_passwords_are_not_trimmed(self):
        body = self.hetzner(password=" mit Leerzeichen ")
        body["target"]["password"] = " box "
        job = rb.normalize(body)
        self.assertEqual(job["password"], " mit Leerzeichen ")
        self.assertEqual(job["target"]["password"], " box ")
        self.assertEqual(job["target"]["host"], "u123.your-storagebox.de")

    def test_hetzner_port_choice(self):
        body = self.hetzner()
        body["target"]["port"] = "23"
        self.assertEqual(rb.normalize(body)["target"]["port"], 23)
        body["target"]["port"] = "2222"
        with self.assertRaises(rb.JobError):
            rb.normalize(body)

    def test_error_text_explains_and_drops_noise(self):
        out = ("NOTICE: t_x: No host key validation is being performed. Set known_hosts_file to enable it.\n"
               "See: https://rclone.org/sftp/#host-key-validation\n"
               "CRITICAL: couldn't connect SSH: ssh: handshake failed: ssh: unable to authenticate, "
               "attempted methods [none], no supported methods remain")
        msg = rb._tail(out)
        self.assertTrue(msg.startswith("Anmeldung abgelehnt"), msg)
        self.assertIn("Port 22", msg)
        self.assertNotIn("known_hosts", msg)

    def test_public_hides_secrets(self):
        job = rb.normalize(self.hetzner())
        job["id"] = "abcd1234"
        pub = rb.public(job)
        self.assertEqual(pub["password"], "")
        self.assertTrue(pub["has_password"])
        self.assertEqual(pub["target"]["password"], "")
        self.assertTrue(pub["target"]["has_password"])
        self.assertNotIn("boxpass", json.dumps(pub))
        self.assertNotIn("archivpasswort", json.dumps(pub))


class ConfigAndCommandTest(_Base):
    def job(self, **kw):
        j = rb.normalize(self.hetzner(**kw))
        j["id"] = "abcd1234"
        return j

    def test_hetzner_conf(self):
        conf = rb.build_rclone_conf(self.job(), obscure=fake_obscure)
        self.assertIn("[t_abcd1234]", conf)
        self.assertIn("type = sftp", conf)
        self.assertIn("port = 22", conf)          # plain SFTP, works without "SSH support"
        self.assertIn("pass = OBSC(boxpass)", conf)
        self.assertNotIn("crypt", conf)

    def test_restic_repo_goes_through_rclone(self):
        self.assertEqual(rb.restic_repo(self.job()), "rclone:t_abcd1234:simplenas")

    def test_bucket_is_prefixed(self):
        t = {"provider": "b2", "b2_account": "id", "b2_key": "k", "bucket": "nas"}
        job = self.job(target=t, remote_path="/haus")
        self.assertEqual(rb.restic_repo(job), "rclone:t_abcd1234:nas/haus")
        self.assertIn("hard_delete = true", rb.build_rclone_conf(job, obscure=fake_obscure))

    def test_local_repo_is_plain_path(self):
        target = os.path.join(self.tmp, "usb")
        os.makedirs(target)
        rb.init(self.data, lambda p: safepath.resolve_in_roots([self.media, self.share, target], p, deny=()))
        job = self.job(target={"provider": "local", "path": target})
        self.assertEqual(rb.restic_repo(job), os.path.join(safepath.real(target), "simplenas"))
        self.assertEqual(rb.build_rclone_conf(job, obscure=fake_obscure), "")

    def test_rclone_crypt_layer(self):
        job = self.job(engine="rclone", encrypt=True)
        conf = rb.build_rclone_conf(job, obscure=fake_obscure)
        self.assertIn("[c_abcd1234]", conf)
        self.assertIn("remote = t_abcd1234:simplenas", conf)
        self.assertIn("password = OBSC(archivpasswort)", conf)
        self.assertEqual(rb.rclone_root(job), "c_abcd1234:")

    def test_key_pem_is_one_line(self):
        t = {"provider": "sftp", "host": "h", "user": "u", "port": 2222,
             "key_pem": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"}
        conf = rb.build_rclone_conf(self.job(target=t), obscure=fake_obscure)
        line = next(l for l in conf.splitlines() if l.startswith("key_pem"))
        self.assertIn("\\nabc\\n", line)
        self.assertIn("port = 2222", conf)

    def test_custom_conf_gets_our_section_name(self):
        t = {"provider": "rclone", "rclone_conf": "[meins]\ntype = onedrive\ndrive_id = x"}
        conf = rb.build_rclone_conf(self.job(target=t), obscure=fake_obscure)
        self.assertTrue(conf.startswith("[t_abcd1234]\ntype = onedrive\ndrive_id = x"))
        self.assertNotIn("[meins]", conf)

    def test_restic_backup_cmd(self):
        cmd = rb.restic_backup_cmd(self.job(excludes=["*.tmp"], bwlimit_mbit=8))
        self.assertEqual(cmd[:2], ["restic", "backup"])
        self.assertIn("--json", cmd)
        self.assertEqual(cmd[cmd.index("--host") + 1], "simplenas")
        excludes = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--exclude"]
        self.assertEqual(excludes, [".recycle", "*.tmp"])                 # Samba-Papierkorb immer raus
        self.assertEqual(cmd[cmd.index("--limit-upload") + 1], "976")    # 8 Mbit/s in KiB/s
        self.assertEqual(cmd[-2:], [self.share, self.media])

    def test_forget_policy(self):
        cmd = rb.restic_forget_cmd(self.job(retention={"daily": 7, "weekly": 0, "monthly": 6}))
        self.assertIn("--prune", cmd)
        self.assertEqual(cmd[cmd.index("--keep-daily") + 1], "7")
        self.assertNotIn("--keep-weekly", cmd)
        self.assertIsNone(rb.restic_forget_cmd(self.job(retention={"daily": 0, "weekly": 0, "monthly": 0})))

    def test_rclone_sync_cmds(self):
        cmds = rb.rclone_sync_cmds(self.job(engine="rclone"), "20261014_030000")
        self.assertEqual([src for src, _ in cmds], [self.share, self.media])
        _, cmd = cmds[0]
        sub = rb._sub_name(self.share)          # "share" on the device
        self.assertEqual(cmd[:4], ["rclone", "sync", self.share, "t_abcd1234:simplenas/" + sub])
        self.assertEqual(cmd[cmd.index("--backup-dir") + 1],
                         "t_abcd1234:simplenas/.versions/20261014_030000/" + sub)
        self.assertEqual(cmd[cmd.index("--exclude") + 1], ".recycle/**")

    def test_sub_names(self):
        self.assertEqual(rb._sub_name("/share"), "share")
        self.assertEqual(rb._sub_name("/media/usb1"), "media_usb1")

    def test_restore_cmds(self):
        rjob = self.job(engine="rclone")
        cmd = rb.restore_cmd(rjob, "latest", ".versions/20261014_030000/share/doc.txt", False, "/share/r")
        self.assertEqual(cmd[:4], ["rclone", "copyto",
                                   "t_abcd1234:simplenas/.versions/20261014_030000/share/doc.txt",
                                   os.path.join("/share/r", "share/doc.txt")])
        cmd = rb.restore_cmd(rjob, "latest", "", True, "/share/r")
        self.assertIn("/.versions/**", cmd)
        cmd = rb.restore_cmd(self.job(), "abcdef12", "/share/doc.txt", False, "/share/r")
        self.assertEqual(cmd[:3], ["restic", "restore", "abcdef12"])
        self.assertEqual(cmd[cmd.index("--include") + 1], "/share/doc.txt")

    def test_parallel_connections(self):
        # default: 8 instead of restic's 5 / rclone's 4
        cmd = rb.restic_backup_cmd(self.job())
        self.assertEqual(cmd[cmd.index("-o") + 1], "rclone.connections=8")
        self.assertEqual(cmd[-2:], [self.share, self.media])    # sources stay last
        job = self.job(connections=3)
        self.assertIn("rclone.connections=3", rb.restic_forget_cmd(job))
        self.assertIn("rclone.connections=3", rb.restore_cmd(job, "latest", "", True, "/share/r"))
        _, cmd = rb.rclone_sync_cmds(self.job(engine="rclone", connections=12), "20261014_030000")[0]
        self.assertEqual(cmd[cmd.index("--transfers") + 1], "12")
        # jobs saved before the setting existed
        old = self.job()
        del old["connections"]
        self.assertIn("rclone.connections=8", rb.restic_backup_cmd(old))
        for bad in (0, 17, "viele"):
            with self.assertRaises(rb.JobError):
                self.job(connections=bad)

    def test_local_restic_has_no_rclone_option(self):
        body = self.hetzner(target={"provider": "local", "path": os.path.join(self.share, "ziel")},
                            sources=[self.media])
        job = rb.normalize(body)
        job["id"] = "abcd1234"
        self.assertNotIn("-o", rb.restic_backup_cmd(job))


class PriorityTest(_Base):
    def test_backups_get_fewer_cores_and_the_lowest_priority(self):
        job = rb.normalize(self.hetzner())
        job["id"] = "abcd1234"
        with mock.patch.object(rb, "build_rclone_conf", return_value=""):   # kein rclone obscure auf der Testmaschine
            env = rb._prepare(job)
        self.assertEqual(env["GOMAXPROCS"], str(max(1, (os.cpu_count() or 4) - 2)))
        import subprocess
        out = subprocess.run([sys.executable, "-c", "import os; print(os.nice(0))"], capture_output=True,
                             text=True, preexec_fn=rb._background_priority).stdout.strip()
        self.assertEqual(out, "19", "restic/rclone laufen mit niedrigster CPU-Prioritaet")

    def test_tools_are_started_with_it(self):
        job = rb.normalize(self.hetzner())
        job["id"] = "abcd1234"
        with mock.patch.object(rb.subprocess, "Popen", return_value=_FakeProc([], 0)) as popen:
            rb._run_proc("abcd1234", ["restic", "version"], {}, mock.Mock())
        self.assertIs(popen.call_args.kwargs["preexec_fn"], rb._background_priority)


class ScheduleTest(_Base):
    def test_last_slot_daily(self):
        sch = {"type": "daily", "time": "03:00"}
        self.assertEqual(rb.last_slot(sch, datetime.datetime(2026, 10, 14, 9, 0)),
                         datetime.datetime(2026, 10, 14, 3, 0))
        self.assertEqual(rb.last_slot(sch, datetime.datetime(2026, 10, 14, 2, 0)),
                         datetime.datetime(2026, 10, 13, 3, 0))
        self.assertIsNone(rb.last_slot({"type": "manual"}, datetime.datetime(2026, 10, 14)))

    def test_last_slot_weekly(self):
        sch = {"type": "weekly", "time": "04:00", "weekday": 6}      # Sunday
        # 2026-10-14 is a Wednesday
        self.assertEqual(rb.last_slot(sch, datetime.datetime(2026, 10, 14, 12, 0)),
                         datetime.datetime(2026, 10, 11, 4, 0))
        self.assertEqual(rb.next_slot(sch, datetime.datetime(2026, 10, 14, 12, 0)),
                         datetime.datetime(2026, 10, 18, 4, 0))

    def test_tick_runs_each_slot_once_and_catches_up(self):
        job = rb.normalize(self.hetzner(schedule={"type": "daily", "time": "03:00"}))
        job.update(id="abcd1234", last_slot="2026-10-13T03:00")
        rb.save_jobs([job])
        with mock.patch.object(rb, "enqueue") as enq:
            # HA was off at 03:00 - caught up at 09:00
            self.assertEqual(rb._tick(datetime.datetime(2026, 10, 14, 9, 0)), ["abcd1234"])
            self.assertEqual(rb._tick(datetime.datetime(2026, 10, 14, 9, 1)), [])
            self.assertEqual(rb._tick(datetime.datetime(2026, 10, 15, 3, 0)), ["abcd1234"])
        self.assertEqual(enq.call_count, 2)
        self.assertEqual(rb.get_job("abcd1234")["last_slot"], "2026-10-15T03:00")


class _FakeProc:
    def __init__(self, lines, rc=0):
        self.stdout = io.StringIO("".join(l + "\n" for l in lines))
        self.rc = rc

    def wait(self):
        return self.rc

    def poll(self):
        return self.rc


class RunTest(_Base):
    def setUp(self):
        super().setUp()
        self.job = rb.normalize(self.hetzner())
        self.job.update(id="abcd1234", initialized=False)
        rb.save_jobs([self.job])
        self.calls = []

    def fake_run(self, results):
        def run(cmd, **kw):
            self.calls.append(cmd)
            key = " ".join(cmd[:3])
            rc, out, err = results.get(key, (0, "", ""))
            return mock.Mock(returncode=rc, stdout=out, stderr=err)
        return run

    def test_empty_source_stops_the_run(self):
        # An unplugged USB disk leaves an empty mount point behind.
        empty = os.path.join(self.media, "usb")
        os.makedirs(empty)
        job = rb.normalize(self.hetzner(sources=[self.share, empty]))
        job["id"] = "abcd1234"
        rb.save_jobs([job])
        with mock.patch.object(rb.subprocess, "run") as run, \
                mock.patch.object(rb.subprocess, "Popen") as popen:
            rb._execute("abcd1234", "backup", {})
        run.assert_not_called()
        popen.assert_not_called()
        last = rb.get_job("abcd1234")["last_backup"]
        self.assertEqual(last["status"], "error")
        self.assertIn("leer", last["message"])

    def test_restic_first_backup_inits_and_prunes(self):
        results = {
            "rclone obscure -": (0, "OBSC\n", ""),
            "restic cat config": (10, "", "Fatal: repository does not exist: unable to open config file"),
            "restic init": (0, "created restic repository", ""),
        }
        status = json.dumps({"message_type": "status", "percent_done": 0.5,
                             "total_bytes": 100, "bytes_done": 50})
        summary = json.dumps({"message_type": "summary", "files_new": 2, "files_changed": 0,
                              "data_added": 2048, "snapshot_id": "0123456789abcdef"})
        procs = [_FakeProc([status, summary]), _FakeProc(["keep 1 snapshots"])]
        popen_cmds = []

        def popen(cmd, **kw):
            popen_cmds.append((cmd, kw["env"]))
            return procs.pop(0)

        with mock.patch.object(rb.subprocess, "run", side_effect=self.fake_run(results)), \
                mock.patch.object(rb.subprocess, "Popen", side_effect=popen):
            rb._execute("abcd1234", "backup", {})
        job = rb.get_job("abcd1234")
        self.assertEqual(job["last_backup"]["status"], "ok", job["last_backup"])
        self.assertIn("2 neu", job["last_backup"]["message"])
        self.assertTrue(job["initialized"])
        self.assertIn(["restic", "init"], self.calls)
        self.assertEqual(popen_cmds[0][0][:2], ["restic", "backup"])
        self.assertEqual(popen_cmds[1][0][:2], ["restic", "forget"])
        env = popen_cmds[0][1]
        self.assertEqual(env["RESTIC_REPOSITORY"], "rclone:t_abcd1234:simplenas")
        with open(env["RESTIC_PASSWORD_FILE"]) as f:
            self.assertEqual(f.read(), "archivpasswort")
        with open(env["RCLONE_CONFIG"]) as f:
            self.assertIn("pass = OBSC", f.read())
        with open(rb._dir("logs", "abcd1234.log")) as f:
            log = f.read()
        self.assertIn("Archiv existiert noch nicht", log)
        self.assertNotIn('"percent_done"', log)          # progress lines stay out of the log
        self.assertEqual(rb._state, {})

    def test_wrong_password_is_reported(self):
        results = {"rclone obscure -": (0, "OBSC\n", ""),
                   "restic cat config": (12, "", "Fatal: wrong password or no key found")}
        with mock.patch.object(rb.subprocess, "run", side_effect=self.fake_run(results)), \
                mock.patch.object(rb.subprocess, "Popen") as popen:
            rb._execute("abcd1234", "backup", {})
        popen.assert_not_called()
        self.assertIn("Passwort", rb.get_job("abcd1234")["last_backup"]["message"])

    def test_failure_notifies_home_assistant(self):
        results = {"rclone obscure -": (0, "OBSC\n", ""),
                   "restic cat config": (1, "", "Fatal: unable to connect: dial tcp: i/o timeout")}
        with mock.patch.object(rb.subprocess, "run", side_effect=self.fake_run(results)), \
                mock.patch.dict(os.environ, {"SUPERVISOR_TOKEN": "tok"}), \
                mock.patch.object(rb.urllib.request, "urlopen") as urlopen:
            rb._execute("abcd1234", "backup", {})
        req = urlopen.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/persistent_notification/create"))
        self.assertEqual(req.headers["Authorization"], "Bearer tok")
        self.assertIn("i/o timeout", json.loads(req.data)["message"])

    def test_rclone_sync_and_version_cleanup(self):
        job = rb.normalize(self.hetzner(engine="rclone", password="", keep_versions_days=30))
        job["id"] = "abcd1234"
        rb.save_jobs([job])
        old = (datetime.datetime.now() - datetime.timedelta(days=40)).strftime(rb.TS_FMT)
        new = datetime.datetime.now().strftime(rb.TS_FMT)
        results = {"rclone obscure -": (0, "OBSC\n", ""),
                   "rclone lsf --dirs-only": (0, f"{old}/\n{new}/\n", "")}
        stats = json.dumps({"level": "notice", "msg": "stats", "stats": {"bytes": 1500, "totalBytes": 3000}})
        procs = [_FakeProc([stats]), _FakeProc([stats])]
        with mock.patch.object(rb.subprocess, "run", side_effect=self.fake_run(results)), \
                mock.patch.object(rb.subprocess, "Popen", side_effect=lambda cmd, **kw: procs.pop(0)):
            rb._execute("abcd1234", "backup", {})
        last = rb.get_job("abcd1234")["last_backup"]
        self.assertEqual(last["status"], "ok", last)
        self.assertIn("3.0 KB", last["message"])
        purged = [c for c in self.calls if c[:2] == ["rclone", "purge"]]
        self.assertEqual(purged, [["rclone", "purge", f"t_abcd1234:simplenas/.versions/{old}"]])

    def test_interrupted_backup_is_resumed_at_start(self):
        seen = {}

        def popen(cmd, **kw):
            seen["marker"] = rb.get_job("abcd1234").get("running_since")
            return _FakeProc([])

        results = {"rclone obscure -": (0, "OBSC\n", "")}
        with mock.patch.object(rb.subprocess, "run", side_effect=self.fake_run(results)), \
                mock.patch.object(rb.subprocess, "Popen", side_effect=popen):
            rb._execute("abcd1234", "backup", {})
        self.assertTrue(seen["marker"])                          # set while running
        self.assertEqual(rb.get_job("abcd1234")["running_since"], "")   # cleared at the end

        # the add-on died mid-run: the marker survives into the next start
        rb._update_job("abcd1234", running_since="2026-10-14T23:10:00")
        rb.save_jobs(rb.load_jobs() + [dict(self.job, id="ffff0000", running_since="")])
        with mock.patch.object(rb, "enqueue") as enq:
            self.assertEqual(rb._resume_interrupted(), ["abcd1234"])
        enq.assert_called_once_with("abcd1234", "backup", {"resumed": True})

    def test_check_removes_stale_locks_first(self):
        # A backup killed by an add-on update left its lock on the box;
        # without unlock every check failed with exit 11 until the next backup.
        rb._update_job("abcd1234", initialized=True)
        order = []
        results = {"rclone obscure -": (0, "OBSC\n", "")}
        run = self.fake_run(results)

        def run_logged(cmd, **kw):
            order.append(cmd[:2])
            return run(cmd, **kw)

        def popen(cmd, **kw):
            order.append(cmd[:2])
            return _FakeProc(["no errors were found"])

        with mock.patch.object(rb.subprocess, "run", side_effect=run_logged), \
                mock.patch.object(rb.subprocess, "Popen", side_effect=popen):
            rb._execute("abcd1234", "check", {})
        self.assertIn(["restic", "unlock"], order)
        self.assertLess(order.index(["restic", "unlock"]), order.index(["restic", "check"]))

    def test_relocated_target_is_not_created_by_the_test(self):
        # Changing only the folder of a job with backups would start a second
        # archive; the connection test must say so instead of creating the folder.
        results = {"rclone obscure -": (0, "OBSC\n", ""),
                   "restic cat config": (10, "", "Fatal: repository does not exist")}
        with mock.patch.object(rb.subprocess, "run", side_effect=self.fake_run(results)):
            with self.assertRaises(rb.RunError) as cm:
                rb.test_connection(self.job, relocated=True)
            self.assertIn("noch keine gesicherten Daten", str(cm.exception))
            self.assertFalse(any(c[:2] == ["rclone", "mkdir"] for c in self.calls))
            # a new job may create it
            self.assertIn("ersten Backup", rb.test_connection(self.job))
            self.assertTrue(any(c[:2] == ["rclone", "mkdir"] for c in self.calls))

    def test_relocated_rclone_target_needs_data(self):
        job = rb.normalize(self.hetzner(engine="rclone"))
        job["id"] = "abcd1234"
        results = {"rclone obscure -": (0, "OBSC\n", ""),
                   "rclone lsf --max-depth": (3, "", "ERROR : : error listing: directory not found")}
        with mock.patch.object(rb.subprocess, "run", side_effect=self.fake_run(results)):
            with self.assertRaises(rb.RunError) as cm:
                rb.test_connection(job, relocated=True)
            self.assertIn("noch keine gesicherten Daten", str(cm.exception))
        results["rclone lsf --max-depth"] = (0, "share/\n", "")
        with mock.patch.object(rb.subprocess, "run", side_effect=self.fake_run(results)):
            self.assertIn("gefunden", rb.test_connection(job, relocated=True))
        self.assertFalse(any(c[:2] == ["rclone", "mkdir"] for c in self.calls))

    def test_marker_survives_an_edit(self):
        rb._update_job("abcd1234", running_since="2026-10-14T23:10:00")
        job = rb.normalize(self.hetzner(name="Neu", password=""), rb.get_job("abcd1234"))
        self.assertEqual(job["running_since"], "2026-10-14T23:10:00")

    def test_queue_refuses_double_start(self):
        rb._state["abcd1234"] = {"running": True}
        with self.assertRaises(rb.RunError):
            rb.enqueue("abcd1234")


class ApiTest(_Base):
    def setUp(self):
        super().setUp()
        self._saved_options = nas._OPTIONS
        nas._OPTIONS = {"file_allowed_roots": [self.media, self.share]}
        self._saved_rb_cfg = dict(rb._cfg)
        rb.init(self.data, lambda p: nas._safe(p))
        nas.app.config["TESTING"] = True
        nas.app.secret_key = "test-key"
        self.c = nas.app.test_client()
        self.c.get("/api/roots")
        with self.c.session_transaction() as sess:
            self.csrf = sess.get("csrf", "")

    def tearDown(self):
        nas._OPTIONS = self._saved_options
        super().tearDown()

    def send(self, method, path, body=None):
        return self.c.open(path, method=method, json=body, headers={"X-CSRF-Token": self.csrf})

    def test_crud_and_masking(self):
        r = self.send("POST", "/api/remote-backups", self.hetzner())
        self.assertEqual(r.status_code, 201, r.get_json())
        jid = r.get_json()["id"]
        self.assertNotIn("boxpass", r.get_data(as_text=True))

        r = self.c.get("/api/remote-backups")
        data = r.get_json()
        self.assertIn("restic", data["tools"])
        self.assertEqual(len(data["jobs"]), 1)
        self.assertNotIn("archivpasswort", r.get_data(as_text=True))

        r = self.c.get(f"/api/remote-backups/{jid}/password")
        self.assertEqual(r.get_json()["password"], "archivpasswort")

        body = self.hetzner(name="Umbenannt", password="")
        body["target"]["password"] = ""
        r = self.send("PUT", f"/api/remote-backups/{jid}", body)
        self.assertEqual(r.status_code, 200, r.get_json())
        stored = rb.get_job(jid)
        self.assertEqual(stored["name"], "Umbenannt")
        self.assertEqual(stored["target"]["password"], "boxpass")
        self.assertEqual(stored["connections"], 8)

        r = self.send("DELETE", f"/api/remote-backups/{jid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(rb.load_jobs(), [])

    def test_connection_test_knows_a_relocation(self):
        jid = self.send("POST", "/api/remote-backups", self.hetzner()).get_json()["id"]
        rb._update_job(jid, initialized=True)
        with mock.patch.object(rb, "test_connection", return_value="ok") as tc:
            self.send("POST", "/api/remote-backups/test", dict(self.hetzner(password=""), id=jid))
            self.assertFalse(tc.call_args[0][1])                    # same place
            self.send("POST", "/api/remote-backups/test",
                      dict(self.hetzner(password="", remote_path="neu"), id=jid))
            self.assertTrue(tc.call_args[0][1])                     # moved

    def test_emergency_sheet(self):
        jid = self.send("POST", "/api/remote-backups", self.hetzner()).get_json()["id"]
        r = self.c.get(f"/api/remote-backups/{jid}/notfallblatt?lang=de")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content_type.startswith("text/html"))
        self.assertEqual(r.headers.get("Cache-Control"), "no-store")
        self.assertEqual(r.headers.get("Referrer-Policy"), "no-referrer")
        page = r.get_data(as_text=True)
        self.assertIn("archivpasswort", page)
        self.assertNotIn("boxpass", page)
        self.assertEqual(self.c.get("/api/remote-backups/nope/notfallblatt").status_code, 404)

    def test_jobs_file_is_private(self):
        self.send("POST", "/api/remote-backups", self.hetzner())
        if os.name == "posix":
            self.assertEqual(os.stat(rb._jobs_file()).st_mode & 0o777, 0o600)

    def test_csrf_required(self):
        r = self.c.post("/api/remote-backups", json=self.hetzner())
        self.assertEqual(r.status_code, 403)

    def test_source_outside_roots_403(self):
        r = self.send("POST", "/api/remote-backups", self.hetzner(sources=[self.data]))
        self.assertEqual(r.status_code, 403)

    def test_validation_error_400(self):
        r = self.send("POST", "/api/remote-backups", self.hetzner(password="kurz"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("8 Zeichen", r.get_json()["error"])

    def test_restore_target_confined(self):
        jid = self.send("POST", "/api/remote-backups", self.hetzner()).get_json()["id"]
        r = self.send("POST", f"/api/remote-backups/{jid}/restore",
                      {"snapshot": "latest", "path": "/share", "target": self.data})
        self.assertEqual(r.status_code, 403)
        r = self.send("POST", f"/api/remote-backups/{jid}/restore",
                      {"snapshot": "latest; rm -rf /", "path": "/share", "target": self.share})
        self.assertEqual(r.status_code, 400)

    def test_run_queues(self):
        jid = self.send("POST", "/api/remote-backups", self.hetzner()).get_json()["id"]
        with mock.patch.object(rb, "_ensure_worker"), mock.patch.object(rb._queue, "put") as put:
            r = self.send("POST", f"/api/remote-backups/{jid}/run")
            self.assertEqual(r.status_code, 200)
            r = self.send("POST", f"/api/remote-backups/{jid}/run")
            self.assertEqual(r.status_code, 409)
        put.assert_called_once()
        self.assertTrue(rb.get_job(jid))
        r = self.send("POST", f"/api/remote-backups/{jid}/cancel")
        self.assertEqual(r.status_code, 200)


class SandboxAndMirrorTest(unittest.TestCase):
    def test_share_jail_masks_the_backup_secrets(self):
        self.assertIn("remote_backup", sharesandbox.JAIL_MASK_DIRS)
        with open(os.path.join(HERE, "..", "app", "share_jail.sh")) as f:
            script = f.read()
        self.assertIn("for d in samba remote_backup; do", script)

    def test_jobs_are_part_of_the_settings_backup(self):
        tmp = tempfile.mkdtemp()
        try:
            data, dest = os.path.join(tmp, "data"), os.path.join(tmp, "auto")
            os.makedirs(os.path.join(data, "remote_backup"))
            with open(os.path.join(data, "remote_backup", "jobs.json"), "w") as f:
                f.write("[]")
            with mock.patch.object(nas, "DATA_DIR", data):
                nas._copy_data_to(dest)
                os.remove(os.path.join(data, "remote_backup", "jobs.json"))
                nas._restore_from(dest)
            self.assertTrue(os.path.exists(os.path.join(data, "remote_backup", "jobs.json")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
