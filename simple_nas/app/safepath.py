#!/usr/bin/env python3
"""Path confinement for Simple NAS.

Every filesystem path that reaches this add-on from a browser must pass through
here first. The add-on runs as root with full_access, so an unchecked path is a
read/write primitive over the whole Home Assistant host.

Two rules drive the implementation:

* Resolve symlinks *after* joining, then compare. Checking the string before
  resolution is useless: a symlink inside an allowed root can point anywhere.
* Compare with ``real_root + os.sep``, never a bare ``startswith``. Otherwise
  ``/media/foobar`` counts as a hit for the root ``/media/foo``.
"""

import os

__all__ = [
    "PathError",
    "DENY_ROOTS",
    "real",
    "is_within",
    "resolve_within",
    "resolve_in_roots",
    "resolve_new",
    "open_new_file",
]


class PathError(Exception):
    """A path was rejected. The message is for the log, never for the client."""


# Never reachable through the file API, whatever the configuration says.
# /data holds the password hashes, /config the Home Assistant secrets.
DENY_ROOTS = (
    "/data", "/ssl", "/etc", "/proc", "/sys", "/dev", "/var", "/run", "/boot", "/root",
)


def real(path: str) -> str:
    """Absolute path with every symlink resolved."""
    return os.path.realpath(os.path.abspath(path))


def is_within(root: str, candidate: str) -> bool:
    """True when candidate is root itself or sits below it. Both must be real paths."""
    return candidate == root or candidate.startswith(root.rstrip(os.sep) + os.sep)


def _reject_denied(real_path: str) -> None:
    # Resolve the deny roots too: on some systems /var is a symlink, and a raw
    # string compare would then miss it.
    for deny in DENY_ROOTS:
        if is_within(real(deny), real_path):
            raise PathError(f"denied root: {real_path}")


def _check_segments(rel: str) -> str:
    """Reject traversal and NUL before touching the filesystem."""
    rel = (rel or "").replace("\\", "/")
    if "\x00" in rel:
        raise PathError("NUL in path")
    for seg in rel.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise PathError("traversal")
    return rel


def resolve_within(root: str, rel: str) -> str:
    """Resolve a client-supplied path relative to root.

    The result is guaranteed to be root or below it, after full symlink
    resolution. Raises PathError otherwise.
    """
    real_root = real(root)
    if not os.path.isdir(real_root):
        raise PathError(f"root unavailable: {root}")
    rel = _check_segments(rel)
    stripped = rel.strip("/")
    candidate = os.path.join(real_root, stripped) if stripped else real_root
    resolved = real(candidate)
    if not is_within(real_root, resolved):
        raise PathError(f"escapes root: {resolved}")
    return resolved


def resolve_in_roots(roots, abs_path: str) -> str:
    """Resolve an absolute path supplied by the admin UI against the allowed roots.

    Used by the admin file API, where the client sends whole paths rather than
    a path relative to one known root.
    """
    if not abs_path or not str(abs_path).strip():
        raise PathError("empty path")
    if "\x00" in str(abs_path):
        raise PathError("NUL in path")
    resolved = real(str(abs_path).strip())
    _reject_denied(resolved)
    for root in roots or ():
        real_root = real(root)
        if is_within(real_root, resolved):
            return resolved
    raise PathError(f"outside allowed roots: {resolved}")


def resolve_new(root: str, rel_dir: str, leaf: str) -> str:
    """Destination for a file or directory that does not exist yet.

    The parent is resolved and confined; the leaf is validated separately
    because realpath() on a missing leaf would just append it literally.
    """
    parent = resolve_within(root, rel_dir)
    if leaf in ("", ".", "..") or "/" in leaf or "\\" in leaf or "\x00" in leaf:
        raise PathError(f"bad name: {leaf!r}")
    dest = os.path.join(parent, leaf)
    if os.path.islink(dest):
        raise PathError("destination is a symlink")
    return dest


def open_new_file(dest: str, mode: int = 0o644):
    """Create a file, failing if it exists or is a symlink.

    O_EXCL|O_NOFOLLOW closes the window in which a Samba client could swap the
    destination for a symlink between the check above and the write below.
    O_NOFOLLOW only exists on POSIX; on Windows (tests only) it is dropped.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    return os.open(dest, flags, mode)
