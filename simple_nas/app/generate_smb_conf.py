#!/usr/bin/env python3
"""Generate /etc/samba/smb.conf from shares.json and system config."""
import json, os, sys

SHARES_FILE = "/data/shares.json"
USERS_FILE  = "/data/users.json"
GROUPS_FILE = "/data/groups.json"
SMB_CONF    = "/etc/samba/smb.conf"

workgroup = sys.argv[1] if len(sys.argv) > 1 else "WORKGROUP"

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
   server string = Simple NAS
   server role = standalone server
   log file = /var/log/samba/%m.log
   max log size = 1000
   server signing = auto
   obey pam restrictions = no
   unix password sync = no
   passwd program = /usr/bin/passwd %u
   pam password change = no
   map to guest = Bad User
   usershare allow guests = yes
   dns proxy = no
   ntlm auth = ntlmv2-only
   load printers = no
   printing = bsd
   printcap name = /dev/null
   disable spoolss = yes

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
    vu_parts = list(valid_users)
    for gn in valid_groups:
        vu_parts.append(f"@{gn}")
    if vu_parts:
        conf += f"   valid users = {' '.join(vu_parts)}\n"

    conf += f"   create mask = 0664\n"
    conf += f"   directory mask = 0775\n"
    conf += f"   force create mode = 0664\n"
    conf += f"   force directory mode = 0775\n"
    conf += "\n"

os.makedirs("/etc/samba", exist_ok=True)
with open(SMB_CONF, "w") as f:
    f.write(conf)

print(f"Generated {SMB_CONF} with {len(shares)} share(s).")
