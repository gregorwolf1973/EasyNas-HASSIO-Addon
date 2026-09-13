#!/usr/bin/env python3
"""The public share site as its own process.

Launched by app.py through share_jail.sh, which drops this to `nobody`, strips
the capabilities down to what uploads need, and hides everything outside the
shared folders and a short list of /data files. It then serves exactly the same
WSGI app the in-process fallback serves - the confinement is the only
difference.

Run standalone it simply serves without the sandbox (that is what the
in-process fallback does by importing serve_share).
"""
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import accesslog
import ratelimit
import sharesandbox
import sharing_store
from ratelimit import LIMITER


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(path, data, mirror=True):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _opt_reader(opts):
    def _opt(key, default=None):
        v = opts.get(key, default)
        return default if v is None else v
    return _opt


def _snapshot_loop(data_dir, opt, stop):
    """Publish the limiter state and apply queued unlocks, ~every 2 s."""
    link_ids = []
    last_scan = 0.0
    while not stop.is_set():
        try:
            for key in sharesandbox.drain_unlocks(data_dir):
                LIMITER.clear(key=key)
                print(f"[SHARE] Sperre aufgehoben: {key}", flush=True)
            now = time.time()
            if now - last_scan > 5:          # link ids change rarely; rescan cheaply
                link_ids = [l.get("id") for l in sharing_store.list_links() if l.get("id")]
                last_scan = now
            snap = LIMITER.snapshot(ratelimit.AUTHFAIL_IP[1])
            snap["link_locked"] = [lid for lid in link_ids
                                   if LIMITER.locked(f"authfail:link:{lid}", *ratelimit.AUTHFAIL_LINK)]
            sharesandbox.write_snapshot(data_dir, snap)
        except Exception as e:            # a transient error must not kill the loop
            print(f"[SHARE] Snapshot-Schleife: {type(e).__name__}: {e}", flush=True)
        stop.wait(2)


def serve_share(opt, load_shares, share_roots, data_dir, blocking=True, sandboxed=False):
    """Build the public app and serve it with waitress. Returns the waitress
    server when blocking is False (the in-process fallback runs it in a thread)."""
    import share_web
    from waitress import create_server

    share_app = share_web.create_share_app(opt, load_shares, share_roots, data_dir)
    share_web.assert_public_surface(share_app)

    import clamav
    share_web.SCAN_HOOK = clamav.make_hook(opt)

    host = str(opt("share_bind", "0.0.0.0") or "0.0.0.0")
    port = int(opt("share_port", 8101) or 8101)
    body_limit = int(opt("share_max_upload_mb", 1024) or 1024) * 1024 * 1024 + 8 * 1024 * 1024
    srv = create_server(share_app, host=host, port=port, threads=8, ident=None,
                        channel_timeout=600, max_request_body_size=body_limit,
                        asyncore_use_poll=True)
    share_web.RUNNING = True
    where = "abgeschottet als nobody" if sandboxed else "im Hauptprozess"
    print(f"[SHARE] oeffentliche Freigabe-Seite auf {host}:{port} ({where})", flush=True)
    if not blocking:
        return srv
    srv.run()
    return srv


def main():
    data_dir = os.environ.get("SHARE_DATA_DIR", "/data")
    opts = sharesandbox.read_options(data_dir)
    opt = _opt_reader(opts)

    sharing_store.init(data_dir, _load_json, _save_json)
    accesslog.init(os.path.join(data_dir, sharesandbox.LOG_FILE), opt("share_log_max_mb", 5))

    def load_shares():
        return _load_json(os.path.join(data_dir, "shares.json"), [])

    def share_roots():
        roots = opt("share_allowed_roots", None)
        if not isinstance(roots, list) or not roots:
            roots = list(sharing_store.DEFAULT_SHARE_ROOTS)
        return [str(r).strip() for r in roots if str(r).strip()]

    stop = threading.Event()
    threading.Thread(target=_snapshot_loop, args=(data_dir, opt, stop),
                     daemon=True, name="share-locks").start()

    uid = os.getuid() if hasattr(os, "getuid") else -1
    print(f"[SHARE] Worker gestartet (uid={uid})", flush=True)
    try:
        serve_share(opt, load_shares, share_roots, data_dir, blocking=True, sandboxed=True)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
