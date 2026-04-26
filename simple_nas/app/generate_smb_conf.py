#!/usr/bin/env python3
"""Generate /etc/samba/smb.conf from shares.json and system config."""
import json, os, sys

SHARES_FILE = "/data/shares.json"
USERS_FILE  = "/data/users.json"
GROUPS_FILE = "/data/groups.json"
SMB_CONF    = "/etc/samba/smb.conf"

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
   # Suppress macOS junk files on all shares
   veto files = /.DS_Store/._.DS_Store/._*/.TemporaryItems/.Trashes/.fseventsd/.Spotlight-V100/
   delete veto files = yes

"""

for share in shares:
    name        = share.get("name", "share")
    path        = share.get("path", "/data/shares")
    public      = share.get("public", False)
    writable    = share.get("writable", True)
    comment     = share.get("comment", "")
    valid_users = share.get("users", [])
    valid_groups = share.get("groups", [])

    os.makedirs(path, mode=0o2775, exist_ok=True)
    try:
        os.chmod(path, 0o2775)
    except Exception:
        pass

    conf += f"[{name}]\n"
    conf += f"   comment = {comment or name}\n"
    conf += f"   path = {path}\n"
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
    conf += "\n"

os.makedirs("/etc/samba", exist_ok=True)
with open(SMB_CONF, "w") as f:
    f.write(conf)

print(f"Generated {SMB_CONF} with {len(shares)} share(s).")
