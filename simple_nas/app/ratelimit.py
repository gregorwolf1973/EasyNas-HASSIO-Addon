#!/usr/bin/env python3
"""Sliding-window rate limiting and lockouts, in memory, stdlib only.

One process serves both ports, so a single shared LIMITER is enough. State
is lost on restart, which is fine: the point is to make guessing slow, not
to keep a permanent record.
"""

import threading
import time
from collections import deque


def _split_key(key):
    """authfail:ip:1.2.3.4 -> ("ip", "1.2.3.4"); ban:1.2.3.4 -> ("ban", "1.2.3.4")"""
    parts = key.split(":", 2)
    if parts[0] == "authfail" and len(parts) == 3:
        return parts[1], parts[2]
    return parts[0], parts[-1] if len(parts) > 1 else ""


class Limiter:
    def __init__(self, clock=None):
        self._clock = clock or time.monotonic
        self._buckets = {}
        self._locks = {}          # key -> (until, escalation count, last lock time)
        self._lock = threading.Lock()
        self._ops = 0

    def _prune(self, key, window, now):
        q = self._buckets.get(key)
        if q is None:
            return None
        while q and q[0] <= now - window:
            q.popleft()
        if not q:
            self._buckets.pop(key, None)
            return None
        return q

    def _sweep(self, now):
        """Every few hundred calls drop buckets whose newest entry is old."""
        self._ops += 1
        if self._ops % 200:
            return
        for key in list(self._buckets):
            q = self._buckets[key]
            if not q or q[-1] <= now - 3600:
                self._buckets.pop(key, None)

    def hit(self, key, limit, window):
        """Record one event. Returns (allowed, retry_after_seconds)."""
        now = self._clock()
        with self._lock:
            self._sweep(now)
            q = self._prune(key, window, now)
            if q is None:
                q = self._buckets[key] = deque()
            if len(q) >= limit:
                return False, max(1, int(q[0] + window - now) + 1)
            q.append(now)
            return True, 0

    def count(self, key, window):
        now = self._clock()
        with self._lock:
            q = self._prune(key, window, now)
            return len(q) if q else 0

    def record(self, key):
        """Add an event without checking a limit (for failure counters)."""
        with self._lock:
            self._buckets.setdefault(key, deque()).append(self._clock())

    def locked(self, key, limit, window):
        return self.banned(key) or self.count(key, window) >= limit

    # ── escalating lockouts ──────────────────────────────────────────────
    def lock(self, key, base, cap, decay=24 * 3600):
        """Lock key for base * 2^(n-1) seconds, n = lockouts within `decay`.
        First lockout 15 min, second 30, then 1 h, 2 h ... up to cap."""
        now = self._clock()
        with self._lock:
            until, n, last = self._locks.get(key, (0, 0, 0))
            if last and now - last > decay:
                n = 0
            n += 1
            dur = min(cap, base * (2 ** (n - 1)))
            self._locks[key] = (now + dur, n, now)
            return dur

    def banned(self, key):
        now = self._clock()
        with self._lock:
            entry = self._locks.get(key)
            if not entry:
                return False
            if entry[0] > now:
                return True
            return False

    def ban_remaining(self, key):
        now = self._clock()
        with self._lock:
            entry = self._locks.get(key)
            return max(0, int(entry[0] - now)) if entry else 0

    def retry_after(self, key, window):
        now = self._clock()
        with self._lock:
            q = self._prune(key, window, now)
            return max(1, int(q[0] + window - now) + 1) if q else 0

    def snapshot(self, window=15 * 60):
        """Active lockouts and current failure counters, for the admin UI.

        Without this the protection is invisible: a user who types a wrong
        password five times sees nothing happen and cannot tell whether the
        lockout is broken or simply not reached yet.
        """
        now = self._clock()
        locks, counters = [], []
        with self._lock:
            for key, (until, n, _last) in list(self._locks.items()):
                if until > now:
                    kind, target = _split_key(key)
                    locks.append({"key": key, "kind": kind, "target": target,
                                  "remaining": int(until - now), "strikes": n})
            for key in list(self._buckets):
                if not key.startswith("authfail:"):
                    continue
                q = self._prune(key, window, now)
                if q:
                    kind, target = _split_key(key)
                    counters.append({"key": key, "kind": kind, "target": target, "count": len(q)})
        locks.sort(key=lambda x: -x["remaining"])
        counters.sort(key=lambda x: -x["count"])
        return {"locks": locks, "counters": counters}

    def clear(self, key=None, prefix=None):
        with self._lock:
            if key is not None:
                self._buckets.pop(key, None)
                self._locks.pop(key, None)
            if prefix is not None:
                for k in list(self._buckets):
                    if k.startswith(prefix):
                        self._buckets.pop(k, None)
                for k in list(self._locks):
                    if k.startswith(prefix):
                        self._locks.pop(k, None)


# Limits for the public share site. (limit, window seconds)
REQ_PER_IP = (240, 60)
AUTHFAIL_IP = (10, 15 * 60)
AUTHFAIL_LINK = (20, 15 * 60)
AUTHFAIL_USER = (10, 15 * 60)
UPLOAD_PER_IP = (30, 60 * 60)
SCAN_PER_IP = (20, 10 * 60)         # requests for unknown links -> token scanner
LOCK_BASE = 15 * 60                 # first lockout
LOCK_CAP = 24 * 3600                # repeat offenders end up here

LIMITER = Limiter()
