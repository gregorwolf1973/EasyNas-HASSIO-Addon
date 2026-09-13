#!/bin/sh
# Sandbox launcher for the public share worker. Runs inside a fresh mount
# namespace (the parent starts it with `unshare --mount --propagation private`).
# It blinds the process to the sensitive trees and files, then drops to an
# unprivileged uid with only the capabilities uploads need, and execs the
# worker. If any step fails the process exits non-zero and app.py falls back to
# running the site in the main process.
#
# /data stays LIVE (the real directory), so the worker's atomic writes
# (counters, lock snapshot) and the admin's edits (link rotation, account
# changes) are seen by both sides - a per-file bind mount would break both,
# because renaming onto a mount point fails and a replaced file gets a new
# inode the bind would not follow. The secret files in /data are masked
# individually instead. The mask lists MUST match sharesandbox.JAIL_MASK_*.
set -eu

# 1. hide the sensitive trees entirely (HA config, SSL, other add-ons, backups).
for d in /config /ssl /addon_configs /backup; do
    if [ -d "$d" ]; then
        mount -t tmpfs -o mode=0000 tmpfs "$d"
    fi
done

# 2. mask the secret files/dirs inside the otherwise-live /data.
EMPTY_FILE=/run/nasjail_empty
: > "$EMPTY_FILE"
chmod 0000 "$EMPTY_FILE"
for f in admin_auth.json options.json; do
    if [ -e "/data/$f" ]; then
        mount --bind "$EMPTY_FILE" "/data/$f"
    fi
done
for d in samba; do
    if [ -d "/data/$d" ]; then
        mount -t tmpfs -o mode=0000 tmpfs "/data/$d"
    fi
done

# 3. drop privileges and exec the worker. nobody keeps only the file caps an
#    upload needs (write into a share folder, hand the file to its Samba owner);
#    SYS_ADMIN, SYS_RAWIO and the rest are gone, and no-new-privs pins that.
exec setpriv \
    --reuid nobody --regid nobody --init-groups \
    --no-new-privs \
    --inh-caps -all,+chown,+dac_override,+fowner \
    --ambient-caps -all,+chown,+dac_override,+fowner \
    --bounding-set -all,+chown,+dac_override,+fowner \
    python3 /app/share_worker.py
