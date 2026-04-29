#!/bin/sh
# mount_helper.sh
# Privilegierter Daemon für Mount-Operationen, kommuniziert über FIFO
# Host-Mount Modus: Mountet direkt nach /media/ (geteilt via map: media:rw)
# Container-Mount Modus: Mountet nach beliebigem Pfad (nur in diesem Container sichtbar)

FIFO="/tmp/mount_cmd"
RESULT="/tmp/mount_result"

# Host-Mount Modus: wird über Umgebungsvariable gesteuert
HOST_MOUNT="${HOST_MOUNT:-false}"

# Aufräumen
rm -f "$FIFO" "$RESULT"

# FIFO anlegen
mkfifo "$FIFO" || { echo "FEHLER: mkfifo gescheitert"; exit 1; }

echo "[mount_helper] Bereit auf $FIFO"
echo "[mount_helper] Host-Mount Modus: $HOST_MOUNT"
if [ "$HOST_MOUNT" = "true" ]; then
    echo "[mount_helper] Mounts nach /media/ werden über map:media:rw mit anderen Addons geteilt"
fi

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

                echo "[mount_helper] MOUNT: $DEVICE -> $MOUNTPOINT (fs=$FSTYPE, host=$HOST_MOUNT)"

                # Verify device exists
                if [ ! -b "$DEVICE" ]; then
                    echo "[mount_helper] FEHLER: $DEVICE ist kein Block-Device"
                    printf '1|%s ist kein Block-Device oder nicht gefunden\n' "$DEVICE" > "$RESULT"
                    continue
                fi

                if [ "$HOST_MOUNT" = "true" ]; then
                    # ── HOST-MOUNT ──
                    # Erzwinge Mount nach /media/NAME (geteilt mit allen Addons via map:media:rw)
                    MOUNT_NAME=$(basename "$MOUNTPOINT")
                    MOUNTPOINT="/media/${MOUNT_NAME}"
                    echo "[mount_helper] Host-Mount: $DEVICE -> $MOUNTPOINT (sichtbar für alle Addons)"
                fi

                mkdir -p "$MOUNTPOINT" 2>/dev/null

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

                # Debug bei Fehler
                if [ $RC -ne 0 ]; then
                    echo "[mount_helper] Mount fehlgeschlagen (rc=$RC): $OUT"
                    echo "[mount_helper] DEBUG: id=$(id)"
                    echo "[mount_helper] DEBUG: ls -la $DEVICE = $(ls -la "$DEVICE" 2>&1)"
                    echo "[mount_helper] DEBUG: blkid $DEVICE = $(blkid "$DEVICE" 2>&1)"
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
