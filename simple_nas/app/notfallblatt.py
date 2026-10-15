#!/usr/bin/env python3
"""Emergency sheet ("Notfallblatt") for an external backup job.

A printable page with everything needed to get the data back WITHOUT Home
Assistant: where the data lives, the archive password (the one thing that
cannot be recovered anywhere else), and how to restore - with the Notfall-
Paket (notfall/ in the repository) or by hand with restic/rclone.

Provider secrets (box password, S3 secret, keys, a pasted rclone config) are
NOT printed: they can be reset at the provider. Only the archive password is,
because without it an encrypted backup is lost for good.

Pure function, no Flask: render(job, lang, now) -> HTML string.
"""
import copy
import html

import remote_backup as rb

REPO_URL = "https://github.com/gregorwolf1973/EasyNas-HASSIO-Addon"

PROVIDER_NAMES = {
    "de": {"hetzner": "Hetzner Storage Box", "sftp": "SFTP-Server", "s3": "S3-Speicher",
           "b2": "Backblaze B2", "webdav": "WebDAV", "smb": "SMB-Freigabe (NAS)",
           "local": "Lokaler Ordner / zweite Platte", "rclone": "eigene rclone-Konfiguration"},
    "en": {"hetzner": "Hetzner Storage Box", "sftp": "SFTP server", "s3": "S3 storage",
           "b2": "Backblaze B2", "webdav": "WebDAV", "smb": "SMB share (NAS)",
           "local": "Local folder / second disk", "rclone": "custom rclone config"},
}

FIELD_NAMES_EN = {
    "host": "Server", "port": "Port", "user": "User", "s3_provider": "S3 provider",
    "endpoint": "Endpoint", "region": "Region", "access_key": "Access key", "bucket": "Bucket",
    "b2_account": "Key ID", "url": "URL", "vendor": "Vendor", "domain": "Domain",
    "share": "Share", "path": "Folder",
}

# Characters that are easily mixed up when copying by hand.
AMBIGUOUS = {
    "de": {"l": "klein L", "I": "groß i", "1": "Eins", "|": "Strich", "O": "groß O", "o": "klein o",
           "0": "Null", " ": "Leerzeichen", "`": "Gravis", "'": "Apostroph", "´": "Akut",
           "-": "Minus", "_": "Unterstrich", ".": "Punkt", ",": "Komma"},
    "en": {"l": "lower L", "I": "upper i", "1": "one", "|": "bar", "O": "upper O", "o": "lower o",
           "0": "zero", " ": "space", "`": "backtick", "'": "apostrophe", "´": "acute",
           "-": "minus", "_": "underscore", ".": "dot", ",": "comma"},
}

