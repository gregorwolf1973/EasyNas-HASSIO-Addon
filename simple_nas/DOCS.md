# Simple NAS – Documentation

A full-featured NAS add-on for Home Assistant with Samba file sharing, a web-based management GUI, USB/external drive support, and reinstall-safe settings backup.

---

## Features

- **Samba file sharing** – SMB2/SMB3, compatible with Windows, macOS, Linux
- **Web GUI** – manage shares, users, groups, drives, and file browser via HA Ingress
- **USB / external drives** – mount and unmount drives at runtime; mounts survive reboots using stable `/dev/disk/by-id/` identifiers
- **Network discovery** – mDNS (Avahi) for macOS Finder / Linux Nautilus, WS-Discovery (wsdd) for Windows 10/11
- **HA Core / cross-add-on access** – mounted drives are automatically bind-mounted to `/share/<name>` so Home Assistant Core and other add-ons can read and write the same data (can be disabled per mount via the mount dialog)
- **Reinstall-safe backup** – settings are automatically backed up to `/config/.simplenas/auto` and restored on fresh install; manual snapshots (up to 10) are also supported
- **Password protection** – optional web GUI login (username + password via add-on config)
- **macOS junk suppression** – `.DS_Store`, `._*`, `.TemporaryItems` etc. are hidden and deleted automatically on all shares
- **Language** – German / English toggle in the UI

---

## Installation

1. Add this repository to Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
   ```
   https://github.com/gregorwolf1973/EasyNas-HASSIO-Addon
   ```
2. Install **Simple NAS**.
3. Configure the add-on options (see below).
4. Start the add-on and open the web UI via **Ingress** or directly on the configured port.

---

## Configuration

| Option | Type | Default | Description |
|---|---|---|---|
| `workgroup` | string | `WORKGROUP` | Samba workgroup name |
| `nas_name` | string | `SimpleNAS` | NetBIOS / mDNS hostname shown in network browsers |
| `web_port` | port | `8100` | Port for the web GUI |
| `smb_port` | port | `445` | SMB port. Change to e.g. `4445` if the official Samba add-on is also running |
| `log_level` | list | `info` | Log level: `trace` / `debug` / `info` / `notice` / `warning` / `error` / `fatal` |
| `admin_password_enabled` | bool | `false` | Enable web GUI password protection |
| `admin_username` | string | `admin` | Web GUI username |
| `admin_password` | string | _(empty)_ | Web GUI password |

---

## Running alongside the official Samba add-on

Both add-ons use `host_network: true` and bind to the same SMB ports (445 and 139), so they **cannot run simultaneously** with default settings.

If you need both, set `smb_port` in Simple NAS to a non-standard port (e.g. `4445`) and connect from clients using:

- Windows: `\\<HA-IP>:4445\ShareName` (or map network drive with full UNC path)
- macOS: `smb://<HA-IP>:4445/ShareName`

---

## Drive mounting and stability

Simple NAS automatically resolves `/dev/sdX` device names to their stable `/dev/disk/by-id/…` equivalents before saving a mount. This means:

- A USB drive mounted as `/dev/sdb1` today will still be found correctly after a reboot even if the kernel assigns it a different `/dev/sd*` name.
- If a drive is **not connected at boot**, its Samba share is automatically set to `available = no` — it will not be visible to clients and will not accidentally point to another device or an empty directory.

> ⚠️ **Devices that back the HA system (`/`, `/boot`, …) are marked SYSTEM** in the GUI and require explicit confirmation before mounting/unmounting.

---

## Reinstall-safe backup

Every time you save a setting (share, user, group, mount) the add-on writes a backup to `/config/.simplenas/auto/`. This directory survives an add-on uninstall/reinstall because it lives in the persistent `/config` volume.

On the next start after a fresh install the backup is automatically detected and restored.

You can also create **manual snapshots** from the Backup tab (up to 10 kept). Each snapshot can be individually restored or deleted.

---

## Security rating explanation

Home Assistant assigns this add-on a **security score of 1 (low)** because of the following required capabilities:

| Capability | Reason |
|---|---|
| `full_access: true` | Required to access all mapped volumes (`/share`, `/media`, `/config`, `/ssl`, `/addon_configs`) |
| `host_network: true` | Required for Samba to bind to host ports 445/139 and for mDNS/WS-Discovery to work correctly |
| `SYS_ADMIN` privilege | Required to run `mount` and `umount` inside the container |
| `SYS_RAWIO` privilege | Required for low-level block device access (formatting, fsck) |
| `DAC_READ_SEARCH` privilege | Required to read files owned by other users/processes |
| `apparmor: false` | AppArmor would block `mount` system calls needed for USB drive support |

**How to mitigate the risk:**

- Enable web GUI password protection (`admin_password_enabled: true`) so the management interface is not open to anyone on your network.
- Only expose Samba shares on your local LAN — do **not** forward ports 445 / 139 to the internet.
- Use per-share user authentication instead of public/guest shares wherever possible.
- Keep the add-on updated.

---

## Troubleshooting

**Share shows "Pfad nicht gefunden" / path not found**  
The drive is not mounted. Go to the Drives tab and mount it first.

**Share disappears after reboot**  
The drive was saved using a raw `/dev/sdX` path with an older version. Delete the mount entry and re-mount the drive — Simple NAS will now save the stable by-id path automatically.

**Can't see the NAS in Windows Network Browser**  
Make sure `wsdd` is running (check add-on logs). Windows 10/11 uses WS-Discovery instead of NetBIOS.

**SSL certificate folder is read-only**  
Upgrade to v3.0.32 or later — the `/ssl` mapping was changed from `ro` to `rw`.

**Conflict with official Samba add-on**  
Set `smb_port` to a value other than `445` in Simple NAS configuration. See section above.
