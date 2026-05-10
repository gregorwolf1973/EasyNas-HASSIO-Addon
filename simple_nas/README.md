# Simple NAS – Home Assistant Add-on

A lightweight NAS add-on for Home Assistant OS. Turns your Raspberry Pi (or other HA hardware) into a full-featured network storage with web interface.

![Architectures](https://img.shields.io/badge/arch-aarch64%20|%20amd64%20|%20armv7-blue)
![Version](https://img.shields.io/badge/version-3.0.x-green)

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://buymeacoffee.com/gregorwolf1973)

## Features

- **Samba Server** (SMB/CIFS) for Windows, macOS & Linux
- **Web GUI** directly in Home Assistant (Ingress)
- **Drive management** – mount/unmount external USB drives, persistent across reboots via stable `/dev/disk/by-id/` paths
- **Share management** – create, edit & delete Samba shares
- **Users & Groups** – Samba users with passwords and group membership
- **Access control** – restrict shares to specific users or groups
- **Network discovery** – auto-discovery in Windows Explorer (WSDD), Linux Nautilus/Dolphin (Avahi/mDNS) and macOS Finder
- **Dark/Light theme** – switchable in the header
- **Protection** against accidental unmounting of system partitions
- **File manager** – browse, upload, download, copy, move, rename and delete files
- **Backup jobs** – configure source/destination, run manually, auto-clean old backups
- **Reinstall-safe backup** – settings auto-backed up to `/config/.simplenas/auto`, restored on fresh install
- **Persistent configuration** – shares, users, groups, mounts and passwords survive restarts
- **Admin password protection** – optional login screen for the web interface
- **Language toggle** – DE / EN switch in the UI (default: EN)

## Installation

### Method 1: GitHub Repository (recommended)

[![Add to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fgregorwolf1973%2FEasyNas-HASSIO-Addon)

Click the button → repository is automatically added → continue with step 4.

Or manually:
1. In Home Assistant: **Settings → Add-ons → Add-on Store**
2. Top right **⋮ → Repositories**
3. Enter URL: `https://github.com/gregorwolf1973/EasyNas-HASSIO-Addon`
4. **Simple NAS** appears in the store → **Install**
5. **Start** → enable **Show in sidebar** → **Open**

### Method 2: Local add-on

1. Copy the `simple_nas/` folder to `/addons/` via SSH or Samba:
   ```
   /addons/simple_nas/
   ├── config.yaml
   ├── build.yaml
   ├── Dockerfile
   ├── run.sh
   ├── smb.service
   └── app/
       ├── app.py
       ├── generate_smb_conf.py
       ├── restore_mounts.py
       ├── mount_helper.sh
       └── templates/
           ├── index.html
           └── login.html
   ```
2. **Settings → Add-ons → Add-on Store → ⋮ → Reload local add-ons**
3. **Simple NAS** under "Local add-ons" → **Install** → **Start**

## Quick Start

### 1. Mount a drive

1. Connect a USB drive
2. Open Simple NAS → **Drives** tab
3. Select a partition (e.g. `/dev/sdb1`) → **Mount**
4. Enter a name (e.g. `nas`) → becomes `/media/nas`, or enter a full path (e.g. `/mnt/nas`)

### 2. Create a user

1. **Users & Groups** tab → **+ New User**
2. Enter username and password → **Save**

### 3. Create a share

1. **Shares** tab → **+ New Share**
2. Enter name and path (e.g. `/mnt/nas`)
3. Set public or restrict to specific users/groups
4. **Save**

### 4. Network access

| System | Address |
|--------|---------|
| **Windows** | Type `\\192.168.x.x\sharename` in Explorer |
| **macOS** | Finder → Go → Connect to Server → `smb://192.168.x.x/sharename` |
| **Linux** | File manager → `smb://192.168.x.x/sharename` |
| **Nextcloud** | External storage → SMB/CIFS → Host + Share name + Credentials |

## Configuration

In the add-on configuration:

```yaml
workgroup: WORKGROUP          # Windows workgroup (default: WORKGROUP)
nas_name: SimpleNAS           # NetBIOS / mDNS hostname
log_level: info               # trace, debug, info, notice, warning, error, fatal
web_port: 8100                # Port for the web interface (default: 8100)
smb_port: 445                 # SMB port (use 4445 if running alongside official Samba add-on)
admin_password_enabled: false # Enable web UI password protection
admin_username: "admin"       # Admin login username
admin_password: ""            # Admin login password (stored encrypted)
web_gui_enabled: true         # Set to false to run Samba only, without web interface
```

### Admin Password Protection

When `admin_password_enabled` is set to `true`, the web interface requires a login.
- Set `admin_username` and `admin_password` freely
- The password is stored as a secure hash (pbkdf2:sha256) in `/data/admin_auth.json` — never in plaintext
- To disable protection again, set `admin_password_enabled` to `false` and restart the add-on
- Changing the password in the config and restarting the add-on immediately takes effect

### Running alongside the official Samba add-on

Both add-ons use `host_network: true` and bind to the same SMB ports. Set `smb_port` to a non-standard port (e.g. `4445`) and connect from clients with that port:

- Windows: `\\<HA-IP>:4445\ShareName`
- macOS: `smb://<HA-IP>:4445/ShareName`

## Web GUI

| Tab | Function |
|-----|----------|
| **Overview** | CPU, RAM, disk status, Samba service status |
| **Drives** | Detect drives, mount/unmount, system partition protection |
| **Shares** | Create Samba shares, assign users/groups, public/private |
| **Users & Groups** | Create Samba users, change passwords, manage groups |
| **Files** | File browser with upload, download, copy, move, rename, delete |
| **Backup** | Create backup jobs, run manually, auto-delete old backups |

### Dark/Light Theme

Switchable via the moon/sun button in the top right. The choice is stored in the browser.

## Share Options

| Option | Description |
|--------|-------------|
| **Name** | Network name of the share (e.g. `NAS`, `Documents`) |
| **Path** | Local path on the server (e.g. `/mnt/nas`) |
| **Write access** | Users can create and modify files |
| **Public** | Access without password (guest access) |
| **Users** | Individual Samba users with access (only when not public) |
| **Groups** | Samba groups with access (only when not public) |

> **Note:** When a share is public, **everyone** on the network has access — the user/group selection has no effect.

## Supported Filesystems

ext4, ext3, NTFS (ntfs-3g), FAT32 (vfat), exFAT, btrfs, XFS

## Network Discovery

The add-on automatically starts:

- **Avahi** (mDNS/DNS-SD) – NAS appears in Linux file managers (Nautilus, Dolphin) and macOS Finder
- **WSDD** (WS-Discovery) – NAS appears in Windows 10/11 Explorer under "Network"
- **nmbd** (NetBIOS) – classic Windows network neighbourhood

## Using with Nextcloud

1. In Simple NAS: create a share, assign a user (e.g. `admin`)
2. In Nextcloud: **Settings → Administration → External storage**
3. Configuration:
   - Folder name: anything (e.g. `/nas`)
   - External storage: **SMB/CIFS**
   - Authentication: **Username and password**
   - Host: `192.168.x.x` (IP of the Raspberry Pi)
   - Share: name of the share (exact, case-sensitive!)
   - Username/password: a Samba user from Simple NAS

> **Tip:** "Global credentials" often does not work reliably. Use "Username and password" directly instead.

## Persistent Data

All configuration data is stored in `/data/` and survives add-on updates and restarts:

| File | Contents |
|------|----------|
| `shares.json` | Configured shares |
| `users.json` | Created users |
| `groups.json` | Groups and memberships |
| `mounts.json` | Mount points (restored on startup) |
| `admin_auth.json` | Hashed admin credentials (if password protection is enabled) |
| `fstype_memory.json` | Last known filesystem type per device |

> All data including Samba passwords is automatically backed up and restored after a restart.

## Technical Details

- Based on the official Home Assistant Base Image (Alpine Linux)
- Samba 4.x with SMB2/SMB3 protocol
- Flask web GUI on port 8100 (Ingress)
- Mount operations via privileged FIFO daemon
- Supported architectures: aarch64 (Raspberry Pi 4/5), amd64, armv7

## Troubleshooting

**Drive not detected:**
- Check if the drive appears under **Drives** as a block device
- An unpowered USB hub can cause issues

**Mount fails:**
- Check the add-on log for error messages
- The filesystem might be unsupported or the partition is corrupted
- Try selecting a specific filesystem type instead of "Auto"

**Drive not remounted after reboot:**
- Upgrade to v3.0.49 or later. Older versions used `fstype: auto` when restoring mounts on startup, which fails on some USB devices.
- Since v3.0.52 boot mounts retry automatically when the USB device is not yet ready.

**Samba share unreachable:**
- Check if smbd/nmbd show as "active" in the overview
- Click "Restart Samba"
- Check your network firewall settings (port 445 TCP)

**Cannot log in to web interface:**
- Check that `admin_password_enabled` is `true` and both `admin_username` and `admin_password` are set
- Restart the add-on after changing credentials in the config
- If locked out: set `admin_password_enabled` to `false`, restart, then re-enable with new credentials

**Nextcloud cannot open the share:**
- Use "Username and password" instead of "Global credentials"
- Share name must match exactly (case-sensitive!)
- Reset the Samba password in the Simple NAS GUI

## Documentation

See [DOCS.md](DOCS.md) for the full add-on documentation, including:
- Detailed installation and configuration
- Drive mounting and stability
- Accessing mounted drives from other add-ons
- Automation: reconnect shares after HA restart
- Security rating explanation
- Reinstall-safe backup details
- Extended troubleshooting

## License

MIT License
