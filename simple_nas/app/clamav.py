#!/usr/bin/env python3
"""Talk to a clamd daemon (the user's ClamAV add-on) over TCP.

No clamav package in this image: that would drag in hundreds of MB of
signatures and a second freshclam. The protocol is small enough to speak
directly.

Verdicts: "clean", "infected", "too_large", "error".
"""

import socket
import struct

CHUNK = 32 * 1024
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def _connect(host, port, timeout):
    s = socket.create_connection((host, port), timeout=timeout)
    s.settimeout(timeout)
    return s


def _recv_all(s):
    out = b""
    while True:
        try:
            part = s.recv(4096)
        except socket.timeout:
            break
        if not part:
            break
        out += part
        if out.endswith(b"\0") or out.endswith(b"\n"):
            break
    return out.decode("utf-8", "replace").strip("\0\n ")


def ping(host, port, timeout=5):
    try:
        with _connect(host, port, timeout) as s:
            s.sendall(b"zPING\0")
            return _recv_all(s) == "PONG"
    except OSError:
        return False


def _parse(reply):
    r = reply.strip()
    if r.endswith("OK"):
        return "clean", ""
    if r.endswith("FOUND"):
        return "infected", r.split(":", 1)[-1].replace("FOUND", "").strip()
    if "size limit exceeded" in r.lower():
        return "too_large", r
    return "error", r or "empty reply"


def scan_stream(fileobj, host, port, timeout=120):
    """INSTREAM: push the file through the socket. Subject to clamd's
    StreamMaxLength (25 MB by default)."""
    try:
        with _connect(host, port, timeout) as s:
            s.sendall(b"zINSTREAM\0")
            while True:
                chunk = fileobj.read(CHUNK)
                if not chunk:
                    break
                s.sendall(struct.pack("!I", len(chunk)) + chunk)
            s.sendall(b"\0\0\0\0")
            return _parse(_recv_all(s))
    except OSError as e:
        return "error", str(e)


def scan_path(path, host, port, timeout=300):
    """SCAN <path>: clamd opens the file itself. Only works when the ClamAV
    add-on sees the same path (it maps /media and /share); no size limit."""
    try:
        with _connect(host, port, timeout) as s:
            s.sendall(b"zSCAN " + path.encode() + b"\0")
            return _parse(_recv_all(s))
    except OSError as e:
        return "error", str(e)


def scan_file(path, host, port, timeout=120, path_fallback=True):
    """Best effort: stream first, fall back to path mode for large files."""
    try:
        with open(path, "rb") as f:
            verdict, detail = scan_stream(f, host, port, timeout)
    except OSError as e:
        return "error", str(e)
    if verdict == "too_large" and path_fallback:
        v2, d2 = scan_path(path, host, port, max(timeout, 300))
        if v2 in ("clean", "infected"):
            return v2, d2
        return "too_large", detail
    return verdict, detail


def self_test(host, port, timeout=10):
    """PING then an EICAR round trip. Returns (ok, message)."""
    if not ping(host, port, timeout):
        return False, f"clamd auf {host}:{port} antwortet nicht auf PING"
    import io
    verdict, detail = scan_stream(io.BytesIO(EICAR), host, port, timeout)
    if verdict == "infected":
        return True, f"PONG erhalten, EICAR erkannt als {detail or 'Eicar-Test-Signature'}"
    return False, f"PONG erhalten, aber EICAR nicht erkannt ({verdict}: {detail})"


def make_hook(opt):
    """SCAN_HOOK for share_web: scan(path) -> (verdict, detail) with the
    add-on's policy applied. Returns None when scanning is disabled."""
    if not opt("share_clamav_enabled", False):
        return None
    host = str(opt("share_clamav_host", "127.0.0.1") or "127.0.0.1")
    port = int(opt("share_clamav_port", 3310) or 3310)
    timeout = int(opt("share_clamav_timeout", 120) or 120)
    on_error = str(opt("share_clamav_on_error", "reject") or "reject")
    on_large = str(opt("share_clamav_large_file", "accept") or "accept")

    def hook(path):
        verdict, detail = scan_file(path, host, port, timeout)
        if verdict == "clean":
            return "clean", ""
        if verdict == "infected":
            return "infected", detail
        if verdict == "too_large":
            return ("clean", f"unscanned: {detail}") if on_large == "accept" else ("error", detail)
        return ("clean", f"unscanned: {detail}") if on_error == "accept" else ("error", detail)

    return hook
