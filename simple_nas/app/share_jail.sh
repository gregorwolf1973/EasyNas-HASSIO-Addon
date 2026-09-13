#!/bin/sh
# Sandbox launcher for the public share worker. Runs inside a fresh mount
# namespace (the parent starts it with `unshare --mount --propagation private`).
# It blinds the process to everything outside the shared folders, then drops to
# an unprivileged uid with only the capabilities uploads need, and execs the
# worker. If any step fails the process exits non-zero and app.py falls back to
# running the site in the main process.
#
# The /data file list MUST match sharesandbox.JAIL_DATA_FILES.
set -eu

REAL=/run/nasjail
DATA=/data

# 1. keep a live handle to the real /data, then blank /data with a tmpfs.
mkdir -p "$REAL"
mount --bind "$DATA" "$REAL"
mount -t tmpfs -o mode=0755 tmpfs "$DATA"

# 2. bind back only the files the worker is allowed to see.
mkdir -p "$DATA/tmp"
if [ -d "$REAL/tmp" ]; then
    mount --bind "$REAL/tmp" "$DATA/tmp"
fi
for f in share_links.json share_accounts.json share_auth.json shares.json \
         share_counters.json share_locks.json share_unlock.json \
         share_worker_opts.json share_access.log share_access.log.1; do
    if [ -e "$REAL/$f" ]; then
        touch "$DATA/$f"
        mount --bind "$REAL/$f" "$DATA/$f"
    fi
done

# 3. blind the handle itself, so the hidden /data files cannot be reached
#    through /run/nasjail. The file binds under /data keep working.
mount -t tmpfs -o mode=0000 tmpfs "$REAL"

# 4. hide the sensitive trees entirely (HA config, SSL, other add-ons, backups).
for d in /config /ssl /addon_configs /backup; do
    if [ -d "$d" ]; then
        mount -t tmpfs -o mode=0000 tmpfs "$d"
    fi
done

# 5. drop privileges and exec the worker. nobody keeps only the file caps an
#    upload needs (write into a share folder, hand the file to its Samba owner);
#    SYS_ADMIN, SYS_RAWIO and the rest are gone, and no-new-privs pins that.
exec setpriv \
    --reuid nobody --regid nobody --init-groups \
    --no-new-privs \
    --inh-caps -all,+chown,+dac_override,+fowner \
    --ambient-caps -all,+chown,+dac_override,+fowner \
    --bounding-set -all,+chown,+dac_override,+fowner \
    python3 /app/share_worker.py
