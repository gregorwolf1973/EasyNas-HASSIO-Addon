#!/usr/bin/env python3
"""The public share site - a separate Flask app on its own port.

Never imports app.py. Serves only /healthz, the portal, /s/<token>/... and,
for Collabora, /wopi/files/... - nothing else exists on this port, and
assert_public_surface() refuses to start if anything else ever gets
registered here.
"""

import hmac
import ipaddress
import datetime
import mimetypes
import os
import random
import secrets
import shutil
import tempfile
import time
from datetime import timedelta
from urllib.parse import quote

from flask import (Flask, abort, g, jsonify, make_response, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.security import check_password_hash

import accesslog
import fileicons
import safepath
import sharing_store
import wopi
import zipstream
from ratelimit import (AUTHFAIL_IP, AUTHFAIL_LINK, AUTHFAIL_USER, LIMITER, REQ_PER_IP, UPLOAD_PER_IP,
                       SCAN_PER_IP, LOCK_BASE, LOCK_CAP)

RUNNING = False

# Every endpoint that may exist on the public app. Anything else = refuse to start.
PUBLIC_ENDPOINTS = frozenset({
    "healthz", "share_root", "share_landing", "share_auth", "share_logout",
    "share_browse", "share_download", "share_view", "share_lang", "share_zip",
    "share_root_login", "share_home", "share_home_logout", "share_upload", "share_upload_form",
    "share_edit", "wopi_file", "wopi_contents", "share_delete",
})

DELETE_PER_IP = (120, 60 * 60)     # a person tidying up, not a script emptying the share

# Authorised by the WOPI access token, not by the link token or the session.
WOPI_ENDPOINTS = ("wopi_file", "wopi_contents")

SCAN_HOOK = None   # set by the ClamAV integration: scan(path) -> (verdict, detail)
DISCOVERY = wopi.Discovery()   # module-level so the cache outlives one app and tests can swap the fetcher


# Only these are shown inline. Everything else is an attachment: a user-uploaded
# HTML or SVG served from this origin would be stored XSS with the cookie attached.
INLINE_MIME = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/avif",
    "video/mp4", "video/webm", "audio/mpeg", "audio/ogg", "audio/wav", "audio/flac",
    "application/pdf", "text/plain",
}

LANG = {
    "de": {
        "title": "Freigabe", "not_found": "Link nicht gefunden oder nicht mehr gültig.",
        "not_found_sub": "Der Link ist unbekannt, abgelaufen, deaktiviert oder das Limit ist erreicht.",
        "error": "Da ist etwas schiefgegangen.", "error_sub": "Bitte später noch einmal versuchen.",
        "password": "Passwort", "username": "Benutzername", "unlock": "Öffnen", "login": "Anmelden",
        "protected": "Diese Freigabe ist geschützt.", "login_needed": "Bitte anmelden.",
        "wrong": "Zugang verweigert.", "locked": "Zu viele Fehlversuche. Bitte später erneut versuchen.",
        "download": "Herunterladen", "logout": "Abmelden", "folder": "Ordner", "file": "Datei",
        "files": "Dateien", "empty": "Dieser Ordner ist leer.", "up": "Zurück",
        "size": "Größe", "modified": "Geändert", "name": "Name",
        "upload_soon": "Das Hochladen ist noch nicht verfügbar.",
        "shared_by": "Freigegeben über Simple NAS", "too_many": "Zu viele Anfragen.",
        "zip": "Ordner als ZIP", "zip_too_big": "Dieser Ordner ist zu groß für einen ZIP-Download. Bitte Dateien einzeln laden.",
        "portal_title": "Anmeldung", "portal_sub": "Mit deinem Freigabe-Konto anmelden, um deine Freigaben zu sehen.",
        "my_shares": "Meine Freigaben", "no_shares": "Für dieses Konto sind keine Freigaben eingerichtet.",
        "mode": "Modus", "expires": "Gültig bis", "open": "Öffnen",
        "mode_download": "Herunterladen", "mode_upload": "Ablage", "mode_both": "Herunterladen + Ablage",
        "upload_title": "Dateien hochladen", "upload_hint": "Dateien hierher ziehen oder auswählen.",
        "upload_choose": "Dateien auswählen", "upload_done": "fertig", "upload_failed": "fehlgeschlagen",
        "upload_max": "Maximal {mb} MB je Datei", "upload_ok": "Hochgeladen als",
        "err_too_big": "Datei zu groß", "err_quota": "Kontingent dieses Links erschöpft", "err_ext": "Dateityp nicht erlaubt",
        "err_length": "Größe der Datei fehlt", "err_upload": "Upload fehlgeschlagen", "err_scan": "Datei abgelehnt (Virenprüfung)",
        "err_scan_unavailable": "Virenprüfung nicht erreichbar, Upload abgelehnt", "err_rate": "Zu viele Uploads, bitte später erneut",
        "edit": "Bearbeiten", "open_office": "Im Browser öffnen", "guest": "Gast",
        "edit_unavailable": "Der Dokumenten-Editor ist gerade nicht erreichbar. Bitte später erneut versuchen.",
        "editor_loading": "Dokument wird geöffnet …",
        "theme_toggle": "Hell/Dunkel umschalten",
        "delete": "Löschen", "confirm_delete_file": "„{name}“ wirklich löschen?",
        "confirm_delete_folder": "Ordner „{name}“ mit allem Inhalt wirklich löschen?",
        "err_in_use": "Die Datei ist gerade im Editor geöffnet und kann nicht gelöscht werden.",
        "err_delete": "Löschen fehlgeschlagen.",
    },
    "en": {
        "title": "Share", "not_found": "Link not found or no longer valid.",
        "not_found_sub": "The link is unknown, expired, disabled or its limit has been reached.",
        "error": "Something went wrong.", "error_sub": "Please try again later.",
        "password": "Password", "username": "Username", "unlock": "Open", "login": "Sign in",
        "protected": "This share is protected.", "login_needed": "Please sign in.",
        "wrong": "Access denied.", "locked": "Too many failed attempts. Please try again later.",
        "download": "Download", "logout": "Sign out", "folder": "Folder", "file": "File",
        "files": "files", "empty": "This folder is empty.", "up": "Back",
        "size": "Size", "modified": "Modified", "name": "Name",
        "upload_soon": "Uploading is not available yet.",
        "shared_by": "Shared via Simple NAS", "too_many": "Too many requests.",
        "zip": "Folder as ZIP", "zip_too_big": "This folder is too large for a ZIP download. Please download files individually.",
        "portal_title": "Sign in", "portal_sub": "Sign in with your share account to see your shares.",
        "my_shares": "My shares", "no_shares": "No shares are set up for this account.",
        "mode": "Mode", "expires": "Valid until", "open": "Open",
        "mode_download": "Download", "mode_upload": "Drop box", "mode_both": "Download + drop box",
        "upload_title": "Upload files", "upload_hint": "Drop files here or choose them.",
        "upload_choose": "Choose files", "upload_done": "done", "upload_failed": "failed",
        "upload_max": "Up to {mb} MB per file", "upload_ok": "Uploaded as",
        "err_too_big": "File too large", "err_quota": "This link's quota is used up", "err_ext": "File type not allowed",
        "err_length": "File size missing", "err_upload": "Upload failed", "err_scan": "File rejected (virus scan)",
        "err_scan_unavailable": "Virus scanner unreachable, upload rejected", "err_rate": "Too many uploads, please try again later",
        "edit": "Edit", "open_office": "Open in browser", "guest": "Guest",
        "edit_unavailable": "The document editor is not reachable right now. Please try again later.",
        "editor_loading": "Opening document …",
        "theme_toggle": "Toggle light/dark",
        "delete": "Delete", "confirm_delete_file": "Really delete “{name}”?",
        "confirm_delete_folder": "Really delete the folder “{name}” and everything in it?",
        "err_in_use": "The file is open in the editor right now and cannot be deleted.",
        "err_delete": "Delete failed.",
    },
}


