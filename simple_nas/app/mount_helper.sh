#!/bin/sh
# mount_helper.sh
# Privilegierter Daemon für Mount-Operationen, kommuniziert über FIFO

FIFO="/tmp/mount_cmd"
RESULT="/tmp/mount_result"

# Aufräumen
rm -f "$FIFO" "$RESULT"

# FIFO anlegen
mkfifo "$FIFO" || { echo "FEHLER: mkfifo gescheitert"; exit 1; }

echo "[mount_helper] Bereit auf $FIFO"
echo "[mount_helper] Capabilities: $(cat /proc/self/status 2>/dev/null | grep Cap || echo 'n/a')"

while true; do
    if read -r line < "$FIFO"; then
        ACTION=$(echo "$line" | cut -d'|' -f1)
        ARG1=$(  echo "$line" | cut -d'|' -f2)
        ARG2=$(  echo "$line" | cut -d'|' -f3)
        ARG3=$(  echo "$line" | cut -d'|' -f4)

        case "$ACTION" in
            MOUNT)
                DEVICE="$ARG1"
                MOUNTPOINT="$ARG2"
                FSTYPE="$ARG3"

                echo "[mount_helper] MOUNT: $DEVICE -> $MOUNTPOINT (fs=$FSTYPE)"
                mkdir -p "$MOUNTPOINT" 2>/dev/null

                # Verify device exists
                if [ ! -b "$DEVICE" ]; then
                    echo "[mount_helper] FEHLER: $DEVICE ist kein Block-Device"
                    printf '1|%s ist kein Block-Device oder nicht gefunden\n' "$DEVICE" > "$RESULT"
                    continue
                fi

                # Try mount
                if [ -n "$FSTYPE" ] && [ "$FSTYPE" != "auto" ]; then
                    echo "[mount_helper] Versuche: mount -t $FSTYPE $DEVICE $MOUNTPOINT"
                    OUT=$(mount -t "$FSTYPE" "$DEVICE" "$MOUNTPOINT" 2>&1)
                    RC=$?
                else
                    echo "[mount_helper] Versuche: mount $DEVICE $MOUNTPOINT"
                    OUT=$(mount "$DEVICE" "$MOUNTPOINT" 2>&1)
                    RC=$?
                fi

                # If failed, log debug info
                if [ $RC -ne 0 ]; then
                    echo "[mount_helper] Mount fehlgeschlagen (rc=$RC): $OUT"
                    echo "[mount_helper] DEBUG: id=$(id)"
                    echo "[mount_helper] DEBUG: ls -la $DEVICE = $(ls -la "$DEVICE" 2>&1)"
                    echo "[mount_helper] DEBUG: blkid $DEVICE = $(blkid "$DEVICE" 2>&1)"
                    echo "[mount_helper] DEBUG: mountinfo (last 5):"
                    tail -5 /proc/self/mountinfo 2>/dev/null
                fi

                printf '%s|%s\n' "$RC" "$OUT" > "$RESULT"
                echo "[mount_helper] MOUNT result: rc=$RC"
                ;;
            UMOUNT)
                echo "[mount_helper] UMOUNT: $ARG1"
                OUT=$(umount "$ARG1" 2>&1)
                RC=$?
                printf '%s|%s\n' "$RC" "$OUT" > "$RESULT"
                echo "[mount_helper] UMOUNT result: rc=$RC"
                ;;
            UMOUNT_LAZY)
                echo "[mount_helper] UMOUNT_LAZY: $ARG1"
                OUT=$(umount -l "$ARG1" 2>&1)
                RC=$?
                printf '%s|%s\n' "$RC" "$OUT" > "$RESULT"
                echo "[mount_helper] UMOUNT_LAZY result: rc=$RC"
                ;;
            FUSER)
                # Show what's holding the mount busy
                OUT=$(fuser -mv "$ARG1" 2>&1; lsof +D "$ARG1" 2>&1 | head -20)
                printf '0|%s\n' "$OUT" > "$RESULT"
                ;;
            BIND)
                SRC="$ARG1"
                DST="$ARG2"
                echo "[mount_helper] BIND: $SRC -> $DST"
                mkdir -p "$DST" 2>/dev/null
                # If something is already mounted at DST, treat as success (idempotent restore)
                if mountpoint -q "$DST" 2>/dev/null; then
                    echo "[mount_helper] BIND: $DST already a mountpoint — skipping"
                    printf '0|already-mounted\n' > "$RESULT"
                    continue
                fi
                OUT=$(mount --bind "$SRC" "$DST" 2>&1)
                RC=$?
                if [ $RC -eq 0 ]; then
                    # Make the bind shared so it propagates into other containers
                    mount --make-shared "$DST" 2>/dev/null || true
                else
                    echo "[mount_helper] BIND failed (rc=$RC): $OUT"
                fi
                printf '%s|%s\n' "$RC" "$OUT" > "$RESULT"
                echo "[mount_helper] BIND result: rc=$RC"
                ;;
            MKDIR)
                mkdir -p "$ARG1" 2>/dev/null
                printf '0|\n' > "$RESULT"
                ;;
            *)
                printf '1|Unbekannter Befehl: %s\n' "$ACTION" > "$RESULT"
                ;;
        esac
    fi
done