TEXT = {
    "de": {
        "title": "Notfallblatt", "made": "Erstellt am {d} von Simple NAS · Job-ID {id}",
        "print": "Drucken",
        "confidential": "<b>Vertraulich.</b> Wer dieses Blatt und den Zugang zum Speicher hat, kann alle "
                        "gesicherten Daten lesen. Ausdrucken und sicher aufbewahren – <b>nicht</b> auf dem NAS "
                        "oder in Home Assistant ablegen. Nach jeder Änderung am Job neu drucken.",
        "where": "1. Wo liegen die Daten", "engine": "Verfahren", "provider": "Anbieter",
        "remote_path": "Pfad auf dem Ziel", "provider_pw": "Passwort / Zugang des Anbieters",
        "provider_pw_hetzner": "steht bewusst nicht hier – vergessen? In der Hetzner Console "
                               "(Storage Box → Passwort zurücksetzen) neu setzen, die Daten bleiben erhalten.",
        "provider_pw_other": "steht bewusst nicht hier – beim Anbieter neu setzen bzw. neuen Schlüssel erzeugen.",
        "handwrite": "von Hand eintragen, falls gewünscht:",
        "engine_restic": "restic – verschlüsseltes Archiv mit Sicherungsständen",
        "engine_rclone": "rclone – Dateien 1:1 beim Anbieter",
        "engine_rclone_crypt": "rclone – Dateien 1:1 beim Anbieter, verschlüsselt (rclone crypt)",
        "pw": "2. Archiv-Passwort", "pw_warn": "<b>Ohne dieses Passwort sind die Daten verloren</b> – "
               "auch Hetzner bzw. der Anbieter kann sie nicht entschlüsseln.",
        "pw_none": "Dieser Job verschlüsselt nicht – es gibt kein Archiv-Passwort.",
        "pw_chars": "{n} Zeichen. Unter verwechselbaren Zeichen steht, was gemeint ist.",
        "what": "3. Was gesichert wird", "sources": "Quellordner", "last": "Letztes Backup",
        "never": "noch keines", "snap_host": "restic-Host / Tag der Stände",
        "auto": "Wird nur Home Assistant neu installiert (gleiche Hardware, /config erhalten), kommen die "
                "Jobs samt Passwort mit dem Add-on zurück (Kopie unter /config/.simplenas/auto).",
        "restore": "4. Wiederherstellen ohne Home Assistant",
        "pkg_title": "Mit dem Notfall-Paket (Windows oder Linux)",
        "pkg_steps": "ZIP <code>SimpleNAS-Notfall-…zip</code> auspacken (liegt auf USB-Stick o. Ä.), "
                     "unter Windows <code>WIEDERHERSTELLEN-Windows.cmd</code> doppelklicken, unter Linux "
                     "<code>bash wiederherstellen-linux.sh</code>. Das Tool fragt:",
        "pkg_box": "Storage Box", "pkg_user": "Benutzer", "pkg_port": "Port", "pkg_path": "Pfad auf der Box",
        "pkg_boxpw": "Passwort der Storage Box", "pkg_archpw": "Archiv-Passwort", "pkg_see": "siehe 2.",
        "pkg_boxpw_see": "nicht auf dem Blatt – siehe 1.",
        "local_copy": "Der Ordner {p} enthält die Dateien direkt – einfach kopieren (ohne {v}).",
        "pkg_where": "Kein Paket zur Hand? Es lässt sich aus dem Repository neu bauen: {u} → Ordner "
                     "<code>notfall/</code>, <code>bash notfall/paket-bauen.sh</code>.",
        "pkg_only": "Das Notfall-Paket kennt nur restic auf einer Hetzner Storage Box bzw. einem SFTP-Server "
                    "mit Passwort – für dieses Ziel bitte den Weg von Hand nehmen.",
        "manual_title": "Von Hand mit restic und rclone",
        "manual_intro": "restic und rclone von restic.net bzw. rclone.org holen. Diese Konfiguration als "
                        "Datei <code>notfall.conf</code> speichern; Werte in ‹…› ersetzen – Passwörter vorher "
                        "mit <code>rclone obscure PASSWORT</code> umwandeln:",
        "manual_cmds": "Dann (Windows: <code>set</code> statt <code>export</code>):",
        "manual_custom": "Dieser Job nutzt eine eigene rclone-Konfiguration. Sie steht nicht auf dem Blatt – "
                         "bitte separat aufbewahren. Der Abschnitt darin heißt im Add-on <code>{s}</code>.",
        "ph_pw": "rclone obscure: Passwort des Anbieters", "ph_archpw": "rclone obscure: Archiv-Passwort (2.)",
        "ph_secret": "geheim – beim Anbieter nachsehen", "ph_key": "privater Schlüssel, eine Zeile mit \\n",
        "target_dir": "wiederhergestellt",
    },
    "en": {
        "title": "Emergency sheet", "made": "Created {d} by Simple NAS · job ID {id}",
        "print": "Print",
        "confidential": "<b>Confidential.</b> Anyone holding this sheet and access to the storage can read all "
                        "backed-up data. Print it and keep it safe – <b>not</b> on the NAS or in Home Assistant. "
                        "Print again after every change to the job.",
        "where": "1. Where the data is", "engine": "Method", "provider": "Provider",
        "remote_path": "Path on the target", "provider_pw": "Provider password / access",
        "provider_pw_hetzner": "deliberately not printed – forgotten? Reset it in the Hetzner Console "
                               "(Storage Box → reset password); the data is kept.",
        "provider_pw_other": "deliberately not printed – reset it or create a new key at the provider.",
        "handwrite": "write it in by hand if you like:",
        "engine_restic": "restic – encrypted archive with snapshots",
        "engine_rclone": "rclone – files 1:1 at the provider",
        "engine_rclone_crypt": "rclone – files 1:1 at the provider, encrypted (rclone crypt)",
        "pw": "2. Archive password", "pw_warn": "<b>Without this password the data is lost</b> – "
               "Hetzner or the provider cannot decrypt it either.",
        "pw_none": "This job does not encrypt – there is no archive password.",
        "pw_chars": "{n} characters. Easily confused characters are explained below them.",
        "what": "3. What is backed up", "sources": "Source folders", "last": "Last backup",
        "never": "none yet", "snap_host": "restic host / tag of the snapshots",
        "auto": "If only Home Assistant is reinstalled (same hardware, /config kept), the jobs and the "
                "password come back with the add-on (copy in /config/.simplenas/auto).",
        "restore": "4. Restoring without Home Assistant",
        "pkg_title": "With the emergency package (Windows or Linux)",
        "pkg_steps": "Unpack <code>SimpleNAS-Notfall-…zip</code> (kept on a USB stick or similar); on Windows "
                     "double-click <code>WIEDERHERSTELLEN-Windows.cmd</code>, on Linux run "
                     "<code>bash wiederherstellen-linux.sh</code>. The tool asks for:",
        "pkg_box": "Storage Box", "pkg_user": "User", "pkg_port": "Port", "pkg_path": "Path on the box",
        "pkg_boxpw": "Storage Box password", "pkg_archpw": "Archive password", "pkg_see": "see 2.",
        "pkg_boxpw_see": "not on this sheet – see 1.",
        "local_copy": "The folder {p} holds the files as they are – just copy them (without {v}).",
        "pkg_where": "No package at hand? Rebuild it from the repository: {u} → folder <code>notfall/</code>, "
                     "<code>bash notfall/paket-bauen.sh</code>.",
        "pkg_only": "The emergency package only handles restic on a Hetzner Storage Box or an SFTP server "
                    "with a password – use the manual way for this target.",
        "manual_title": "By hand with restic and rclone",
        "manual_intro": "Get restic and rclone from restic.net and rclone.org. Save this configuration as "
                        "<code>notfall.conf</code>; replace the values in ‹…› – convert passwords with "
                        "<code>rclone obscure PASSWORD</code> first:",
        "manual_cmds": "Then (Windows: <code>set</code> instead of <code>export</code>):",
        "manual_custom": "This job uses a custom rclone configuration. It is not printed here – keep it "
                         "separately. Its section is called <code>{s}</code> in the add-on.",
        "ph_pw": "rclone obscure: provider password", "ph_archpw": "rclone obscure: archive password (2.)",
        "ph_secret": "secret – look it up at the provider", "ph_key": "private key, one line with \\n",
        "target_dir": "restored",
    },
}

