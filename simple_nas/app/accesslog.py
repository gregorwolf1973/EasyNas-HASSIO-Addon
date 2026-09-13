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
_lock = threading.Lock()

EVENTS = ("view", "auth_ok", "auth_fail", "download", "zip", "upload",
          "upload_reject", "rate_limited", "link_404")


def init(path, max_mb=5):
    global _logger, _path
    _path = path
    lg = logging.getLogger("nas.share.access")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    for h in list(lg.handlers):
        lg.removeHandler(h)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    h = logging.handlers.RotatingFileHandler(
        path, maxBytes=int(max_mb) * 1024 * 1024, backupCount=1, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(message)s"))
    lg.addHandler(h)
    _logger = lg


def log(event, **fields):
    if _logger is None:
        return
    rec = {"ts": round(time.time(), 3), "event": event}
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
