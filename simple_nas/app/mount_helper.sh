#!/bin/sh
# mount_helper.sh
# Privilegierter Daemon für Mount-Operationen, kommuniziert über FIFO
# Unterstützt Container-Mount (Standard) und Host-Mount (nsenter) Modi

FIFO="/tmp/mount_cmd"
RESULT="/tmp/mount_result"

# Host-Mount Modus: wird über Umgebungsvariable gesteuert
HOST_MOUNT="${HOST_MOUNT:-false}"
# Host-Pfad für Supervisor Media (dort wo alle Container /media/ sehen)
HOST_MEDIA="/mnt/data/supervisor/media"

# Aufräumen
rm -f "$FIFO" "$RESULT"

# FIFO anlegen
mkfifo "$FIFO" || { echo "FEHLER: mkfifo gescheitert"; exit 1; }

echo "[mount_helper] Bereit auf $FIFO"
echo "[mount_helper] Host-Mount Modus: $HOST_MOUNT"

if [ "$HOST_MOUNT" = "true" ]; then
    # Prüfe ob nsenter verfügbar und Host-PID-Namespace erreichbar ist
    if nsenter --mount=/proc/1/ns/mnt -- ls / > /dev/null 2>&1; then
        echo "[mount_helper] Host-Namespace erreichbar via nsenter"
    else
        echo "[mount_helper] WARNUNG: Host-Namespace nicht erreichbar! Falle zurück auf Container-Mount."
        echo "[mount_helper] Prüfe ob host_pid: true in config.yaml gesetzt ist."
        HOST_MOUNT="false"
    fi
fi

host_exec() {
    # Führt einen Befehl im Host-Mount-Namespace aus
    nsenter --mount=/proc/1/ns/mnt -- "$@"
}

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
                    # ── HOST-MOUNT via nsenter ──
                    # Extrahiere den Mount-Namen aus dem Pfad
                    MOUNT_NAME=$(basename "$MOUNTPOINT")
                    HOST_PATH="${HOST_MEDIA}/${MOUNT_NAME}"

                    echo "[mount_helper] Host-Mount: $DEVICE -> $HOST_PATH (sichtbar als /media/$MOUNT_NAME)"

                    # Erstelle Mountpoint auf dem Host
                    host_exec mkdir -p "$HOST_PATH" 2>/dev/null

                    # Mount auf dem Host ausführen
                    if [ -n "$FSTYPE" ] && [ "$FSTYPE" != "auto" ]; then
                        OUT=$(host_exec mount -t "$FSTYPE" "$DEVICE" "$HOST_PATH" 2>&1)
                        RC=$?
                    else
                        OUT=$(host_exec mount "$DEVICE" "$HOST_PATH" 2>&1)
                        RC=$?
                    fi

                    if [ $RC -eq 0 ]; then
                        # Zusätzlich im Container mounten damit wir sofort Zugriff haben
                        mkdir -p "$MOUNTPOINT" 2>/dev/null
                        mount --bind "/media/$MOUNT_NAME" "$MOUNTPOINT" 2>/dev/null || true
                        echo "[mount_helper] Host-Mount erfolgreich: $HOST_PATH (alle Addons sehen /media/$MOUNT_NAME)"
                    else
                        echo "[mount_helper] Host-Mount fehlgeschlagen (rc=$RC): $OUT"
                    fi
                else
                    # ── CONTAINER-MOUNT (Standard) ──
                    mkdir -p "$MOUNTPOINT" 2>/dev/null

                    if [ -n "$FSTYPE" ] && [ "$FSTYPE" != "auto" ]; then
                        OUT=$(mount -t "$FSTYPE" "$DEVICE" "$MOUNTPOINT" 2>&1)
                        RC=$?
                    else
                        OUT=$(mount "$DEVICE" "$MOUNTPOINT" 2>&1)
                        RC=$?
                    fi
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
                if [ "$HOST_MOUNT" = "true" ]; then
                    MOUNT_NAME=$(basename "$ARG1")
                    HOST_PATH="${HOST_MEDIA}/${MOUNT_NAME}"
                    # Erst Container-Bind-Mount lösen
                    umount "$ARG1" 2>/dev/null || true
                    # Dann Host-Mount lösen
                    OUT=$(host_exec umount "$HOST_PATH" 2>&1)
                    RC=$?
                    # Aufräumen
                    host_exec rmdir "$HOST_PATH" 2>/dev/null || true
                    echo "[mount_helper] Host-Umount: $HOST_PATH rc=$RC"
                else
                    OUT=$(umount "$ARG1" 2>&1)
                    RC=$?
                fi
                printf '%s|%s\n' "$RC" "$OUT" > "$RESULT"
                echo "[mount_helper] UMOUNT result: rc=$RC"
                ;;

            MKDIR)
                mkdir -p "$ARG1" 2>/dev/null
                if [ "$HOST_MOUNT" = "true" ]; then
                    MOUNT_NAME=$(basename "$ARG1")
                    host_exec mkdir -p "${HOST_MEDIA}/${MOUNT_NAME}" 2>/dev/null || true
                fi
                printf '0|\n' > "$RESULT"
                ;;

            *)
                printf '1|Unbekannter Befehl: %s\n' "$ACTION" > "$RESULT"
                ;;
        esac
    fi
done
