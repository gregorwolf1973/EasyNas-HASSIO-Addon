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

# Wrapper: try util-linux mount first; if fsconfig() is blocked (HA-OS seccomp),
# fall back to busybox mount which uses the old mount(2) syscall.
do_mount() {
    local _OUT _RC
    _OUT=$(mount "$@" 2>&1)
    _RC=$?
    if [ $_RC -ne 0 ] && echo "$_OUT" | grep -q "fsconfig()"; then
        echo "[mount_helper] fsconfig() blocked — retrying with busybox mount"
        _OUT=$(busybox mount "$@" 2>&1)
        _RC=$?
    fi
    echo "$_OUT"
    return $_RC
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
                    echo "[mount_helper] Trying: mount -t $FSTYPE $DEVICE $MOUNTPOINT"
                    OUT=$(do_mount -t "$FSTYPE" "$DEVICE" "$MOUNTPOINT" 2>&1)
                    RC=$?
                else
                    # AUTO: blkid/lsblk sometimes return nothing for by-id
                    # symlinks even though mount -t works fine. Resolve the
                    # symlink first, then escalate through several detectors,
                    # and as a last resort try common FS types directly.
                    REAL_DEV=$(readlink -f "$DEVICE" 2>/dev/null)
                    [ -z "$REAL_DEV" ] && REAL_DEV="$DEVICE"
                    echo "[mount_helper] auto: real device = $REAL_DEV"

                    DETECTED=$(blkid -o value -s TYPE "$REAL_DEV" 2>/dev/null | head -1)
                    [ -z "$DETECTED" ] && DETECTED=$(blkid -p -o value -s TYPE "$REAL_DEV" 2>/dev/null | head -1)
                    [ -z "$DETECTED" ] && DETECTED=$(lsblk -no FSTYPE "$REAL_DEV" 2>/dev/null | head -1 | tr -d ' ')
                    # file -s reads the first bytes directly — works when blkid can't
                    if [ -z "$DETECTED" ] && command -v file >/dev/null 2>&1; then
                        FILE_OUT=$(file -sL "$REAL_DEV" 2>/dev/null)
                        case "$FILE_OUT" in
                            *ext4*)  DETECTED=ext4 ;;
                            *ext3*)  DETECTED=ext3 ;;
                            *ext2*)  DETECTED=ext2 ;;
                            *NTFS*)  DETECTED=ntfs ;;
                            *FAT*)   DETECTED=vfat ;;
                            *exFAT*) DETECTED=exfat ;;
                            *BTRFS*|*btrfs*) DETECTED=btrfs ;;
                            *XFS*)   DETECTED=xfs ;;
                        esac
                        [ -n "$DETECTED" ] && echo "[mount_helper] auto: file -s detected '$DETECTED'"
                    fi
                    case "$DETECTED" in
                        ntfs)  DETECTED=ntfs-3g ;;
                    esac
                    RC=1
                    if [ -n "$DETECTED" ]; then
                        echo "[mount_helper] auto: detected '$DETECTED'"
                        echo "[mount_helper] Trying: mount -t $DETECTED $DEVICE $MOUNTPOINT"
                        OUT=$(do_mount -t "$DETECTED" "$DEVICE" "$MOUNTPOINT" 2>&1)
                        RC=$?
                    fi
                    # Last resort: brute-force the common Linux/Windows types.
                    # mount -t <wrong> on a USB blockdev fails fast and cleanly,
                    # so we can iterate without harm.
                    if [ $RC -ne 0 ]; then
                        if [ -n "$DETECTED" ]; then
                            echo "[mount_helper] auto: '$DETECTED' failed ($OUT) — trying common FS types"
                        else
                            echo "[mount_helper] auto: no FS detected — brute-force common types"
                        fi
                        for TRY_FS in ext4 ext3 ext2 ntfs-3g vfat exfat btrfs xfs; do
                            OUT=$(do_mount -t "$TRY_FS" "$DEVICE" "$MOUNTPOINT" 2>&1)
                            RC=$?
                            if [ $RC -eq 0 ]; then
                                echo "[mount_helper] auto: brute-force succeeded with -t $TRY_FS"
                                DETECTED="$TRY_FS"
                                break
                            fi
                        done
                    fi
                    # Final fallback: bare mount (relies on kernel auto-probe)
                    if [ $RC -ne 0 ]; then
                        echo "[mount_helper] auto: brute-force failed — last bare mount attempt"
                        OUT=$(do_mount "$DEVICE" "$MOUNTPOINT" 2>&1)
                        RC=$?
                    fi
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
            MKDIR)
                mkdir -p "$ARG1" 2>/dev/null
                printf '0|\n' > "$RESULT"
                ;;
            PARTLIST)
                # Machine-readable partition layout incl. free-space gaps.
                # ARG1 = /dev/sdX (whole disk)
                DISK="$ARG1"
                OUT=$(parted -m -s "$DISK" unit MiB print free 2>&1)
                RC=$?
                # Newlines break our FIFO line protocol → encode as \\n
                ENC=$(printf '%s' "$OUT" | sed ':a;N;$!ba;s/\n/\\n/g')
                printf '%s|%s\n' "$RC" "$ENC" > "$RESULT"
                echo "[mount_helper] PARTLIST $DISK rc=$RC"
                ;;
            PARTMKLABEL)
                # Initialize new partition table. ARG1 = /dev/sdX, ARG2 = gpt|msdos
                DISK="$ARG1"; LBL="$ARG2"
                echo "[mount_helper] PARTMKLABEL $DISK $LBL"
                OUT=$(parted -s "$DISK" mklabel "$LBL" 2>&1)
                RC=$?
                partprobe "$DISK" 2>/dev/null
                printf '%s|%s\n' "$RC" "$OUT" > "$RESULT"
                ;;
            PARTADD)
                # Create partition. ARG1 = /dev/sdX, ARG2 = START (MiB),
                # ARG3 = END (MiB or "100%")
                DISK="$ARG1"; PSTART="$ARG2"; PEND="$ARG3"
                echo "[mount_helper] PARTADD $DISK ${PSTART}MiB → $PEND"
                OUT=$(parted -s -a optimal "$DISK" mkpart primary "${PSTART}MiB" "$PEND" 2>&1)
                RC=$?
                partprobe "$DISK" 2>/dev/null
                # parted itself sometimes returns 0 even when partprobe needs a moment
                sleep 1
                printf '%s|%s\n' "$RC" "$OUT" > "$RESULT"
                ;;
            PARTRM)
                # Remove partition. ARG1 = /dev/sdX, ARG2 = partition number
                DISK="$ARG1"; PNUM="$ARG2"
                echo "[mount_helper] PARTRM $DISK $PNUM"
                OUT=$(parted -s "$DISK" rm "$PNUM" 2>&1)
                RC=$?
                partprobe "$DISK" 2>/dev/null
                printf '%s|%s\n' "$RC" "$OUT" > "$RESULT"
                ;;
            MKFS)
                # Format partition. ARG1 = /dev/sdXN, ARG2 = fstype, ARG3 = label
                PART="$ARG1"; FS="$ARG2"; LABEL="$ARG3"
                echo "[mount_helper] MKFS $PART -t $FS -L '$LABEL'"
                if [ ! -b "$PART" ]; then
                    printf '1|%s ist kein Block-Device\n' "$PART" > "$RESULT"
                    continue
                fi
                # Refuse if currently mounted
                if grep -q "^$PART " /proc/mounts 2>/dev/null; then
                    printf '1|%s ist eingehängt — bitte zuerst aushängen\n' "$PART" > "$RESULT"
                    continue
                fi
                case "$FS" in
                    ext4)
                        OUT=$(mkfs.ext4 -F -L "$LABEL" "$PART" 2>&1) ;;
                    ext3)
                        OUT=$(mkfs.ext3 -F -L "$LABEL" "$PART" 2>&1) ;;
                    ext2)
                        OUT=$(mkfs.ext2 -F -L "$LABEL" "$PART" 2>&1) ;;
                    exfat)
                        OUT=$(mkfs.exfat -n "$LABEL" "$PART" 2>&1) ;;
                    vfat|fat32)
                        OUT=$(mkfs.vfat -F 32 -n "$LABEL" "$PART" 2>&1) ;;
                    ntfs)
                        OUT=$(mkfs.ntfs -f -L "$LABEL" "$PART" 2>&1) ;;
                    *)
                        OUT="Unsupported filesystem: $FS"
                        RC=1
                        printf '%s|%s\n' "$RC" "$OUT" > "$RESULT"
                        continue
                        ;;
                esac
                RC=$?
                # New filesystem signature isn't always picked up immediately;
                # ask kernel to re-read & udev to refresh symlinks
                partprobe "$(echo "$PART" | sed 's/[0-9]*$//')" 2>/dev/null
                printf '%s|%s\n' "$RC" "$OUT" > "$RESULT"
                echo "[mount_helper] MKFS rc=$RC"
                ;;
            PARTPROBE)
                partprobe "$ARG1" 2>/dev/null
                printf '0|\n' > "$RESULT"
                ;;
            *)
                printf '1|Unbekannter Befehl: %s\n' "$ACTION" > "$RESULT"
                ;;
        esac
    fi
done
