# Simple NAS — a Home Assistant Add-on

Turns your Home Assistant machine into network storage: Samba shares for
Windows, macOS and Linux, plus a web interface for drives, users and files —
all inside Home Assistant, no second box needed.

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://buymeacoffee.com/gregorwolf1973)

## What it does

- **Samba server** (SMB/CIFS) reachable from Windows, macOS and Linux
- **Web interface** inside Home Assistant via Ingress — no extra port, no
  separate login
- **Drives** — mount and unmount external USB disks; mounts survive a reboot
  because they are pinned to stable `/dev/disk/by-id/` paths
- **Shares, users and groups** with per-share access rules
- **File manager** — browse, upload, download, copy, move, rename, delete
- **Backup jobs** with automatic clean-up of old runs
- **Network discovery** — the NAS shows up in Windows Explorer (WSDD), in
  Nautilus and Dolphin (Avahi/mDNS) and in the macOS Finder
- **Settings survive a reinstall** — they are backed up to
  `/config/.simplenas/auto` and restored automatically
- German and English interface, dark and light theme

Filesystems: ext4, ext3, NTFS, FAT32, exFAT, btrfs, XFS.
Architectures: `aarch64` (Raspberry Pi 4/5), `amd64`, `armv7`.

## Installation

[![Add to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fgregorwolf1973%2FEasyNas-HASSIO-Addon)

Click the button → the repository is added to Home Assistant → install
**Simple NAS** from the store → Start.

Or by hand:

1. In Home Assistant: **Settings → Apps** → **Install app**
2. Top right **⋮ → Repositories**
3. Paste this URL and select **Add**:
   ```
   https://github.com/gregorwolf1973/EasyNas-HASSIO-Addon
   ```
4. **Simple NAS** appears in the store → Install → Start

## Documentation

- [simple_nas/README.md](./simple_nas/README.md) — full feature list,
  configuration and quick start
- [simple_nas/DOCS.md](./simple_nas/DOCS.md) — the in-depth documentation
- [simple_nas/CHANGELOG.md](./simple_nas/CHANGELOG.md) — what changed when

## Add-ons in this repository

| Add-on | What it is |
|---|---|
| [Simple NAS](./simple_nas) | Samba shares and a web interface for drives, users and files |

## License

MIT — see [LICENSE](./LICENSE).
