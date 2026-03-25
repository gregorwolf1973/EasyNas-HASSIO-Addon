#!/bin/sh
# mount_helper.sh - Läuft als privilegierter Daemon, empfängt Mount-Befehle über FIFO
# Wird beim Start einmalig aufgerufen, bleibt im Hintergrund

FIFO="/tmp/mount_cmd"
RESULT="/tmp/mount_result"

rm -f "$FIFO" "$RESULT"
mkfifo "$FIFO"

echo "mount_helper ready"

while true; do
    if read -r line < "$FIFO"; then
        ACTION=$(echo "$line" | cut -d'|' -f1)
        ARG1=$(echo "$line" | cut -d'|' -f2)
        ARG2=$(echo "$line" | cut -d'|' -f3)
        ARG3=$(echo "$line" | cut -d'|' -f4)

        case "$ACTION" in
            MOUNT)
                DEVICE="$ARG1"
                MOUNTPOINT="$ARG2"
                FSTYPE="$ARG3"
                mkdir -p "$MOUNTPOINT"
                if [ -n "$FSTYPE" ] && [ "$FSTYPE" != "auto" ]; then
                    OUT=$(mount -t "$FSTYPE" "$DEVICE" "$MOUNTPOINT" 2>&1)
                else
                    OUT=$(mount "$DEVICE" "$MOUNTPOINT" 2>&1)
                fi
                RC=$?
                echo "$RC|$OUT" > "$RESULT"
                ;;
            UMOUNT)
                TARGET="$ARG1"
                OUT=$(umount "$TARGET" 2>&1)
                RC=$?
                echo "$RC|$OUT" > "$RESULT"
                ;;
            MKDIR)
                mkdir -p "$ARG1" 2>&1
                echo "0|" > "$RESULT"
                ;;
        esac
    fi
done
