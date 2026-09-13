#!/usr/bin/env python3
"""Stream a directory as a ZIP archive without touching the disk.

stdlib zipfile writes to a non-seekable sink, so it emits data descriptors
instead of seeking back - exactly what a streaming HTTP response needs.

Rules that keep the archive valid under real-world conditions:
* preflight() walks the tree first so the caller can refuse with a proper
  status code before the first byte is sent;
* every source file is opened *before* its entry is created, so a file that
  vanishes mid-stream is skipped and listed in _MISSING.txt instead of
  corrupting the archive;
* if the running size exceeds the estimate by a margin, no further entries
  are added, ZIP-INCOMPLETE.txt explains why, and the archive is closed
  cleanly. A short but valid ZIP beats a truncated one.
"""

import os
import shutil
import time
import zipfile

SKIP_SUFFIX = (".part",)
CHUNK = 1024 * 1024


class TooBig(Exception):
    def __init__(self, total, count, limit_bytes, limit_files):
        super().__init__("archive too big")
        self.total, self.count, self.limit_bytes, self.limit_files = total, count, limit_bytes, limit_files


class _Sink:
    """Write target for ZipFile that hands the bytes to a generator."""

    def __init__(self):
        self.buf = bytearray()
        self.pos = 0

    def write(self, b):
        self.buf += b
        self.pos += len(b)
        return len(b)

    def flush(self):
        pass

    def tell(self):
        return self.pos

    def seekable(self):
        return False

    def take(self):
        out = bytes(self.buf)
        self.buf.clear()
        return out


def _wanted(name):
    return not name.startswith(".") and not name.endswith(SKIP_SUFFIX)


def walk(root):
    """(abs_path, arcname, size, mtime) for every regular file; symlinks skipped."""
    root = os.path.abspath(root)
    base = os.path.basename(root.rstrip(os.sep)) or "archive"
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames
                             if _wanted(d) and not os.path.islink(os.path.join(dirpath, d)))
        rel_dir = os.path.relpath(dirpath, root)
        for fn in sorted(filenames):
            if not _wanted(fn):
                continue
            p = os.path.join(dirpath, fn)
            if os.path.islink(p):
                continue
            try:
                st = os.lstat(p)
            except OSError:
                continue
            arc = base if rel_dir == "." else os.path.join(base, rel_dir)
            arc = os.path.join(arc, fn).replace(os.sep, "/")
            yield p, arc, st.st_size, st.st_mtime


def preflight(root, limit_bytes, limit_files, max_seconds=8):
    """Total size and file count, or TooBig. Aborts the walk on the time budget
    (large USB trees) and treats that as too big to be safe."""
    total, count = 0, 0
    deadline = time.time() + max_seconds
    for _, _, size, _ in walk(root):
        total += size
        count += 1
        if total > limit_bytes or count > limit_files or time.time() > deadline:
            raise TooBig(total, count, limit_bytes, limit_files)
    return total, count


def _zinfo(arc, mtime, size, compress):
    lt = time.localtime(mtime)
    zi = zipfile.ZipInfo(arc, date_time=(max(1980, lt.tm_year), lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min, lt.tm_sec))
    zi.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    zi.file_size = size
    zi.external_attr = 0o644 << 16
    return zi


def stream(root, estimate_bytes=0, compress=False, margin=1.2):
    """Generator of bytes for the ZIP of `root`."""
    sink = _Sink()
    zf = zipfile.ZipFile(sink, "w", allowZip64=True)
    missing, written, truncated = [], 0, False
    hard_cap = int(estimate_bytes * margin) if estimate_bytes else None
    try:
        for path, arc, size, mtime in walk(root):
            if hard_cap is not None and written > hard_cap:
                truncated = True
                break
            try:
                src = open(path, "rb")           # open FIRST, then create the entry
            except OSError:
                missing.append(arc)
                continue
            with src:
                zi = _zinfo(arc, mtime, size, compress)
                try:
                    with zf.open(zi, "w", force_zip64=size >= 0x7FFFFFFF) as dst:
                        while True:
                            chunk = src.read(CHUNK)
                            if not chunk:
                                break
                            dst.write(chunk)
                            written += len(chunk)
                            if len(sink.buf) >= CHUNK:
                                yield sink.take()
                except OSError:
                    missing.append(arc)
            if sink.buf:
                yield sink.take()
        if missing:
            zf.writestr(_zinfo("_MISSING.txt", time.time(), 0, False),
                        "These files disappeared while the archive was being created:\n" + "\n".join(missing) + "\n")
        if truncated:
            zf.writestr(_zinfo("ZIP-INCOMPLETE.txt", time.time(), 0, False),
                        "The folder grew beyond the size checked at the start.\n"
                        "This archive was closed cleanly but does not contain every file.\n")
    finally:
        zf.close()
    if sink.buf:
        yield sink.take()
