#!/usr/bin/env python3
"""SSD TRIM: runs fstrim on a mounted drive and remembers the result.

fstrim can take minutes on a large drive, so it runs in a background thread and
the web interface polls for the result. Not every USB-SATA bridge passes TRIM
through to the SSD; fstrim then fails with "not supported", which is reported
as a normal result rather than as an error.
"""
import json
import os
import re
import subprocess
import threading
import time

BYTES = re.compile(r"\((\d+) bytes\) trimmed")
TIMEOUT = 3600  # seconds


def parse_result(returncode, stdout, stderr):
    """Turn fstrim's exit code and output into a result dict."""
    output = (stderr or stdout or "").strip()
    if returncode == 0:
        match = BYTES.search(stdout or "")
        return {"status": "ok", "bytes": int(match.group(1)) if match else None, "message": ""}
    if "not supported" in output.lower():
        return {"status": "unsupported", "bytes": None, "message": output}
    return {"status": "error", "bytes": None, "message": output or f"fstrim exit code {returncode}"}


def run_fstrim(mountpoint, runner=subprocess.run):
    try:
        done = runner(["fstrim", "-v", mountpoint], capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError:
        return {"status": "error", "bytes": None, "message": "fstrim is not installed"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "bytes": None, "message": f"fstrim timed out after {TIMEOUT} seconds"}
    return parse_result(done.returncode, done.stdout, done.stderr)


def discard_supported(disk, sys_block="/sys/block"):
    """True if the kernel reports that the disk accepts TRIM."""
    try:
        with open(os.path.join(sys_block, disk, "queue", "discard_max_bytes")) as fh:
            return int(fh.read().strip() or 0) > 0
    except (OSError, ValueError):
        return False


def is_due(last_run, days, now):
    return days > 0 and (last_run is None or now - last_run >= days * 86400)


class Trimmer:
    """Runs one fstrim per mountpoint and keeps the last result in a JSON file."""

    def __init__(self, path, run=run_fstrim, clock=time.time):
        self.path = path
        self.run = run
        self.clock = clock
        self.lock = threading.Lock()
        self.running = set()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data):
        tmp = f"{self.path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self.path)
        except OSError as exc:
            print(f"[TRIM] cannot save results: {exc}", flush=True)

    def status(self):
        with self.lock:
            return {"running": sorted(self.running), "results": self._load()}

    def last_run(self, mountpoint):
        with self.lock:
            return self._load().get(mountpoint, {}).get("time")

    def execute(self, mountpoint):
        """Run fstrim now and return its result; None if it is already running."""
        with self.lock:
            if mountpoint in self.running:
                return None
            self.running.add(mountpoint)
        result = {"status": "error", "bytes": None, "message": "fstrim failed"}
        try:
            result = dict(self.run(mountpoint))
        finally:
            result["time"] = self.clock()
            with self.lock:
                data = self._load()
                data[mountpoint] = result
                self._save(data)
                self.running.discard(mountpoint)
        print(f"[TRIM] {mountpoint}: {result['status']} {result.get('bytes') or ''} "
              f"{result.get('message') or ''}".rstrip(), flush=True)
        return result

    def start(self, mountpoint):
        """Run fstrim in the background. False if it is already running."""
        with self.lock:
            if mountpoint in self.running:
                return False
        threading.Thread(target=self.execute, args=(mountpoint,), daemon=True, name="fstrim").start()
        return True