CSS = """
*{box-sizing:border-box}
body{font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#111;background:#fff;
  max-width:820px;margin:24px auto;padding:0 16px}
h1{font-size:24px;margin:0 0 2px}h2{font-size:16px;margin:22px 0 8px;border-bottom:2px solid #111;padding-bottom:3px}
h3{font-size:14px;margin:14px 0 6px}
.sub{color:#555;font-size:12.5px}
.box{border:1.5px solid #b00;background:#fff5f5;padding:8px 12px;margin:12px 0;border-radius:4px}
table.kv{border-collapse:collapse;width:100%}table.kv td{border-bottom:1px solid #ddd;padding:5px 6px;vertical-align:top}
table.kv td:first-child{width:34%;color:#444}
.mono,code,pre{font-family:Consolas,"DejaVu Sans Mono",Menlo,monospace}
pre{background:#f4f4f4;border:1px solid #ddd;padding:8px 10px;white-space:pre-wrap;word-break:break-all;font-size:12.5px}
.pw{display:flex;flex-wrap:wrap;gap:3px;margin:8px 0}
.pw span{display:inline-flex;flex-direction:column;align-items:center;min-width:30px;border:1px solid #999;padding:3px 2px 2px}
.pw b{font:600 20px Consolas,"DejaVu Sans Mono",Menlo,monospace;white-space:pre}
.pw i{font-size:10.5px;color:#555;font-style:normal;line-height:1.1;text-align:center;min-height:10px}
.pw small{font-size:9px;color:#999}
.line{display:inline-block;min-width:260px;border-bottom:1px solid #111;height:1.2em}
button{font:inherit;padding:6px 16px;cursor:pointer}
@media print{button{display:none}body{margin:0;max-width:none}h2{break-after:avoid}pre,.pw,table{break-inside:avoid}}
"""


