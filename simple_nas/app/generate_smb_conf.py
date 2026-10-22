#!/usr/bin/env python3
"""Generate /etc/samba/smb.conf from shares.json and system config."""
import errno, json, os, re, sys


def get_mounted_points():
    """Return set of all mount points listed in /proc/mounts."""
    mounted = set()
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mounted.add(parts[1])
    except Exception:
        pass
    return mounted


def _path_under_mount(path, mounted_points):
    """Return the closest ancestor of `path` that is an actual mount point,
    or None if no ancestor is mounted (excluding root '/')."""
    check = os.path.realpath(path)
    while check not in ("/", ""):
        if check in mounted_points:
            return check
        parent = os.path.dirname(check)
        if parent == check:
            break
        check = parent
    return None


def share_available(path, mounted_points):
    """Return True if the share path is currently accessible.

    Paths under /media/ or /mnt/ require an actual mount so that an
    unmounted / missing USB device never silently maps to an empty host
    directory.

    If the share path itself does not exist yet but its parent IS an actual
    mount point (e.g. share = /mnt/nas/backup and /mnt/nas is the mounted
    drive), auto-create the subdirectory so the share works on first start.
    This handles the case where the share folder was originally created on
    the container overlay before the drive was mounted, then got hidden by
    the live filesystem after the mount.
    """
    needs_real_mount = path.startswith("/media/") or path.startswith("/mnt/")

    if needs_real_mount:
        ancestor_mount = _path_under_mount(path, mounted_points)
        if ancestor_mount is None:
            return False  # nothing mounted under this path
        # Path is below a real mount point — auto-create missing subdirs
        if not os.path.exists(path):
            try:
                os.makedirs(path, mode=0o2775, exist_ok=True)
                print(f"share_available: auto-created missing share dir {path}")
            except OSError as e:
                print(f"share_available: cannot create {path}: {e}")
                return False
        return True

    if not os.path.exists(path):
        return False
    return True  # HA-mapped or other always-available path

DATA_DIR    = os.environ.get("SIMPLE_NAS_DATA", "/data")              # Tests: Temp-Ordner
SHARES_FILE = f"{DATA_DIR}/shares.json"
USERS_FILE  = f"{DATA_DIR}/users.json"
GROUPS_FILE = f"{DATA_DIR}/groups.json"
RECYCLE_FILE = f"{DATA_DIR}/recycle.json"
SMB_CONF    = os.environ.get("SIMPLE_NAS_SMB_CONF", "/etc/samba/smb.conf")

# Network recycle bin (Settings tab): what a client deletes over SMB is moved
# to <share>/.recycle instead; app.py empties it after the configured days.
# Within one share only - a drive mounted below the share root cannot rename
# into the bin, and vfs_recycle then deletes for real.
RECYCLE_OPTIONS = """   recycle:repository = .recycle
   recycle:keeptree = yes
   recycle:versions = yes
   recycle:directory_mode = 0777
   recycle:exclude = *.tmp *.temp ~$* .~lock.*# *.part *.crdownload
   recycle:exclude_dir = .recycle
"""


MAC_MODULES = "catia fruit streams_xattr"
MAC_VETO = "/.DS_Store/.TemporaryItems/.Trashes/.fseventsd/.Spotlight-V100/"
# Named streams of shares on exFAT/FAT (issue #15). Inside the container /tmp starts empty with
# every add-on start - see the comment where it is used.
NO_XATTR_STREAMS = os.environ.get("SIMPLE_NAS_STREAMS_DIR", "/tmp/simple-nas-streams")


def streams_dir(share_name):
    return os.path.join(NO_XATTR_STREAMS, re.sub(r"[^A-Za-z0-9_.-]", "_", share_name) or "share")


def supports_xattr(path):
    """Can the drive behind `path` store extended attributes? exFAT and FAT cannot (issue #15).

    streams_xattr keeps the Finder metadata and resource forks of macOS in them. Without that
    support every stream write fails, and since ._* AppleDouble files are vetoed as well, macOS
    has nowhere to put the metadata and aborts the copy with error 100093.
    """
    test_paths = os.environ.get("SIMPLE_NAS_NO_XATTR_PATHS")  # Tests: tmpfs/ext4 can always
    if test_paths is not None:
        return os.path.realpath(path) not in {os.path.realpath(p) for p in test_paths.split(":") if p}
    probe = "user.simplenas_probe"
    try:
        os.setxattr(path, probe, b"1")
        os.removexattr(path, probe)
    except OSError as e:
        return e.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP)  # e.g. read-only: nothing to write
    return True


def recycle_enabled():
    try:
        with open(RECYCLE_FILE) as f:
            saved = json.load(f)
    except Exception:
        return True   # on unless switched off in the Settings tab
    return not isinstance(saved, dict) or bool(saved.get("enabled", True))

workgroup = sys.argv[1] if len(sys.argv) > 1 else "WORKGROUP"
nas_name  = sys.argv[2] if len(sys.argv) > 2 else "SimpleNAS"
smb_port  = int(sys.argv[3]) if len(sys.argv) > 3 else 445

shares, users, groups = [], [], []
try:
    with open(SHARES_FILE) as f: shares = json.load(f)
except Exception: pass
try:
    with open(USERS_FILE) as f: users = json.load(f)
except Exception: pass
try:
    with open(GROUPS_FILE) as f: groups = json.load(f)
except Exception: pass

