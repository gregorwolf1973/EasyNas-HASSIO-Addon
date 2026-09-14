#!/usr/bin/env python3
"""Collabora / WOPI: tokens, discovery, proof keys, locks, and the WOPI routes
on the public share site - authorisation, read-only links, saves, conflicts."""
import base64
import hashlib
import os
import random
import re
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)

import share_web  # noqa: E402
import sharing_store as ss  # noqa: E402
import wopi  # noqa: E402
from test_share_site import Base  # noqa: E402


# ── a throwaway RSA key, pure Python, so the proof check is tested for real ──

def _is_probable_prime(n, rounds=24):
    if n < 4:
        return n in (2, 3)
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d, r = d // 2, r + 1
    rng = random.Random(n)
    for _ in range(rounds):
        x = pow(rng.randrange(2, n - 1), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits, rng):
    while True:
        c = rng.getrandbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _is_probable_prime(c):
            return c


def make_rsa(seed):
    rng = random.Random(seed)
    e = 65537
    while True:
        p, q = _prime(512, rng), _prime(512, rng)
        n, phi = p * q, (p - 1) * (q - 1)
        if n.bit_length() == 1024 and phi % e:
            return n, e, pow(e, -1, phi)


def _b64(n):
    return base64.b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).decode()


def sign(key, message):
    n, _e, d = key
    k = (n.bit_length() + 7) // 8
    t = wopi._SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return base64.b64encode(pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")).decode()


KEY = make_rsa(1)
OTHER_KEY = make_rsa(2)


def discovery_xml(key=KEY, host="https://homeassistant:9980"):
    n, e, _d = key
    return f"""<?xml version="1.0" encoding="utf-8"?>
<wopi-discovery><net-zone name="external-http">
  <app name="writer"><action default="true" ext="docx" name="edit" urlsrc="{host}/browser/abc/cool.html?"/>
                     <action ext="odt" name="edit" urlsrc="{host}/browser/abc/cool.html?"/></app>
  <app name="calc"><action ext="xlsx" name="edit" urlsrc="{host}/browser/abc/cool.html?"/></app>
  <app name="draw"><action ext="pdf" name="view" urlsrc="{host}/browser/abc/cool.html?"/></app>
</net-zone>
<proof-key exponent="{_b64(e)}" modulus="{_b64(n)}" oldexponent="{_b64(e)}" oldmodulus="{_b64(n)}" value="x" oldvalue="x"/>
</wopi-discovery>"""


def ticks(unix=None):
    return int(((unix if unix is not None else time.time()) + 62135596800) * 10_000_000)


# ── pure module ──────────────────────────────────────────────────────────────

class TokenTest(unittest.TestCase):
    def test_roundtrip_tamper_and_expiry(self):
        tok, exp = wopi.make_token("s3cret", "abcd", "docs/a.docx", "anna", True, 3, 2, 60, origin="https://x", now=1000)
        c = wopi.read_token("s3cret", tok, now=1030)
        self.assertEqual((c["l"], c["p"], c["u"], c["w"], c["le"], c["ue"], c["o"]),
                         ("abcd", "docs/a.docx", "anna", 1, 3, 2, "https://x"))
        self.assertEqual(exp, 1060)
        self.assertIsNone(wopi.read_token("s3cret", tok, now=1061), "abgelaufen")
        self.assertIsNone(wopi.read_token("anderes", tok, now=1030), "falscher Schluessel")
        body, sig = tok.split(".")
        forged = wopi._b64e(wopi._b64d(body).replace(b"a.docx", b"b.docx"))
        self.assertIsNone(wopi.read_token("s3cret", f"{forged}.{sig}", now=1030))
        for junk in ("", "x", "a.b.c", "." * 5000):
            self.assertIsNone(wopi.read_token("s3cret", junk, now=1030))

    def test_file_id_is_opaque_and_stable(self):
        a = wopi.file_id("abcd", "docs/a.docx")
        self.assertEqual(a, wopi.file_id("abcd", "docs/a.docx"))
        self.assertNotEqual(a, wopi.file_id("abcd", "docs/b.docx"))
        self.assertNotEqual(a, wopi.file_id("abce", "docs/a.docx"))
        self.assertNotIn("docx", a)


class DiscoveryTest(unittest.TestCase):
    def test_parse_rewrites_internal_origin(self):
        d = wopi.parse_discovery(discovery_xml(), "https://collabora.example.org")
        self.assertEqual(d["actions"]["docx"]["edit"], "https://collabora.example.org/browser/abc/cool.html?")
        self.assertIn("modulus", d["proof"])
        self.assertEqual(wopi.pick_action(d, "Bericht.DOCX", True)[0], "edit")
        self.assertEqual(wopi.pick_action(d, "x.pdf", True)[0], "view")
        self.assertEqual(wopi.pick_action(d, "x.jpg", True), (None, None))
        self.assertEqual(wopi.pick_action(d, "x.docx", False)[0], "edit", "kein view-Eintrag: edit, aber read-only")

    def test_cache_and_retry_after_failure(self):
        calls = []
        clock = [1000.0]

        def fetch(url, verify):
            calls.append(url)
            if len(calls) == 1:
                raise OSError("down")
            return discovery_xml()
        d = wopi.Discovery(fetch=fetch, clock=lambda: clock[0])
        self.assertIsNone(d.get("https://c.example", "https://intern:9980"))
        self.assertIn("down", d.error)
        self.assertIsNone(d.get("https://c.example", "https://intern:9980"), "kein neuer Versuch innerhalb 60 s")
        self.assertEqual(len(calls), 1)
        clock[0] += wopi.DISCOVERY_RETRY + 1
        self.assertIsNotNone(d.get("https://c.example", "https://intern:9980"))
        self.assertEqual(calls[-1], "https://intern:9980/hosting/discovery")
        d.get("https://c.example", "https://intern:9980")
        self.assertEqual(len(calls), 2, "Treffer aus dem Cache")

    def test_editor_url(self):
        u = wopi.editor_url("https://c/browser/x/cool.html?", "https://nas/wopi/files/ab", "de")
        self.assertEqual(u, "https://c/browser/x/cool.html?WOPISrc=https%3A%2F%2Fnas%2Fwopi%2Ffiles%2Fab&lang=de&closebutton=1")


class ProofTest(unittest.TestCase):
    def setUp(self):
        self.proof = wopi.parse_discovery(discovery_xml(), "https://c")["proof"]

    def test_valid_and_invalid_signatures(self):
        ts = ticks()
        url = "https://nas.example/wopi/files/ab?access_token=T"
        sig = sign(KEY, wopi.proof_message("T", url, ts))
        self.assertTrue(wopi.verify_proof(self.proof, "T", ["http://other/x", url], ts, sig, ""))
        self.assertTrue(wopi.verify_proof(self.proof, "T", [url.lower()], ts, sig, ""), "URL wird gross geschrieben")
        self.assertFalse(wopi.verify_proof(self.proof, "T2", [url], ts, sig, ""), "anderes Token")
        self.assertFalse(wopi.verify_proof(self.proof, "T", ["https://nas.example/wopi/files/zz?access_token=T"], ts, sig, ""))
        self.assertFalse(wopi.verify_proof(self.proof, "T", [url], ts, sign(OTHER_KEY, wopi.proof_message("T", url, ts)), ""))
        self.assertFalse(wopi.verify_proof(self.proof, "T", [url], ts, "", ""))
        old = ticks(time.time() - wopi.PROOF_MAX_AGE - 5)
        self.assertFalse(wopi.verify_proof(self.proof, "T", [url], old, sign(KEY, wopi.proof_message("T", url, old)), ""))


class LockTableTest(unittest.TestCase):
    def test_lock_semantics_and_persistence(self):
        tmp = tempfile.mkdtemp()
        clock = [1000.0]
        t = wopi.LockTable(tmp, clock=lambda: clock[0])
        self.assertEqual(t.lock("/f", "A"), (True, "A"))
        self.assertEqual(t.lock("/f", "A"), (True, "A"), "gleiche Sperre = auffrischen")
        self.assertEqual(t.lock("/f", "B"), (False, "A"))
        self.assertEqual(t.lock("/f", "B", old_lock="X"), (False, "A"))
        self.assertEqual(t.lock("/f", "B", old_lock="A"), (True, "B"))
        self.assertEqual(wopi.LockTable(tmp, clock=lambda: clock[0]).get("/f"), "B", "ueberlebt Neustart")
        self.assertEqual(t.refresh("/f", "A"), (False, "B"))
        self.assertEqual(t.unlock("/f", "A"), (False, "B"))
        clock[0] += wopi.LOCK_SECONDS + 1
        self.assertEqual(t.get("/f"), "", "abgelaufen")
        self.assertEqual(t.lock("/f", "C"), (True, "C"))
        self.assertEqual(t.unlock("/f", "C"), (True, ""))
        self.assertEqual(t.lock("/f", ""), (False, ""))


# ── the share site ───────────────────────────────────────────────────────────

class WopiSiteBase(Base):
    def setUp(self):
        super().setUp()
        self.opts.update({"collabora_enabled": True, "collabora_url": "https://collabora.example.org",
                          "collabora_internal_url": "https://homeassistant:9980"})
        self._saved_disc = share_web.DISCOVERY
        self.fetches = 0

        def fetch(url, verify):
            self.fetches += 1
            return discovery_xml()
        share_web.DISCOVERY = wopi.Discovery(fetch=fetch)
        with open(os.path.join(self.fotos, "Bericht.docx"), "wb") as f:
            f.write(b"PK-original")
        os.makedirs(os.path.join(self.fotos, "2026"), exist_ok=True)
        with open(os.path.join(self.fotos, "2026", "Tabelle.xlsx"), "wb") as f:
            f.write(b"PK-xlsx")

    def tearDown(self):
        share_web.DISCOVERY = self._saved_disc
        super().tearDown()

    def open_editor(self, link, sub="Bericht.docx"):
        r = self.c.get(f"/s/{link['token']}/e/{sub}")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        html = r.get_data(as_text=True)
        token = re.search(r'name="access_token" value="([^"]+)"', html).group(1)
        action = re.search(r'action="([^"]+)"', html).group(1).replace("&amp;", "&")
        fid = re.search(r"WOPISrc=[^&]*%2Fwopi%2Ffiles%2F([0-9a-f]+)", action).group(1)
        return r, token, fid, action

    def call(self, method, path, token, key=KEY, signed_url=None, headers=None, **kw):
        url_path = f"{path}?access_token={token}"
        ts = ticks()
        signed = signed_url or f"http://localhost{url_path}"
        h = {"X-WOPI-TimeStamp": str(ts), "X-WOPI-Proof": sign(key, wopi.proof_message(token, signed, ts))}
        h.update(headers or {})
        return self.c.open(url_path, method=method, headers=h, **kw)


class EditorPageTest(WopiSiteBase):
    def test_listing_offers_edit_only_where_allowed(self):
        ro = self.link()
        rw = self.link(allow_edit=True, name="Buero")
        h_ro = self.c.get(f"/s/{ro['token']}/").get_data(as_text=True)
        h_rw = self.c.get(f"/s/{rw['token']}/").get_data(as_text=True)
        self.assertIn(f"/s/{ro['token']}/e/Bericht.docx", h_ro)
        self.assertIn("👁", h_ro)
        self.assertNotIn("✎", h_ro)
        self.assertIn("✎", h_rw)
        self.assertNotIn("/e/a.jpg", h_rw, "Bilder gehen nicht an Collabora")
        self.assertEqual(self.fetches, 1, "eine Discovery-Abfrage fuer beide Listen")

    def test_disabled_collabora_hides_everything(self):
        self.opts["collabora_enabled"] = False
        l = self.link(allow_edit=True)
        self.assertNotIn("/e/", self.c.get(f"/s/{l['token']}/").get_data(as_text=True))
        self.assertEqual(self.c.get(f"/s/{l['token']}/e/Bericht.docx").status_code, 404)
        self.assertEqual(self.c.get("/wopi/files/abc?access_token=x").status_code, 404)
        self.assertEqual(self.fetches, 0)

    def test_editor_page_frames_only_collabora(self):
        l = self.link(allow_edit=True)
        r, token, fid, action = self.open_editor(l)
        csp = r.headers["Content-Security-Policy"]
        self.assertIn("frame-src https://collabora.example.org;", csp)
        self.assertIn("form-action 'self' https://collabora.example.org;", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertTrue(action.startswith("https://collabora.example.org/browser/abc/cool.html?WOPISrc="))
        self.assertEqual(fid, wopi.file_id(l["id"], "Bericht.docx"))
        self.assertNotIn(self.fotos, r.get_data(as_text=True))
        self.assertEqual(ss.get_link(l["id"])["download_count"], 1)
        # other pages keep the strict policy
        self.assertNotIn("frame-src", self.c.get(f"/s/{l['token']}/").headers["Content-Security-Policy"])

    def test_editor_needs_link_auth_and_confines_paths(self):
        l = self.link(access="password", password="geheim123", allow_edit=True)
        self.assertEqual(self.c.get(f"/s/{l['token']}/e/Bericht.docx").status_code, 302)
        pub = self.link(allow_edit=True)
        for bad in ("../secret.txt", ".hidden", "nope.docx"):
            self.assertEqual(self.c.get(f"/s/{pub['token']}/e/{bad}").status_code, 404, bad)
        self.assertEqual(self.c.get(f"/s/{pub['token']}/e/a.jpg").status_code, 503)
        up = self.link(mode="upload", allow_edit=True)
        self.assertFalse(ss.get_link(up["id"])["allow_edit"], "Ablage-Links koennen nicht bearbeiten")
        self.assertEqual(self.c.get(f"/s/{up['token']}/e/Bericht.docx").status_code, 403)

    def test_collabora_down_gives_friendly_503(self):
        share_web.DISCOVERY = wopi.Discovery(fetch=lambda u, v: (_ for _ in ()).throw(OSError("weg")))
        l = self.link(allow_edit=True)
        self.assertNotIn("/e/", self.c.get(f"/s/{l['token']}/").get_data(as_text=True))
        self.assertEqual(self.c.get(f"/s/{l['token']}/e/Bericht.docx").status_code, 503)


class WopiCallsTest(WopiSiteBase):
    def test_check_file_info_and_get_file(self):
        l = self.link(allow_edit=True)
        _, token, fid, _ = self.open_editor(l)
        r = self.call("GET", f"/wopi/files/{fid}", token)
        self.assertEqual(r.status_code, 200)
        info = r.get_json()
        self.assertEqual(info["BaseFileName"], "Bericht.docx")
        self.assertEqual(info["Size"], len(b"PK-original"))
        self.assertTrue(info["UserCanWrite"])
        self.assertTrue(info["UserCanNotWriteRelative"])
        self.assertEqual(info["PostMessageOrigin"], "http://localhost")
        r = self.call("GET", f"/wopi/files/{fid}/contents", token)
        self.assertEqual((r.status_code, r.get_data()), (200, b"PK-original"))
        self.assertIn("X-WOPI-ItemVersion", r.headers)
        r.close()

    def test_proof_is_required(self):
        l = self.link(allow_edit=True)
        _, token, fid, _ = self.open_editor(l)
        self.assertEqual(self.c.get(f"/wopi/files/{fid}?access_token={token}").status_code, 401, "ohne Signatur")
        self.assertEqual(self.call("GET", f"/wopi/files/{fid}", token, key=OTHER_KEY).status_code, 401)
        # the signature over the configured public base also counts (Collabora behind a proxy)
        self.opts["share_public_url"] = "https://nas.example.org"
        r = self.call("GET", f"/wopi/files/{fid}", token,
                      signed_url=f"https://nas.example.org/wopi/files/{fid}?access_token={token}")
        self.assertEqual(r.status_code, 200)
        self.opts["collabora_verify_proof"] = False
        self.assertEqual(self.c.get(f"/wopi/files/{fid}?access_token={token}").status_code, 200)

    def test_token_is_bound_to_its_file(self):
        l = self.link(allow_edit=True)
        _, token, fid, _ = self.open_editor(l)
        other = wopi.file_id(l["id"], "2026/Tabelle.xlsx")
        self.assertEqual(self.call("GET", f"/wopi/files/{other}", token).status_code, 401)
        self.assertEqual(self.call("GET", f"/wopi/files/{fid}", token + "x").status_code, 401)
        forged, _ = wopi.make_token("falscher-key", l["id"], "Bericht.docx", "", True, 1, 0, 3600)
        self.assertEqual(self.call("GET", f"/wopi/files/{fid}", forged).status_code, 401)

    def test_save_with_lock(self):
        l = self.link(allow_edit=True)
        _, token, fid, _ = self.open_editor(l)
        f = f"/wopi/files/{fid}"
        stamp = self.call("GET", f, token).get_json()["LastModifiedTime"]
        r = self.call("POST", f, token, headers={"X-WOPI-Override": "LOCK", "X-WOPI-Lock": "L1"})
        self.assertEqual(r.status_code, 200)
        r = self.call("POST", f, token, headers={"X-WOPI-Override": "LOCK", "X-WOPI-Lock": "L2"})
        self.assertEqual((r.status_code, r.headers["X-WOPI-Lock"]), (409, "L1"))
        r = self.call("POST", f"{f}/contents", token, data=b"PK-fremd",
                      headers={"X-WOPI-Override": "PUT", "X-WOPI-Lock": "L2"})
        self.assertEqual(r.status_code, 409)
        r = self.call("POST", f"{f}/contents", token, data=b"PK-neu",
                      headers={"X-WOPI-Override": "PUT", "X-WOPI-Lock": "L1", "X-COOL-WOPI-Timestamp": stamp})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        with open(os.path.join(self.fotos, "Bericht.docx"), "rb") as fh:
            self.assertEqual(fh.read(), b"PK-neu")
        self.assertIn("LastModifiedTime", r.get_json())
        self.assertFalse([n for n in os.listdir(self.fotos) if n.startswith(".wopi-")], "keine Reste")
        r = self.call("POST", f, token, headers={"X-WOPI-Override": "GET_LOCK"})
        self.assertEqual(r.headers["X-WOPI-Lock"], "L1")
        r = self.call("POST", f, token, headers={"X-WOPI-Override": "UNLOCK", "X-WOPI-Lock": "L1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.call("POST", f, token, headers={"X-WOPI-Override": "PUT_RELATIVE"}).status_code, 501)

    def test_changed_behind_collaboras_back_is_a_conflict(self):
        l = self.link(allow_edit=True)
        _, token, fid, _ = self.open_editor(l)
        f = f"/wopi/files/{fid}"
        stamp = self.call("GET", f, token).get_json()["LastModifiedTime"]
        self.call("POST", f, token, headers={"X-WOPI-Override": "LOCK", "X-WOPI-Lock": "L1"})
        p = os.path.join(self.fotos, "Bericht.docx")
        os.utime(p, (time.time() + 50, time.time() + 50))          # a Samba user saved meanwhile
        r = self.call("POST", f"{f}/contents", token, data=b"PK-alt",
                      headers={"X-WOPI-Override": "PUT", "X-WOPI-Lock": "L1", "X-COOL-WOPI-Timestamp": stamp})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["COOLStatusCode"], 1010)
        with open(p, "rb") as fh:
            self.assertEqual(fh.read(), b"PK-original")

    def test_read_only_link_cannot_lock_or_save(self):
        l = self.link()
        _, token, fid, _ = self.open_editor(l)
        f = f"/wopi/files/{fid}"
        self.assertFalse(self.call("GET", f, token).get_json()["UserCanWrite"])
        self.assertEqual(self.call("POST", f, token, headers={"X-WOPI-Override": "LOCK", "X-WOPI-Lock": "L"}).status_code, 401)
        r = self.call("POST", f"{f}/contents", token, data=b"boese", headers={"X-WOPI-Override": "PUT"})
        self.assertEqual(r.status_code, 401)
        # switching editing off on a writable link ends writes for tokens already issued
        l2 = self.link(allow_edit=True, name="zwei")
        _, token2, fid2, _ = self.open_editor(l2)
        ss.update_link(l2["id"], {"allow_edit": False}, allowed_roots=[self.media])
        r = self.call("POST", f"/wopi/files/{fid2}/contents", token2, data=b"boese", headers={"X-WOPI-Override": "PUT"})
        self.assertEqual(r.status_code, 401)
        with open(os.path.join(self.fotos, "Bericht.docx"), "rb") as fh:
            self.assertEqual(fh.read(), b"PK-original")

    def test_revocation_ends_running_sessions(self):
        l = self.link(allow_edit=True)
        _, token, fid, _ = self.open_editor(l)
        self.assertEqual(self.call("GET", f"/wopi/files/{fid}", token).status_code, 200)
        ss.rotate_token(l["id"])
        self.assertEqual(self.call("GET", f"/wopi/files/{fid}", token).status_code, 401)
        l2 = self.link(allow_edit=True, name="zwei")
        _, token2, fid2, _ = self.open_editor(l2)
        ss.update_link(l2["id"], {"enabled": False}, allowed_roots=[self.media])
        self.assertEqual(self.call("GET", f"/wopi/files/{fid2}", token2).status_code, 401)

    def test_one_download_link_still_saves(self):
        l = self.link(allow_edit=True, max_downloads=1)
        _, token, fid, _ = self.open_editor(l)
        self.assertEqual(ss.get_link(l["id"])["download_count"], 1)
        r = self.call("POST", f"/wopi/files/{fid}/contents", token, data=b"PK-neu",
                      headers={"X-WOPI-Override": "PUT", "X-WOPI-Lock": "L1"})
        self.assertEqual(r.status_code, 200)

    def test_users_link_carries_the_account(self):
        ss.create_account("anna", "annapass1", display_name="Anna A.")
        l = self.link(access="users", users=["anna"], allow_edit=True)
        tok = self.csrf(l["token"])
        self.c.post(f"/s/{l['token']}/auth", data={"username": "anna", "password": "annapass1", "csrf": tok})
        _, token, fid, _ = self.open_editor(l)
        info = self.call("GET", f"/wopi/files/{fid}", token).get_json()
        self.assertEqual((info["UserId"], info["UserFriendlyName"], info["IsAnonymousUser"]), ("anna", "Anna A.", False))
        ss.update_account("anna", {"password": "neuespass1"})
        self.assertEqual(self.call("GET", f"/wopi/files/{fid}", token).status_code, 401,
                         "Passwortwechsel beendet auch die Editor-Sitzung")

    def test_subfolder_file(self):
        l = self.link(allow_edit=True)
        r, token, fid, _ = self.open_editor(l, "2026/Tabelle.xlsx")
        self.assertIn(f'href="/s/{l["token"]}/b/2026"', r.get_data(as_text=True))
        r = self.call("POST", f"/wopi/files/{fid}/contents", token, data=b"PK-xlsx2",
                      headers={"X-WOPI-Override": "PUT", "X-WOPI-Lock": "L"})
        self.assertEqual(r.status_code, 200)
        with open(os.path.join(self.fotos, "2026", "Tabelle.xlsx"), "rb") as fh:
            self.assertEqual(fh.read(), b"PK-xlsx2")

    def test_save_respects_size_limit(self):
        self.opts["share_max_upload_mb"] = 1
        l = self.link(allow_edit=True)
        _, token, fid, _ = self.open_editor(l)
        r = self.call("POST", f"/wopi/files/{fid}/contents", token, data=b"x" * (1024 * 1024 + 10),
                      headers={"X-WOPI-Override": "PUT", "X-WOPI-Lock": "L"})
        self.assertEqual(r.status_code, 413)
        with open(os.path.join(self.fotos, "Bericht.docx"), "rb") as fh:
            self.assertEqual(fh.read(), b"PK-original")


if __name__ == "__main__":
    unittest.main()