def content_disposition(filename, inline=False):
    import re
    import unicodedata
    ascii_fb = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode() or "download"
    ascii_fb = re.sub(r'[\\"\r\n;]', "_", ascii_fb)
    return (f'{"inline" if inline else "attachment"}; filename="{ascii_fb}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}")


def fmt_size(n):
    if n is None:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def create_share_app(opt, load_shares, share_roots, data_dir):
    """opt(key, default) reads add-on options; load_shares() returns shares.json."""
    tdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates_share")
    app = Flask("simple_nas_share", template_folder=tdir, static_folder=None)

    # ── session: own key, own cookie, never shared with the admin UI ─────────
    auth_file = os.path.join(data_dir, "share_auth.json")
    key = None
    try:
        import json
        with open(auth_file) as f:
            key = json.load(f).get("secret_key")
    except (OSError, ValueError):
        pass
    if not key:
        key = secrets.token_hex(32)
        try:
            import json
            os.makedirs(data_dir, exist_ok=True)
            with open(auth_file, "w") as f:
                json.dump({"secret_key": key}, f)
        except OSError:
            pass
    app.secret_key = key
    app.config.update(
        SESSION_COOKIE_NAME="nas_share",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(opt("share_cookie_secure", True)),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=int(opt("share_session_hours", 8) or 8)),
        PROPAGATE_EXCEPTIONS=False,
        MAX_CONTENT_LENGTH=int(opt("share_max_upload_mb", 1024) or 1024) * 1024 * 1024 + 8 * 1024 * 1024,
    )

    accesslog.init(os.path.join(data_dir, "share_access.log"), opt("share_log_max_mb", 5))

    # ── proxy awareness ─────────────────────────────────────────────────────
    def trusted_nets():
        nets = []
        for n in opt("share_trusted_proxies", ["127.0.0.1", "::1", "172.30.32.0/23"]) or []:
            try:
                nets.append(ipaddress.ip_network(str(n), strict=False))
            except ValueError:
                pass
        return nets

    def from_trusted_proxy():
        try:
            ip = ipaddress.ip_address(request.remote_addr or "")
        except ValueError:
            return False
        return any(ip in n for n in trusted_nets())

    def ext_ip():
        """Address used for rate limits and the log.

        Behind a Cloudflare tunnel the proxy chain is cloudflared -> Nginx Proxy
        Manager -> here, so X-Forwarded-For only names the tunnel container.
        Cloudflare carries the visitor in CF-Connecting-IP; honour it, but only
        when the request really came from a trusted proxy."""
        if from_trusted_proxy():
            cf = request.headers.get("CF-Connecting-IP", "").strip()
            if cf:
                return cf
            xff = request.headers.get("X-Forwarded-For", "")
            if xff:
                return xff.split(",")[-1].strip() or request.remote_addr
        return request.remote_addr or "?"

    def ext_scheme():
        if from_trusted_proxy():
            return request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip()
        return request.scheme

    app.jinja_env.globals["fmt_size"] = fmt_size
    app.jinja_env.globals["file_icon"] = fileicons.icon

    # ── language ────────────────────────────────────────────────────────────
    def pick_lang():
        q = request.args.get("lang")
        if q in LANG:
            session["lang"] = q
        lg = session.get("lang")
        if lg in LANG:
            return lg
        al = (request.headers.get("Accept-Language") or "").lower()
        return "de" if al.startswith("de") or ",de" in al else "en"

    def csrf():
        tok = session.get("csrf")
        if not tok:
            tok = secrets.token_urlsafe(32)
            session["csrf"] = tok
        return tok

    def csrf_ok():
        sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token", "")
        return bool(sent) and hmac.compare_digest(sent, session.get("csrf", ""))

    def render(tpl, status=200, **ctx):
        L = LANG[pick_lang()]
        resp = make_response(render_template(tpl, L=L, lang=pick_lang(), csrf=csrf(), **ctx), status)
        return resp

    def not_found():
        # One identical page for unknown, disabled, expired, exhausted and
        # broken links. Nothing here may depend on which case it was.
        return render("notfound.html", 404)

    # ── the authorization chain ─────────────────────────────────────────────
    def resolve_link(token):
        link = sharing_store.resolve_token(token)
        if not link or not sharing_store.link_is_live(link):
            return None
        try:
            root = sharing_store.validate_root(link["root"], link.get("share", ""), link.get("file", ""),
                                               load_shares(), share_roots())
        except sharing_store.ShareError:
            return None
        link["_real_root"] = root
        return link

    def authed(link):
        if link["access"] == "public":
            return True
        if link["access"] == "password":
            return session.get(f"lnk:{link['id']}") == link.get("auth_epoch", 1)
        if link["access"] == "users":
            user = session.get("su")
            if not user:
                return False
            acc = sharing_store.get_account(user)
            if not acc or not acc.get("enabled", True):
                return False
            if session.get("su_epoch") != acc.get("auth_epoch", 1):
                return False
            allowed = link.get("users") or []
            return (not allowed) or any(u.lower() == user.lower() for u in allowed)
        return False

    @app.before_request
    def gate():
        g.ip = ext_ip()
        if LIMITER.banned(f"ban:{g.ip}"):
            resp = render("error.html", 429, msg_key="too_many")
            resp.headers["Retry-After"] = str(LIMITER.ban_remaining(f"ban:{g.ip}"))
            return resp
        ok, retry = LIMITER.hit(f"ip:{g.ip}", *REQ_PER_IP)
        if not ok:
            accesslog.log("rate_limited", ip=g.ip, path=request.path[:80])
            resp = render("error.html", 429, msg_key="too_many")
            resp.headers["Retry-After"] = str(retry)
            return resp
        if request.endpoint in ("healthz", "share_root", "share_lang", "share_root_login",
                                "share_home", "share_home_logout", None) + WOPI_ENDPOINTS:
            return
        token = (request.view_args or {}).get("token", "")
        if not sharing_store.token_shape_ok(token):
            return not_found()
        link = resolve_link(token)
        if link is None:
            accesslog.log("link_404", ip=g.ip, ua=request.headers.get("User-Agent"))
            # Someone probing many unknown links is scanning; ban the address.
            LIMITER.record(f"scan:{g.ip}")
            if LIMITER.count(f"scan:{g.ip}", SCAN_PER_IP[1]) >= SCAN_PER_IP[0]:
                dur = LIMITER.lock(f"ban:{g.ip}", LOCK_BASE, LOCK_CAP)
                accesslog.log("rate_limited", ip=g.ip, detail=f"scanner ban {dur // 60} min")
            return not_found()
        g.link = link
        if request.endpoint in ("share_landing", "share_auth", "share_logout"):
            return
        if not authed(link):
            return redirect(url_for("share_landing", token=token))
        if link["mode"] == "upload" and request.endpoint in ("share_browse", "share_download", "share_view",
                                                             "share_zip", "share_edit", "share_delete"):
            abort(403)

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        # Only the editor page may frame (and post its form to) exactly the
        # configured Collabora origin. Nothing may ever frame this site.
        frame = getattr(g, "frame_origin", "")
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; media-src 'self'; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            + (f"frame-src {frame}; form-action 'self' {frame}; " if frame else "form-action 'self'; ")
            + "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "private, no-store"
        if ext_scheme() == "https":
            resp.headers["Strict-Transport-Security"] = "max-age=15552000"
        resp.headers.pop("Server", None)
        return resp

    @app.errorhandler(403)
    def _403(e):
        return render("error.html", 403, msg_key="wrong")

    @app.errorhandler(404)
    def _404(e):
        return not_found()

    @app.errorhandler(405)
    def _405(e):
        # wrong method on a known path: reveal nothing, same page as unknown
        return not_found()

    @app.errorhandler(Exception)
    def _500(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e
        print(f"[SHARE] Fehler: {type(e).__name__}: {e}", flush=True)
        return render("error.html", 500, msg_key="error")

    # ── routes ──────────────────────────────────────────────────────────────
    @app.route("/healthz")
    def healthz():
        return "ok", 200, {"Content-Type": "text/plain"}

    def fail(key, limit_window):
        """Record an auth failure; when the window is full, lock the key with
        escalating duration so repeat offenders wait longer each time."""
        LIMITER.record(key)
        limit, window = limit_window
        if LIMITER.count(key, window) >= limit:
            dur = LIMITER.lock(key, LOCK_BASE, LOCK_CAP)
            accesslog.log("rate_limited", ip=g.ip, detail=f"lock {key.split(':')[0]}:{key.split(':')[1]} {dur // 60} min")

    def account_session():
        """The logged-in share account, or None. Same session keys the
        per-link login uses, so a portal login also opens users-only links."""
        user = session.get("su")
        if not user:
            return None
        acc = sharing_store.get_account(user)
        if not acc or not acc.get("enabled", True) or session.get("su_epoch") != acc.get("auth_epoch", 1):
            return None
        return acc

    def portal_locked():
        return LIMITER.locked(f"authfail:ip:{g.ip}", *AUTHFAIL_IP)

    @app.route("/")
    def share_root():
        if account_session():
            return redirect(url_for("share_home"))
        return render("home_login.html", locked=portal_locked(), error=None)

    @app.route("/login", methods=["POST"])
    def share_root_login():
        if not csrf_ok():
            return render("home_login.html", locked=False, error="wrong")
        if portal_locked():
            accesslog.log("rate_limited", ip=g.ip, detail="portal auth locked")
            return render("home_login.html", locked=True, error="locked")
        user = request.form.get("username", "").strip()
        user_key = f"authfail:user:{user.lower()}"
        if user and LIMITER.locked(user_key, *AUTHFAIL_USER):
            accesslog.log("rate_limited", ip=g.ip, user=user, detail="portal user locked")
            return render("home_login.html", locked=True, error="locked")
        acc = sharing_store.check_account_password(user, request.form.get("password", ""))
        if not acc:
            time.sleep(random.uniform(0.15, 0.35))
            fail(f"authfail:ip:{g.ip}", AUTHFAIL_IP)
            if user:
                fail(user_key, AUTHFAIL_USER)
            accesslog.log("auth_fail", ip=g.ip, user=user, detail="portal", ua=request.headers.get("User-Agent"))
            return render("home_login.html", locked=False, error="wrong")
        session.permanent = True
        session["su"] = acc["username"]
        session["su_epoch"] = acc.get("auth_epoch", 1)
        sharing_store.touch_login(acc["username"])
        accesslog.log("auth_ok", ip=g.ip, user=acc["username"], detail="portal")
        return redirect(url_for("share_home"))

    @app.route("/home")
    def share_home():
        acc = account_session()
        if not acc:
            return redirect(url_for("share_root"))
        user = acc["username"].lower()
        links = []
        for l in sharing_store.list_links():
            if l.get("access") != "users" or not sharing_store.link_is_live(l):
                continue
            allowed = l.get("users") or []
            if allowed and not any(u.lower() == user for u in allowed):
                continue
            try:
                sharing_store.validate_root(l["root"], l.get("share", ""), l.get("file", ""), load_shares(), share_roots())
            except sharing_store.ShareError:
                continue
            links.append(l)
        links.sort(key=lambda l: l["name"].lower())
        return render("home.html", links=links, display_name=acc.get("display_name") or acc["username"])

    @app.route("/logout")
    def share_home_logout():
        session.pop("su", None)
        session.pop("su_epoch", None)
        return redirect(url_for("share_root"))

    @app.route("/lang/<code>")
    def share_lang(code):
        if code in LANG:
            session["lang"] = code
        return redirect(request.referrer or "/")

    def listing(link, sub):
        root = link["_real_root"]
        cur = safepath.resolve_within(root, sub)
        if not os.path.isdir(cur):
            abort(404)
        office = collabora_opener(link)
        dirs, files = [], []
        with os.scandir(cur) as it:
            for e in it:
                if e.name.startswith(".") or e.name.endswith(".part") or e.is_symlink():
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                rel = os.path.relpath(os.path.join(cur, e.name), root).replace(os.sep, "/")
                item = {"name": e.name, "rel": rel, "mtime": st.st_mtime}
                if e.is_dir(follow_symlinks=False):
                    dirs.append(item)
                else:
                    item["size"] = st.st_size
                    mime, _ = mimetypes.guess_type(e.name)
                    item["inline"] = mime in INLINE_MIME
                    item["office"] = office(e.name)
                    files.append(item)
        dirs.sort(key=lambda x: x["name"].lower())
        files.sort(key=lambda x: x["name"].lower())
        rel_cur = os.path.relpath(cur, root).replace(os.sep, "/")
        crumbs = []
        if rel_cur != ".":
            parts = rel_cur.split("/")
            for i, p in enumerate(parts):
                crumbs.append({"name": p, "rel": "/".join(parts[:i + 1])})
        parent = "/".join(rel_cur.split("/")[:-1]) if rel_cur != "." else None
        return dirs, files, crumbs, parent

    @app.route("/s/<token>/")
    def share_landing(token):
        link = g.link
        accesslog.log("view", ip=g.ip, link_id=link["id"], link_name=link["name"],
                      user=session.get("su"), ua=request.headers.get("User-Agent"))
        if not authed(link):
            locked = (LIMITER.locked(f"authfail:link:{link['id']}", *AUTHFAIL_LINK)
                      or LIMITER.locked(f"authfail:ip:{g.ip}", *AUTHFAIL_IP))
            return render("unlock.html", link=link, token=token, locked=locked, error=None)
        if link["mode"] == "upload":
            return render("landing.html", link=link, token=token, upload_only=True, can_upload=True,
                          max_mb=upload_limit_mb(link), dirs=[], files=[], crumbs=[], parent=None, single=None)
        if link.get("file"):
            fp = os.path.join(link["_real_root"], link["file"])
            try:
                size = os.path.getsize(fp)
            except OSError:
                size = None
            single = {"name": link["file"], "size": size, "rel": link["file"],
                      "office": collabora_opener(link)(link["file"])}
            return render("landing.html", link=link, token=token, single=single,
                          dirs=[], files=[], crumbs=[], parent=None, upload_only=False)
        dirs, files, crumbs, parent = listing(link, "")
        return render("landing.html", link=link, token=token, single=None, upload_only=False,
                      can_upload=link["mode"] == "both", max_mb=upload_limit_mb(link),
                      dirs=dirs, files=files, crumbs=crumbs, parent=parent)

    def upload_limit_mb(link):
        site = int(opt("share_max_upload_mb", 1024) or 1024)
        per = int(link.get("max_file_mb") or 0)
        return min(per, site) if per else site

    def upload_folder(link, sub):
        """Destination folder for an upload, created if needed."""
        root = link["_real_root"]
        if not link.get("allow_subdirs", True):
            sub = ""
        base = safepath.resolve_within(root, sub)
        mode = sharing_store.upload_subdir_of(link)
        if mode == "by-date":
            base = os.path.join(base, datetime.date.today().isoformat())
        elif mode == "by-user" and session.get("su"):
            base = os.path.join(base, sharing_store.safe_upload_name(session["su"]))
        os.makedirs(base, exist_ok=True)
        return safepath.resolve_within(root, os.path.relpath(base, root))

    def store_upload(link, folder, name, stream, declared_len):
        """Stream to a .part file in the target folder, then rename into place.
        Returns (final_path, size) or raises ShareError with an L-key."""
        cap = upload_limit_mb(link) * 1024 * 1024
        quota = int(link.get("upload_quota_mb") or 0) * 1024 * 1024
        used = int(link.get("uploaded_bytes") or 0)
        if declared_len > cap:
            raise sharing_store.ShareError("err_too_big")
        if quota and used + declared_len > quota:
            raise sharing_store.ShareError("err_quota")
        fd, tmp = tempfile.mkstemp(prefix=".upload-", suffix=".part", dir=folder)
        written = 0
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > cap or (quota and used + written > quota):
                        raise sharing_store.ShareError("err_too_big" if written > cap else "err_quota")
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if SCAN_HOOK is not None:
                verdict, detail = SCAN_HOOK(tmp)
                if verdict == "infected":
                    accesslog.log("upload_reject", ip=g.ip, link_id=link["id"], link_name=link["name"],
                                  user=session.get("su"), path=name, detail=detail)
                    raise sharing_store.ShareError("err_scan")
                if verdict == "error":
                    accesslog.log("upload_reject", ip=g.ip, link_id=link["id"], link_name=link["name"],
                                  user=session.get("su"), path=name, detail=f"scan error: {detail}")
                    raise sharing_store.ShareError("err_scan_unavailable")
            ffd, final = sharing_store.reserve_free_name(folder, name)
            os.close(ffd)
            os.replace(tmp, final)
            tmp = None
            try:                                   # let Samba users manage the file
                st = os.stat(folder)
                os.chown(final, st.st_uid, st.st_gid)
                os.chmod(final, 0o664)
            except (OSError, AttributeError):
                pass
            return final, written
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def upload_common(link, name_raw, sub, stream, declared_len):
        ok, _ = LIMITER.hit(f"upload:ip:{g.ip}", *UPLOAD_PER_IP)
        if not ok:
            accesslog.log("rate_limited", ip=g.ip, link_id=link["id"], detail="upload")
            raise sharing_store.ShareError("err_rate")
        name = sharing_store.safe_upload_name(name_raw)
        if not sharing_store.extension_allowed(name, opt("share_upload_blocked_ext", None),
                                               opt("share_upload_allowed_ext", None)):
            accesslog.log("upload_reject", ip=g.ip, link_id=link["id"], link_name=link["name"],
                          user=session.get("su"), path=name, detail="extension")
            raise sharing_store.ShareError("err_ext")
        try:
            folder = upload_folder(link, sub)
        except safepath.PathError:
            abort(404)
        final, size = store_upload(link, folder, name, stream, declared_len)
        sharing_store.record_upload(link["id"], size, g.ip)
        rel = os.path.relpath(final, link["_real_root"]).replace(os.sep, "/")
        accesslog.log("upload", ip=g.ip, link_id=link["id"], link_name=link["name"],
                      user=session.get("su"), path=rel, bytes=size)
        return rel, size

    def upload_allowed(link):
        if link["mode"] not in ("upload", "both") or link.get("file"):
            abort(403)

    @app.route("/s/<token>/upload", methods=["POST"])
    def share_upload(token):
        """Raw body upload: fetch(url, {method:'POST', body: file}). One temp
        copy instead of the two a multipart parser would make."""
        link = g.link
        upload_allowed(link)
        if not csrf_ok():
            return jsonify({"ok": False, "error": "wrong"}), 403
        length = request.headers.get("Content-Length")
        if not length or not length.isdigit():
            return jsonify({"ok": False, "error": "err_length"}), 411
        try:
            rel, size = upload_common(link, request.args.get("name", ""), request.args.get("dir", ""),
                                      request.stream, int(length))
        except sharing_store.ShareError as e:
            code = {"err_too_big": 413, "err_quota": 413, "err_ext": 415, "err_rate": 429,
                    "err_scan": 422, "err_scan_unavailable": 503}.get(str(e), 400)
            return jsonify({"ok": False, "error": str(e)}), code
        return jsonify({"ok": True, "name": os.path.basename(rel), "path": rel, "size": size})

    @app.route("/s/<token>/upload-form", methods=["POST"])
    def share_upload_form(token):
        """No-JavaScript fallback: multipart form."""
        link = g.link
        upload_allowed(link)
        if not csrf_ok():
            abort(403)
        f = request.files.get("file")
        if not f or not f.filename:
            return redirect(url_for("share_landing", token=token))
        f.stream.seek(0, os.SEEK_END)
        size = f.stream.tell()
        f.stream.seek(0)
        try:
            upload_common(link, f.filename, request.form.get("dir", ""), f.stream, size)
        except sharing_store.ShareError as e:
            return render("error.html", 400, msg_key=str(e))
        return redirect(url_for("share_landing", token=token))

    @app.route("/s/<token>/delete", methods=["POST"])
    def share_delete(token):
        """Delete one file or folder below the link root. The confirmation
        happens in the browser; here it is CSRF, the link's allow_delete, the
        path confinement and 'never the root itself'."""
        link = g.link
        if not link.get("allow_delete") or link.get("file") or link["mode"] == "upload":
            abort(403)
        if not csrf_ok():
            abort(403)
        rel = (request.form.get("path") or "").replace("\\", "/").strip("/")
        parent = "/".join(rel.split("/")[:-1])
        back = url_for("share_browse", token=token, sub=parent) if parent else url_for("share_landing", token=token)
        if not rel or (not link.get("allow_subdirs", True) and "/" in rel):
            abort(404)
        ok, _ = LIMITER.hit(f"delete:ip:{g.ip}", *DELETE_PER_IP)
        if not ok:
            accesslog.log("rate_limited", ip=g.ip, link_id=link["id"], detail="delete")
            return render("error.html", 429, msg_key="too_many")
        root = link["_real_root"]
        lexical = os.path.join(root, *rel.split("/"))
        name = os.path.basename(lexical)
        # A symlink would resolve to its target; listings never show them, so
        # there is nothing legitimate to delete through one.
        if name.startswith(".") or name.endswith(".part") or os.path.islink(lexical):
            abort(404)
        try:
            target = safepath.resolve_within(root, rel)
        except safepath.PathError:
            abort(404)
        if target == safepath.real(root) or not os.path.lexists(target):
            abort(404)
        is_dir = os.path.isdir(target)
        held = locks.paths()
        if target in held or (is_dir and any(safepath.is_within(target, p) for p in held)):
            return render("error.html", 409, msg_key="err_in_use")
        try:
            if is_dir:
                shutil.rmtree(target)
            else:
                os.unlink(target)
        except OSError as e:
            print(f"[SHARE] Loeschen fehlgeschlagen ({link['id']}): {type(e).__name__}: {e}", flush=True)
            return render("error.html", 500, msg_key="err_delete")
        accesslog.log("delete", ip=g.ip, link_id=link["id"], link_name=link["name"], user=session.get("su"),
                      path=rel, detail="folder" if is_dir else "file")
        return redirect(back)

    @app.route("/s/<token>/auth", methods=["POST"])
    def share_auth(token):
        link = g.link
        if not csrf_ok():
            return render("unlock.html", link=link, token=token, locked=False, error="wrong")
        ip_key, link_key = f"authfail:ip:{g.ip}", f"authfail:link:{link['id']}"
        if LIMITER.locked(ip_key, *AUTHFAIL_IP) or LIMITER.locked(link_key, *AUTHFAIL_LINK):
            accesslog.log("rate_limited", ip=g.ip, link_id=link["id"], detail="auth locked")
            return render("unlock.html", link=link, token=token, locked=True, error="locked")

        ok = False
        user = None
        if link["access"] == "password":
            pw = request.form.get("password", "")
            ok = bool(link.get("password_hash")) and check_password_hash(link["password_hash"], pw)
        elif link["access"] == "users":
            user = request.form.get("username", "").strip()
            user_key = f"authfail:user:{user.lower()}"
            if user and LIMITER.locked(user_key, *AUTHFAIL_USER):
                accesslog.log("rate_limited", ip=g.ip, link_id=link["id"], user=user, detail="user locked")
                return render("unlock.html", link=link, token=token, locked=True, error="locked")
            acc = sharing_store.check_account_password(user, request.form.get("password", ""))
            allowed = link.get("users") or []
            ok = bool(acc) and ((not allowed) or any(u.lower() == user.lower() for u in allowed))
            if not ok and user:
                fail(user_key, AUTHFAIL_USER)
        else:
            ok = True

        if not ok:
            time.sleep(random.uniform(0.15, 0.35))
            fail(ip_key, AUTHFAIL_IP)
            fail(link_key, AUTHFAIL_LINK)
            accesslog.log("auth_fail", ip=g.ip, link_id=link["id"], link_name=link["name"], user=user,
                          ua=request.headers.get("User-Agent"))
            return render("unlock.html", link=link, token=token, locked=False, error="wrong")

        session.permanent = True
        if link["access"] == "password":
            session[f"lnk:{link['id']}"] = link.get("auth_epoch", 1)
        elif link["access"] == "users":
            acc = sharing_store.get_account(user)
            session["su"] = acc["username"]
            session["su_epoch"] = acc.get("auth_epoch", 1)
            sharing_store.touch_login(acc["username"])
        accesslog.log("auth_ok", ip=g.ip, link_id=link["id"], link_name=link["name"], user=user)
        return redirect(url_for("share_landing", token=token))

    @app.route("/s/<token>/logout")
    def share_logout(token):
        link = g.link
        session.pop(f"lnk:{link['id']}", None)
        session.pop("su", None)
        session.pop("su_epoch", None)
        return redirect(url_for("share_landing", token=token))

    @app.route("/s/<token>/b/<path:sub>")
    def share_browse(token, sub):
        link = g.link
        if link.get("file") or not link.get("allow_subdirs", True):
            abort(404)
        try:
            dirs, files, crumbs, parent = listing(link, sub)
        except safepath.PathError:
            abort(404)
        return render("landing.html", link=link, token=token, single=None, upload_only=False,
                      can_upload=link["mode"] == "both", max_mb=upload_limit_mb(link),
                      dirs=dirs, files=files, crumbs=crumbs, parent=parent)

    def _file_for(link, sub):
        if link.get("file"):
            if sub != link["file"]:
                abort(404)
        elif not link.get("allow_subdirs", True) and "/" in sub.strip("/"):
            abort(404)
        try:
            fp = safepath.resolve_within(link["_real_root"], sub)
        except safepath.PathError:
            abort(404)
        base = os.path.basename(fp)
        if not os.path.isfile(fp) or base.startswith(".") or base.endswith(".part"):
            abort(404)
        return fp

    @app.route("/s/<token>/d/<path:sub>")
    def share_download(token, sub):
        link = g.link
        fp = _file_for(link, sub)
        rng = request.headers.get("Range", "")
        if not rng or rng.startswith("bytes=0-"):
            sharing_store.record_download(link["id"], g.ip)
            accesslog.log("download", ip=g.ip, link_id=link["id"], link_name=link["name"],
                          user=session.get("su"), path=sub, bytes=os.path.getsize(fp))
        resp = send_file(fp, as_attachment=True, conditional=True, download_name=os.path.basename(fp))
        resp.headers["Content-Disposition"] = content_disposition(os.path.basename(fp))
        resp.headers["X-Accel-Buffering"] = "no"
        return resp

    @app.route("/s/<token>/zip", defaults={"sub": ""})
    @app.route("/s/<token>/zip/<path:sub>")
    def share_zip(token, sub):
        link = g.link
        if link.get("file") or not link.get("allow_zip", True):
            abort(404)
        if sub and not link.get("allow_subdirs", True):
            abort(404)
        try:
            folder = safepath.resolve_within(link["_real_root"], sub)
        except safepath.PathError:
            abort(404)
        if not os.path.isdir(folder):
            abort(404)
        limit_bytes = int(opt("share_zip_max_gb", 5) or 5) * 1024 ** 3
        try:
            total, count = zipstream.preflight(folder, limit_bytes, int(opt("share_zip_max_files", 10000) or 10000))
        except zipstream.TooBig:
            return render("error.html", 413, msg_key="zip_too_big")
        name = (os.path.basename(folder.rstrip(os.sep)) or link["name"]) + ".zip"
        sharing_store.record_download(link["id"], g.ip)
        accesslog.log("zip", ip=g.ip, link_id=link["id"], link_name=link["name"], user=session.get("su"),
                      path=sub or ".", bytes=total, detail=f"{count} files")
        resp = app.response_class(zipstream.stream(folder, total, compress=bool(opt("share_zip_compress", False))),
                                  mimetype="application/zip")
        resp.headers["Content-Disposition"] = content_disposition(name)
        resp.headers["X-Accel-Buffering"] = "no"
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    @app.route("/s/<token>/v/<path:sub>")
    def share_view(token, sub):
        link = g.link
        fp = _file_for(link, sub)
        mime, _ = mimetypes.guess_type(fp)
        inline = mime in INLINE_MIME
        resp = send_file(fp, mimetype=mime if inline else "application/octet-stream",
                         as_attachment=not inline, conditional=True, download_name=os.path.basename(fp))
        resp.headers["Content-Disposition"] = content_disposition(os.path.basename(fp), inline=inline)
        resp.headers["X-Accel-Buffering"] = "no"
        return resp

    # ── Collabora Online (WOPI) ─────────────────────────────────────────────
    # The browser gets an editor page that frames Collabora; Collabora then
    # reads and writes the file through /wopi/files/<id>, authorised by a
    # short-lived access token minted here and bound to one link and one file.
    locks = wopi.LockTable(data_dir)

    def collabora_on():
        return bool(opt("collabora_enabled", False)) and bool(str(opt("collabora_url", "") or "").strip())

    def discovery():
        if not collabora_on():
            return None
        return DISCOVERY.get(opt("collabora_url", ""), opt("collabora_internal_url", ""),
                             bool(opt("collabora_verify_tls", True)))

    def link_can_edit(link):
        return bool(link.get("allow_edit")) and link.get("mode") != "upload"

    def collabora_opener(link):
        """name -> 'edit' / 'view' / '' for the buttons in a listing. One
        discovery lookup per listing, not per file."""
        d = discovery() if link.get("mode") != "upload" else None
        if not d:
            return lambda name: ""
        write = link_can_edit(link)

        def office(name):
            action, _ = wopi.pick_action(d, name, write)
            if not action:
                return ""
            return "edit" if write and action == "edit" else "view"
        return office

    def wopi_base():
        base = str(opt("collabora_wopi_url", "") or opt("share_public_url", "") or "").strip().rstrip("/")
        return base or f"{ext_scheme()}://{request.host}"

    @app.route("/s/<token>/e/<path:sub>")
    def share_edit(token, sub):
        link = g.link
        if not collabora_on():
            abort(404)
        fp = _file_for(link, sub)
        rel = os.path.relpath(fp, link["_real_root"]).replace(os.sep, "/")
        want_write = link_can_edit(link)
        action, urlsrc = wopi.pick_action(discovery(), os.path.basename(fp), want_write)
        if not urlsrc:
            if DISCOVERY.error:
                print(f"[SHARE] Collabora-Discovery fehlgeschlagen: {DISCOVERY.error}", flush=True)
            return render("error.html", 503, msg_key="edit_unavailable")
        user = session.get("su", "") if link["access"] == "users" else ""
        acc = sharing_store.get_account(user) if user else None
        hours = int(opt("share_session_hours", 8) or 8)
        can_write = want_write and action == "edit"
        page_origin = wopi.origin_of(f"{ext_scheme()}://{request.host}")
        access_token, exp = wopi.make_token(app.secret_key, link["id"], rel, user, can_write,
                                            link.get("auth_epoch", 1), acc.get("auth_epoch", 1) if acc else 0,
                                            hours * 3600, origin=page_origin)
        src = wopi.editor_url(urlsrc, f"{wopi_base()}/wopi/files/{wopi.file_id(link['id'], rel)}", pick_lang())
        sharing_store.record_download(link["id"], g.ip)
        accesslog.log("edit_open", ip=g.ip, link_id=link["id"], link_name=link["name"], user=session.get("su"),
                      path=rel, detail="edit" if can_write else "view", ua=request.headers.get("User-Agent"))
        parent = "/".join(rel.split("/")[:-1])
        if link.get("file") or not parent:
            back = url_for("share_landing", token=token)
        else:
            back = url_for("share_browse", token=token, sub=parent)
        g.frame_origin = wopi.origin_of(opt("collabora_url", ""))
        return render("editor.html", link=link, name=os.path.basename(fp), action_url=src,
                      access_token=access_token, ttl_ms=exp * 1000, back=back, collabora_origin=g.frame_origin)

    def wopi_fail(status, why, claims=None, **extra_headers):
        if why:
            accesslog.log("wopi_denied", ip=g.ip, link_id=(claims or {}).get("l"), user=(claims or {}).get("u"),
                          detail=why)
        resp = make_response("", status)
        for k, v in extra_headers.items():
            resp.headers[k.replace("_", "-")] = v
        return resp

    def proof_ok(access_token):
        if not bool(opt("collabora_verify_proof", True)):
            return True
        d = discovery()
        if not d:
            return False                   # fail closed: cannot tell Collabora from anyone else
        if not d.get("proof"):
            return True                    # this Collabora publishes no proof key
        qs = request.query_string.decode("latin-1")
        tail = request.path + ("?" + qs if qs else "")
        urls = [wopi_base() + tail, f"{ext_scheme()}://{request.host}{tail}", request.url_root.rstrip("/") + tail]
        return wopi.verify_proof(d["proof"], access_token, urls, request.headers.get("X-WOPI-TimeStamp", ""),
                                 request.headers.get("X-WOPI-Proof", ""), request.headers.get("X-WOPI-ProofOld", ""))

    def wopi_context(fid):
        """(ctx, None) or (None, error response). Re-checks everything the
        editor page checked, because the token outlives the page: a disabled
        link, a rotated token, a changed password or a disabled account all
        end a running editor session at its next call."""
        if not collabora_on():
            return None, make_response("", 404)
        access_token = request.args.get("access_token", "")
        claims = wopi.read_token(app.secret_key, access_token)
        if not claims or not hmac.compare_digest(wopi.file_id(claims["l"], claims["p"]), fid):
            return None, wopi_fail(401, "token")
        if not proof_ok(access_token):
            return None, wopi_fail(401, "proof", claims)
        link = sharing_store.get_link(claims["l"])
        # Deliberately not link_is_live(): opening the editor counted as a
        # download, and a one-download link must still save what it opened.
        now = time.time()
        if (not link or not link.get("enabled") or (link.get("expires") and now > link["expires"])
                or link.get("mode") == "upload" or int(link.get("auth_epoch", 1)) != claims["le"]):
            return None, wopi_fail(401, "link", claims)
        try:
            root = sharing_store.validate_root(link["root"], link.get("share", ""), link.get("file", ""),
                                               load_shares(), share_roots())
        except sharing_store.ShareError:
            return None, wopi_fail(404, "root", claims)
        user = claims.get("u") or ""
        display = ""
        if user:
            acc = sharing_store.get_account(user)
            allowed = link.get("users") or []
            if (link.get("access") != "users" or not acc or not acc.get("enabled", True)
                    or int(acc.get("auth_epoch", 1)) != claims["ue"]
                    or (allowed and not any(u.lower() == user.lower() for u in allowed))):
                return None, wopi_fail(401, "account", claims)
            display = acc.get("display_name") or acc["username"]
        elif link.get("access") == "users":
            return None, wopi_fail(401, "account", claims)
        if link.get("file") and claims["p"] != link["file"]:
            return None, wopi_fail(404, "path", claims)
        try:
            path = safepath.resolve_within(root, claims["p"])
        except safepath.PathError:
            return None, wopi_fail(404, "path", claims)
        base = os.path.basename(path)
        if not os.path.isfile(path) or base.startswith(".") or base.endswith(".part"):
            return None, wopi_fail(404, "")
        return {"link": link, "path": path, "rel": claims["p"], "user": user, "claims": claims,
                "display": display or LANG[pick_lang()]["guest"],
                "can_write": bool(claims.get("w")) and link_can_edit(link)}, None

    def check_file_info(ctx):
        st = os.stat(ctx["path"])
        stamp = wopi.iso_mtime(st.st_mtime)
        link = ctx["link"]
        info = {
            "BaseFileName": os.path.basename(ctx["path"]),
            "Size": st.st_size,
            "Version": stamp,
            "LastModifiedTime": stamp,
            "OwnerId": "simplenas",
            "UserId": ctx["user"] or f"guest-{link['id']}",
            "UserFriendlyName": ctx["display"],
            "IsAnonymousUser": not ctx["user"],
            "UserCanWrite": ctx["can_write"],
            "ReadOnly": not ctx["can_write"],
            "SupportsLocks": True,
            "SupportsGetLock": True,
            "SupportsUpdate": True,
            "SupportsRename": False,
            "UserCanRename": False,
            "UserCanNotWriteRelative": True,       # no "save as" into the folder
            "EnableInsertRemoteImage": False,
            "EnableShare": False,
            "HideUserList": "false",
        }
        origin = ctx["claims"].get("o") or ""
        if origin:
            info["PostMessageOrigin"] = origin
        return jsonify(info)

    def lock_response(ok, current, status_ok=200):
        resp = make_response("", status_ok if ok else 409)
        resp.headers["X-WOPI-Lock"] = current or ""
        if not ok:
            resp.headers["X-WOPI-LockFailureReason"] = "locked by another session" if current else "not locked"
        return resp

    @app.route("/wopi/files/<fid>", methods=["GET", "POST"])
    def wopi_file(fid):
        ctx, err = wopi_context(fid)
        if err is not None:
            return err
        if request.method == "GET":
            return check_file_info(ctx)
        override = request.headers.get("X-WOPI-Override", "").upper()
        lock_id = request.headers.get("X-WOPI-Lock", "")
        path = ctx["path"]
        if override == "GET_LOCK":
            return lock_response(True, locks.get(path))
        if override in ("LOCK", "REFRESH_LOCK", "UNLOCK"):
            if not ctx["can_write"]:
                return wopi_fail(401, "readonly", ctx["claims"])
            if override == "LOCK":
                old = request.headers.get("X-WOPI-OldLock")
                return lock_response(*locks.lock(path, lock_id, old_lock=old if old else None))
            if override == "REFRESH_LOCK":
                return lock_response(*locks.refresh(path, lock_id))
            return lock_response(*locks.unlock(path, lock_id))
        return make_response("", 501)          # PUT_RELATIVE, RENAME_FILE, ... are not offered

    @app.route("/wopi/files/<fid>/contents", methods=["GET", "POST"])
    def wopi_contents(fid):
        ctx, err = wopi_context(fid)
        if err is not None:
            return err
        path, link = ctx["path"], ctx["link"]
        if request.method == "GET":
            resp = send_file(path, mimetype="application/octet-stream", as_attachment=False, conditional=False)
            resp.headers["X-WOPI-ItemVersion"] = wopi.iso_mtime(os.stat(path).st_mtime)
            resp.headers["Cache-Control"] = "private, no-store"
            return resp

        if not ctx["can_write"]:
            return wopi_fail(401, "readonly", ctx["claims"])
        lock_id = request.headers.get("X-WOPI-Lock", "")
        current = locks.get(path)
        if current and current != lock_id:
            return lock_response(False, current)
        if not current and lock_id:
            # The lock lapsed (e.g. the host was offline past its expiry) but
            # this very session holds a write token: take it again rather
            # than throw away the user's edits.
            ok, current = locks.lock(path, lock_id)
            if not ok:
                return lock_response(False, current)
        st = os.stat(path)
        stamp = wopi.iso_mtime(st.st_mtime)
        sent = request.headers.get("X-COOL-WOPI-Timestamp") or request.headers.get("X-LOOL-WOPI-Timestamp")
        if sent and sent != stamp:
            # Changed behind Collabora's back (Samba, another link): let the
            # user choose instead of silently overwriting.
            resp = jsonify({"COOLStatusCode": 1010, "LOOLStatusCode": 1010})
            resp.status_code = 409
            return resp

        cap = int(opt("share_max_upload_mb", 1024) or 1024) * 1024 * 1024
        length = request.headers.get("Content-Length", "")
        if length.isdigit() and int(length) > cap:
            return make_response("", 413)
        folder = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(prefix=".wopi-", suffix=".part", dir=folder)
        written = 0
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = request.stream.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > cap:
                        return make_response("", 413)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if SCAN_HOOK is not None:
                verdict, detail = SCAN_HOOK(tmp)
                if verdict in ("infected", "error"):
                    accesslog.log("upload_reject", ip=g.ip, link_id=link["id"], link_name=link["name"],
                                  user=ctx["user"] or None, path=ctx["rel"], detail=f"wopi {verdict}: {detail}")
                    return make_response("", 500)
            try:                               # keep the Samba owner and mode of the original
                os.chown(tmp, st.st_uid, st.st_gid)
                os.chmod(tmp, st.st_mode & 0o7777)
            except (OSError, AttributeError):
                pass
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        new_stamp = wopi.iso_mtime(os.stat(path).st_mtime)
        accesslog.log("edit_save", ip=g.ip, link_id=link["id"], link_name=link["name"], user=ctx["user"] or None,
                      path=ctx["rel"], bytes=written,
                      detail="autosave" if request.headers.get("X-COOL-WOPI-IsAutosave") == "true" else "")
        resp = jsonify({"LastModifiedTime": new_stamp})
        resp.headers["X-WOPI-ItemVersion"] = new_stamp
        return resp

    return app


def assert_public_surface(app):
    """Refuse to start if the public app exposes anything unexpected."""
    got = {r.endpoint for r in app.url_map.iter_rules()} - {"static"}
    extra = got - PUBLIC_ENDPOINTS
    if extra:
        raise SystemExit(f"FATAL: oeffentliche Freigabe-App zeigt unerwartete Endpunkte: {sorted(extra)}")
    return True
