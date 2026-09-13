#!/usr/bin/env python3
"""The public share site - a separate Flask app on its own port.

Never imports app.py. Serves only /healthz and /s/<token>/... - nothing
else exists on this port, and assert_public_surface() refuses to start if
anything else ever gets registered here.
"""

import hmac
import ipaddress
import mimetypes
import os
import random
import secrets
import time
from datetime import timedelta
from urllib.parse import quote

from flask import (Flask, abort, g, jsonify, make_response, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.security import check_password_hash

import accesslog
import safepath
import sharing_store
import zipstream
from ratelimit import (AUTHFAIL_IP, AUTHFAIL_LINK, AUTHFAIL_USER, LIMITER, REQ_PER_IP)

RUNNING = False

# Every endpoint that may exist on the public app. Anything else = refuse to start.
PUBLIC_ENDPOINTS = frozenset({
    "healthz", "share_root", "share_landing", "share_auth", "share_logout",
    "share_browse", "share_download", "share_view", "share_lang", "share_zip",
    "share_root_login", "share_home", "share_home_logout",
})

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
        MAX_CONTENT_LENGTH=64 * 1024,      # no uploads in this release
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
        ok, retry = LIMITER.hit(f"ip:{g.ip}", *REQ_PER_IP)
        if not ok:
            accesslog.log("rate_limited", ip=g.ip, path=request.path[:80])
            resp = render("error.html", 429, msg_key="too_many")
            resp.headers["Retry-After"] = str(retry)
            return resp
        if request.endpoint in ("healthz", "share_root", "share_lang", "share_root_login",
                                "share_home", "share_home_logout", None):
            return
        token = (request.view_args or {}).get("token", "")
        if not sharing_store.token_shape_ok(token):
            return not_found()
        link = resolve_link(token)
        if link is None:
            accesslog.log("link_404", ip=g.ip, ua=request.headers.get("User-Agent"))
            return not_found()
        g.link = link
        if request.endpoint in ("share_landing", "share_auth", "share_logout"):
            return
        if not authed(link):
            return redirect(url_for("share_landing", token=token))
        if link["mode"] == "upload" and request.endpoint in ("share_browse", "share_download", "share_view", "share_zip"):
            abort(403)

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; media-src 'self'; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
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
            LIMITER.record(f"authfail:ip:{g.ip}")
            if user:
                LIMITER.record(user_key)
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
            return render("landing.html", link=link, token=token, upload_only=True,
                          dirs=[], files=[], crumbs=[], parent=None, single=None)
        if link.get("file"):
            fp = os.path.join(link["_real_root"], link["file"])
            try:
                size = os.path.getsize(fp)
            except OSError:
                size = None
            single = {"name": link["file"], "size": size, "rel": link["file"]}
            return render("landing.html", link=link, token=token, single=single,
                          dirs=[], files=[], crumbs=[], parent=None, upload_only=False)
        dirs, files, crumbs, parent = listing(link, "")
        return render("landing.html", link=link, token=token, single=None, upload_only=False,
                      dirs=dirs, files=files, crumbs=crumbs, parent=parent)

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
                LIMITER.record(user_key)
        else:
            ok = True

        if not ok:
            time.sleep(random.uniform(0.15, 0.35))
            LIMITER.record(ip_key)
            LIMITER.record(link_key)
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

    return app


def assert_public_surface(app):
    """Refuse to start if the public app exposes anything unexpected."""
    got = {r.endpoint for r in app.url_map.iter_rules()} - {"static"}
    extra = got - PUBLIC_ENDPOINTS
    if extra:
        raise SystemExit(f"FATAL: oeffentliche Freigabe-App zeigt unerwartete Endpunkte: {sorted(extra)}")
    return True