conf = f"""[global]
   workgroup = {workgroup}
   netbios name = {nas_name}
   server string = {nas_name}
   server role = standalone server
   log file = /var/log/samba/%m.log
   max log size = 1000
   server signing = auto
   server min protocol = SMB2
   server max protocol = SMB3
   obey pam restrictions = no
   unix password sync = no
   passwd program = /usr/bin/passwd %u
   pam password change = no
   map to guest = Bad User
   guest account = nobody
   usershare allow guests = yes
   dns proxy = no
   ntlm auth = yes
   load printers = no
   printing = bsd
   printcap name = /dev/null
   disable spoolss = yes
   log level = 2
   # Network browser visibility
   local master = yes
   preferred master = yes
   os level = 65
   wins support = yes
   # Custom SMB port (default 445; change if official Samba add-on is also running)
   smb ports = {smb_port}
   # macOS Finder / iOS Files app compatibility (resource forks, metadata,
   # correct filename display). Harmless for Windows/Linux clients. (Issue #7)
   vfs objects = {MAC_MODULES}
   fruit:metadata = stream
   fruit:model = MacSamba
   fruit:posix_rename = yes
   fruit:veto_appledouble = no
   fruit:nfs_aces = no
   fruit:wipe_intentionally_left_blank_rfork = yes
   fruit:delete_empty_adfiles = yes
   # Suppress macOS junk files on all shares
   veto files = /._*{MAC_VETO}
   delete veto files = yes

"""

mounted_points = get_mounted_points()
inactive_count = 0
use_recycle = recycle_enabled()

for share in shares:
    name         = share.get("name", "share")
    path         = share.get("path", "/data/shares")
    public       = share.get("public", False)
    writable     = share.get("writable", True)
    comment      = share.get("comment", "")
    valid_users  = share.get("users", [])
    valid_groups = share.get("groups", [])
    timemachine  = bool(share.get("timemachine")) and writable

    available = share_available(path, mounted_points)

    if available:
        os.makedirs(path, mode=0o2775, exist_ok=True)
        try:
            os.chmod(path, 0o2775)
        except Exception:
            pass

    conf += f"[{name}]\n"
    conf += f"   comment = {comment or name}\n"
    conf += f"   path = {path}\n"

    if not available:
        # Path not mounted / does not exist — disable share so Samba
        # does NOT expose an empty directory or the wrong device.
        conf += f"   available = no\n"
        conf += f"   # WARNING: '{path}' is not mounted or does not exist\n"
        inactive_count += 1
        conf += "\n"
        continue

    conf += f"   browseable = yes\n"
    conf += f"   read only = {'no' if writable else 'yes'}\n"
    conf += f"   guest ok = {'yes' if public else 'no'}\n"

    # Build valid users list: individual users + @group notation
    # IMPORTANT: don't set valid users when share is public — it overrides guest ok!
    if not public:
        vu_parts = list(valid_users)
        for gn in valid_groups:
            vu_parts.append(f"@{gn}")
        if vu_parts:
            conf += f"   valid users = {' '.join(vu_parts)}\n"

    conf += f"   create mask = 0666\n"
    conf += f"   directory mask = 0777\n"
    conf += f"   force create mode = 0666\n"
    conf += f"   force directory mode = 0777\n"
    conf += f"   force user = root\n"
    conf += f"   force group = root\n"
    if timemachine:
        # macOS Time Machine target (issue #11). The global fruit/streams_xattr modules do the work;
        # the drive must support extended attributes (ext4, NTFS - not exFAT/FAT).
        # No recycle bin here: Time Machine prunes old backup bands all the time, and every one of
        # them would end up in .recycle and fill the drive.
        conf += "   fruit:time machine = yes\n"
        max_gb = share.get("timemachine_max_gb") or 0
        if isinstance(max_gb, int) and max_gb > 0:
            conf += f"   fruit:time machine max size = {max_gb}G\n"
    else:
        modules = MAC_MODULES
        if not supports_xattr(path):
            # exFAT/FAT (issue #15) keep no extended attributes, so streams_xattr cannot work.
            # Without named streams the iOS Files app opens every file read-only, and macOS gives
            # up with error 100093. streams_depot keeps the streams as files instead. Its folders
            # are named after the inode number, which exFAT/FAT hand out anew whenever a file is
            # loaded (iunique) - a lasting depot would show old metadata on the wrong file after
            # a reboot. So the depot lives in /tmp, which starts empty with every add-on start:
            # Finder metadata does not survive a restart there, but never lands on another file.
            # Resource forks go to ._* files next to the file (fruit:resource = file, the
            # default), which exFAT keeps by name - so ._* must not be vetoed here.
            modules = "catia fruit streams_depot"
            depot = streams_dir(name)
            os.makedirs(depot, exist_ok=True)
            conf += "   ea support = no\n"
            conf += f"   streams_depot:directory = {depot}\n"
            # the validity check needs an extended attribute on every file
            conf += "   streams_depot:check_valid = no\n"
            conf += "   streams_depot:delete_lost = yes\n"
            conf += f"   veto files = {MAC_VETO}\n"
        if use_recycle and writable:
            modules += " recycle"
            conf += RECYCLE_OPTIONS
        if modules != MAC_MODULES:
            # replaces the global vfs objects for this share
            conf += f"   vfs objects = {modules}\n"
    conf += "\n"

os.makedirs(os.path.dirname(SMB_CONF), exist_ok=True)
with open(SMB_CONF, "w") as f:
    f.write(conf)

active = len(shares) - inactive_count
print(f"Generated {SMB_CONF} with {len(shares)} share(s) "
      f"({active} active, {inactive_count} inactive/not mounted).")
