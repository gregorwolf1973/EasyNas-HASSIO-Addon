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

while true; do
    # Blockierendes Lesen – wartet auf nächsten Befehl
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
                mkdir -p "$MOUNTPOINT" 2>/dev/null
                if [ -n "$FSTYPE" ] && [ "$FSTYPE" != "auto" ]; then
                    OUT=$(mount -t "$FSTYPE" "$DEVICE" "$MOUNTPOINT" 2>&1)
                else
                    OUT=$(mount "$DEVICE" "$MOUNTPOINT" 2>&1)
                fi
                RC=$?
                printf '%s|%s\n' "$RC" "$OUT" > "$RESULT"
                ;;
            UMOUNT)
                OUT=$(umount "$ARG1" 2>&1)
                RC=$?
                printf '%s|%s\n' "$RC" "$OUT" > "$RESULT"
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
