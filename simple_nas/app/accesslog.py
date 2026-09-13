#!/usr/bin/env python3
"""Access log of the public share site: one JSON object per line.

Bounded by RotatingFileHandler (stdlib, thread-safe). Never the token, never
an absolute filesystem path - only the link id and the path relative to the
link root.
"""

import json
import logging
import logging.handlers
import os
import threading
import time

_logger = None
_path = None
_handlers = {}          # slot -> (path, handler); "main" is the configured one,
                        # "crowdsec" the copy the CrowdSec add-on can read
_lock = threading.Lock()

EVENTS = ("view", "auth_ok", "auth_fail", "download", "zip", "upload",
          "upload_reject", "rate_limited", "link_404")


def init(path, max_mb=5, export_path=None):
    """export_path: an optional copy somewhere another add-on can read.
    Failure to open it must never break the site."""
    global _logger, _path
    _path = path
    lg = logging.getLogger("nas.share.access")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()
    _handlers.clear()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    h = logging.handlers.RotatingFileHandler(
        path, maxBytes=int(max_mb) * 1024 * 1024, backupCount=1, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(message)s"))
    lg.addHandler(h)
    _logger = lg
    if export_path:
        set_export(export_path, max_mb)


def set_export(export_path, max_mb=5, slot="main"):
    """Point one export slot at a file, at runtime. Empty path closes the slot.

    Slots exist because the configured path and the path CrowdSec can read are
    not always the same file. Returns True when the slot is served - including
    the case where another slot already writes that exact file: two rotating
    handlers on one path would interleave and shred each other's rotation.
    """
    global _logger
    lg = _logger or logging.getLogger("nas.share.access")
    with _lock:
        current = _handlers.get(slot)
        if current and current[0] == export_path:
            return True
        if current:
            lg.removeHandler(current[1])
            current[1].close()
            _handlers.pop(slot, None)
        if not export_path:
            return True
        if any(p == export_path for p, _h in _handlers.values()):
            return True
        try:
            os.makedirs(os.path.dirname(export_path), exist_ok=True)
            eh = logging.handlers.RotatingFileHandler(
                export_path, maxBytes=int(max_mb) * 1024 * 1024, backupCount=1, encoding="utf-8")
            eh.setFormatter(logging.Formatter("%(message)s"))
            lg.addHandler(eh)
            _handlers[slot] = (export_path, eh)
        except OSError as e:
            print(f"[SHARE] Protokoll-Export nach {export_path} nicht moeglich: {e}", flush=True)
            return False
    if _logger is None:
        lg.setLevel(logging.INFO)
        lg.propagate = False
        _logger = lg
    return True


def export_path(slot="main"):
    """The file this slot writes, or the file another slot writes for it."""
    entry = _handlers.get(slot)
    return entry[0] if entry else None


def writes_to(path):
    """Is this file being written by any slot?"""
    return bool(path) and any(p == path for p, _h in _handlers.values())


def log(event, **fields):
    if _logger is None:
        return
    now = time.time()
    rec = {"ts": round(now, 3),
           "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),   # RFC3339 for CrowdSec
           "event": event}
    for k, v in fields.items():
        if v is None or v == "":
            continue
        if k == "ua":
            v = str(v)[:120]
        rec[k] = v
    try:
        _logger.info(json.dumps(rec, ensure_ascii=False))
    except Exception:
        pass
    if event in ("auth_fail", "upload_reject", "rate_limited", "upload", "download", "zip"):
        print(f"[SHARE] {event} " + " ".join(f"{k}={v}" for k, v in rec.items()
                                            if k not in ("ts", "event", "ua")), flush=True)


def tail(limit=200, event=None, link_id=None, max_bytes=256 * 1024):
    """Last entries, newest first. Reads only the end of the file."""
    if not _path or not os.path.exists(_path):
        return []
    out = []
    for p in (_path, _path + ".1"):
        if not os.path.exists(p):
            continue
        try:
            with open(p, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
                chunk = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        lines = chunk.split("\n")
        if size > max_bytes:
            lines = lines[1:]          # first line is probably cut
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            if event and rec.get("event") != event:
                continue
            if link_id and rec.get("link_id") != link_id:
                continue
            out.append(rec)
    out.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return out[:limit]


def clear():
    with _lock:
        for p in (_path, (_path or "") + ".1"):
            if p and os.path.exists(p):
                try:
                    open(p, "w").close()
                except OSError:
                    pass