def _e(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def _password_cells(pw, lang):
    amb = AMBIGUOUS[lang]
    cells = []
    for i, ch in enumerate(pw, 1):
        cells.append(f"<span><b>{_e(ch)}</b><i>{_e(amb.get(ch, ''))}</i><small>{i}</small></span>")
    return '<div class="pw">' + "".join(cells) + "</div>"


def _masked_job(job, tx):
    """Copy of the job with every secret replaced by a placeholder and the
    section names shortened to t_nas / c_nas."""
    j = copy.deepcopy(job)
    j["id"] = "nas"
    t = j.setdefault("target", {})
    for f in ("password",):
        if t.get(f):
            t[f] = "‹" + tx["ph_pw"] + "›"
    if t.get("key_pem"):
        t["key_pem"] = "‹" + tx["ph_key"] + "›"
    for f in ("secret_key", "b2_key"):
        if t.get(f):
            t[f] = "‹" + tx["ph_secret"] + "›"
    if j.get("password"):
        j["password"] = "‹" + tx["ph_archpw"] + "›"
    return j


def _manual_block(job, tx):
    prov = (job.get("target") or {}).get("provider")
    out = [f"<h3>{_e(tx['manual_title'])}</h3>"]
    target_dir = tx["target_dir"]
    engine = job.get("engine")
    sources = job.get("sources") or []
    include = f"restore latest --target ./{target_dir} --include {sources[0]}" if sources else ""
    if prov == "local" and engine == "restic":
        # A local repository needs no rclone at all.
        path = rb._base(job)
        cmds = (f"restic -r \"{path}\" snapshots\n"
                f"restic -r \"{path}\" restore latest --target ./{target_dir}"
                + (f"\nrestic -r \"{path}\" {include}" if include else ""))
        out.append(f"<pre>{_e(cmds)}</pre>")
        return "".join(out)
    if prov == "local" and not job.get("encrypt"):
        out.append(f"<p>{tx['local_copy'].format(p='<code>' + _e(rb._base(job)) + '</code>', v=_e(rb.VERSIONS_DIR))}</p>")
        return "".join(out)
    if prov == "rclone":
        out.append(f"<p>{tx['manual_custom'].format(s=_e('t_' + str(job.get('id', ''))))}</p>")
        return "".join(out)
    m = _masked_job(job, tx)
    conf = rb.build_rclone_conf(m, obscure=lambda s: s).strip()
    out.append(f"<p>{tx['manual_intro']}</p><pre>{_e(conf)}</pre><p>{tx['manual_cmds']}</p>")
    if engine == "restic":
        repo = rb.restic_repo(m)
        cmds = ("export RCLONE_CONFIG=notfall.conf\n"
                f"restic -r {repo} snapshots\n"
                f"restic -r {repo} ls latest /\n"
                f"restic -r {repo} restore latest --target ./{target_dir}"
                + (f"\nrestic -r {repo} {include}" if include else ""))
    else:
        root = rb.rclone_root(m)
        cmds = ("export RCLONE_CONFIG=notfall.conf\n"
                f"rclone lsd {root}\n"
                f"rclone copy {root} ./{target_dir} --exclude \"/{rb.VERSIONS_DIR}/**\" --progress")
    out.append(f"<pre>{_e(cmds)}</pre>")
    return "".join(out)


def _package_supported(job):
    t = job.get("target") or {}
    if job.get("engine") != "restic":
        return False
    if t.get("provider") == "hetzner":
        return True
    return t.get("provider") == "sftp" and bool(t.get("password")) and not t.get("key_pem")


def render(job, lang="de", now=None):
    lang = lang if lang in TEXT else "de"
    tx = TEXT[lang]
    t = job.get("target") or {}
    prov = t.get("provider", "")
    engine = job.get("engine", "")
    when = (now.strftime("%d.%m.%Y %H:%M") if lang == "de" else now.strftime("%Y-%m-%d %H:%M")) if now else ""

    def label(f):
        return rb.FIELD_LABELS.get(f, f) if lang == "de" else FIELD_NAMES_EN.get(f, f)

    rows = [(tx["engine"], tx["engine_restic"] if engine == "restic"
             else tx["engine_rclone_crypt"] if job.get("encrypt") else tx["engine_rclone"]),
            (tx["provider"], PROVIDER_NAMES[lang].get(prov, prov))]
    for f in rb.TARGET_FIELDS.get(prov, ()):
        if f in rb.SECRET_TARGET_FIELDS:
            continue
        v = t.get(f)
        if f == "port" and not v and prov in ("hetzner", "sftp"):
            v = 22
        if v not in (None, ""):
            rows.append((label(f), v))
    if prov != "local":
        rows.append((tx["remote_path"], job.get("remote_path") or "/"))
    kv = "".join(f'<tr><td>{_e(k)}</td><td class="mono">{_e(v)}</td></tr>' for k, v in rows)
    if prov not in ("local",):
        note = tx["provider_pw_hetzner"] if prov == "hetzner" else tx["provider_pw_other"]
        kv += (f'<tr><td>{_e(tx["provider_pw"])}</td><td>{_e(note)}<br><span class="sub">'
               f'{_e(tx["handwrite"])}</span> <span class="line"></span></td></tr>')

    pw = job.get("password") or ""
    has_pw = bool(pw) and (engine == "restic" or job.get("encrypt"))
    if has_pw:
        pw_html = (f'<div class="box">{tx["pw_warn"]}</div>'
                   f'<pre style="font-size:18px">{_e(pw)}</pre>'
                   f'{_password_cells(pw, lang)}<div class="sub">{_e(tx["pw_chars"].format(n=len(pw)))}</div>')
    else:
        pw_html = f"<p>{_e(tx['pw_none'])}</p>"

    last = (job.get("last_backup") or {})
    last_txt = f"{last.get('at', '').replace('T', ' ')} ({last.get('status', '')})" if last.get("at") else tx["never"]
    what = [(tx["sources"], "\n".join(job.get("sources") or [])), (tx["last"], last_txt)]
    if engine == "restic":
        what.append((tx["snap_host"], f"{rb.RESTIC_HOST} / {rb.RESTIC_TAG}"))
    what_html = "".join(f'<tr><td>{_e(k)}</td><td class="mono" style="white-space:pre-line">{_e(v)}</td></tr>'
                        for k, v in what)

    if _package_supported(job):
        port = t.get("port") or 22
        pkg_rows = [(tx["pkg_box"], t.get("host", "")), (tx["pkg_user"], t.get("user", "")),
                    (tx["pkg_port"], port), (tx["pkg_path"], job.get("remote_path") or "/")]
        pkg = "".join(f'<tr><td>{_e(k)}</td><td class="mono">{_e(v)}</td></tr>' for k, v in pkg_rows)
        pkg += (f'<tr><td>{_e(tx["pkg_boxpw"])}</td><td>{_e(tx["pkg_boxpw_see"])}</td></tr>'
                f'<tr><td>{_e(tx["pkg_archpw"])}</td><td>{_e(tx["pkg_see"])}</td></tr>')
        pkg_html = (f"<h3>{_e(tx['pkg_title'])}</h3><p>{tx['pkg_steps']}</p><table class=\"kv\">{pkg}</table>"
                    f"<p class=\"sub\">{tx['pkg_where'].format(u=_e(REPO_URL))}</p>")
    else:
        pkg_html = f"<p class=\"sub\">{_e(tx['pkg_only'])}</p>"

    name = job.get("name", "")
    return f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{_e(tx['title'])} – {_e(name)}</title><style>{CSS}</style></head><body>
<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
<div><h1>{_e(tx['title'])} – {_e(name)}</h1>
<div class="sub">{_e(tx['made'].format(d=when, id=job.get('id', '')))}</div></div>
<button onclick="window.print()">{_e(tx['print'])}</button></div>
<div class="box">{tx['confidential']}</div>
<h2>{_e(tx['where'])}</h2><table class="kv">{kv}</table>
<h2>{_e(tx['pw'])}</h2>{pw_html}
<h2>{_e(tx['what'])}</h2><table class="kv">{what_html}</table><p class="sub">{_e(tx['auto'])}</p>
<h2>{_e(tx['restore'])}</h2>{pkg_html}{_manual_block(job, tx)}
</body></html>
"""
