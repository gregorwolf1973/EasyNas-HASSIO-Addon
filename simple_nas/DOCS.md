# Simple NAS – Documentation

A full-featured NAS add-on for Home Assistant with Samba file sharing, a web-based management GUI, USB/external drive support, and reinstall-safe settings backup.

---

## Features

- **Samba file sharing** – SMB2/SMB3, compatible with Windows, macOS, Linux
- **Web GUI** – manage shares, users, groups, drives, and file browser via HA Ingress
- **USB / external drives** – mount and unmount drives at runtime; mounts survive reboots using stable `/dev/disk/by-id/` identifiers
- **Network discovery** – mDNS (Avahi) for macOS Finder / Linux Nautilus, WS-Discovery (wsdd) for Windows 10/11
- **Reinstall-safe backup** – settings are automatically backed up to `/config/.simplenas/auto` and restored on fresh install; manual snapshots (up to 10) are also supported
- **Password protection** – optional web GUI login (username + password via add-on config)
- **macOS junk suppression** – `.DS_Store`, `._*`, `.TemporaryItems` etc. are hidden and deleted automatically on all shares
- **Language** – German / English toggle in the UI

---

## Installation

1. Add this repository to Home Assistant: **Settings → Apps → Install app → ⋮ → Repositories**
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
| `web_gui_enabled` | bool | `true` | Set to `false` to run Samba only, without the web interface |
| `hdd_idle_seconds` | int | `0` | Spin down mounted drives after this many seconds of inactivity (0 = disabled). See "Drive spindown / power saving" below |

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
- Saved mounts are **automatically restored on every add-on start**, using the filesystem type that was detected at mount time (e.g. `ext4`) rather than relying on kernel auto-detection, which can fail on some USB devices.

> ⚠️ **Devices that back the HA system (`/`, `/boot`, …) are marked SYSTEM** in the GUI and require explicit confirmation before mounting/unmounting.

---

## Drive spindown / power saving

Set `hdd_idle_seconds` to a value greater than `0` (e.g. `1800` for 30 minutes) to have mounted drives spin down after a period of inactivity. This saves power and reduces noise and mechanical wear on spinning HDDs.

**How it works:**

- Uses `hd-idle`, which monitors actual disk I/O via `/proc/diskstats` and issues the standby command when a drive has been idle long enough. This is the robust approach for **USB drives**, where the drive's built-in `hdparm -S` standby timer often does not work because USB-SATA bridges do not pass the ATA standby command through.
- **Only the drives you mounted in Simple NAS are affected.** hd-idle is started with a global default of `-i 0` (never spin down) and is then explicitly pointed only at the base disks of the devices listed in `mounts.json`. **The HA system disk is never spun down**, because it is never part of your mounts.

**Important caveats:**

- Spindown only happens when **nothing** touches the drive. On a Home Assistant system many processes may poll the disk periodically:
  - An active Samba client keeping a connection open
  - SMART monitoring / status checks
  - Backup jobs
  - HA media source scanning
- If any of these access the drive, the idle timer resets and the drive stays awake. If your drive never spins down, check what is accessing it.
- The **first access after spindown is slow** (a few seconds) while the drive spins back up. This is normal.
- SSDs do not benefit from this and can safely ignore the setting.

---

## Accessing mounted drives from other add-ons

Due to how HA OS isolates add-on containers (slave mount namespaces), it is **not possible** to make a drive mounted by Simple NAS directly visible to other add-ons or HA Core via a filesystem mount. Any mount performed inside one container stays within its own mount namespace.

**The correct approach is Samba.** Other add-ons (Nextcloud, Jellyfin, Immich, …) can connect to a Simple NAS share via SMB:

```
smb://localhost:445/ShareName
```

Most add-ons with media or storage support have an SMB/CIFS mount option in their settings.

---

## ⚠️ Important warning for upgraders from versions 3.0.38–3.0.47

Versions 3.0.38 through 3.0.47 included an experimental "bind-mount" feature that exposed mounted drives under `/share/<name>` for cross-add-on access. **This feature was removed in v3.0.48** because:

1. It was **ineffective** — HA OS uses slave mount namespaces (`master:118`), so the bind never actually propagated to HA Core or other add-on containers.
2. It was **dangerous** — bind mounts look like normal folders. Running `rm -rf /share/<name>` (e.g. to clean up what looks like an empty leftover folder) **recurses through the bind mount and deletes everything on the underlying drive**.

**If you have ever used Simple NAS between versions 3.0.38 and 3.0.47**, please observe the following:

- **NEVER run `rm -rf /share/<name>`** on a folder name that matches one of your previously mounted drives (e.g. `/share/nas`) without first verifying it is NOT a bind mount. Check with:
  ```
  mount | grep '/share/<name>'
  ```
  If anything appears in the output → it is still a bind mount, do NOT delete it. Reboot the add-on first to clear the bind.

- After upgrading to v3.0.48 or later **and restarting the add-on at least once**, any leftover bind mount is gone. The empty `/share/<name>` directory can then be removed safely with `rmdir` (NOT `rm -rf`) — `rmdir` refuses to delete a non-empty directory and will safely error out if a bind is still active.

Since v3.0.56 the migration step in `run.sh` automatically cleans any obsolete bind-mount entries from `/data/mounts.json` on startup, so the feature can never re-activate itself after an upgrade.

---

## Automation: reconnect shares after HA restart

When Home Assistant restarts, HA Core may try to access a network path (backup location, media source) before Simple NAS has finished starting up. The automation below waits for Simple NAS to be ready and then triggers a Samba reload to ensure all shares are available.

**Step 1 – Add a REST command** to `configuration.yaml`:

```yaml
rest_command:
  simplenas_reload:
    url: "http://localhost:8100/api/samba/restart"
    method: POST
    headers:
      Content-Type: "application/json"
```

**Step 2 – Add the automation** (UI editor or `automations.yaml`):

```yaml
alias: "NAS: Reconnect shares after restart"
description: >
  Waits for Simple NAS to be ready after a reboot and reloads Samba
  shares. Skips if the add-on is not running.
trigger:
  - platform: homeassistant
    event: start
  - platform: state
    entity_id: binary_sensor.simple_nas_running
    to: "on"
condition: []
action:
  - alias: "Wait until Simple NAS is running (max. 5 minutes)"
    wait_template: "{{ is_state('binary_sensor.simple_nas_running', 'on') }}"
    timeout: "00:05:00"
    continue_on_timeout: false

  - alias: "Give Samba time to fully start"
    delay: "00:00:45"

  - alias: "Only proceed if Simple NAS is still running"
    condition: state
    entity_id: binary_sensor.simple_nas_running
    state: "on"

  - alias: "Reload Samba shares"
    service: rest_command.simplenas_reload
mode: single
max_exceeded: silent
```

> **Note:** The entity ID `binary_sensor.simple_nas_running` may differ slightly on your system. Check under **Settings → Devices & Services → Supervisor → Simple NAS → Entities** to find the correct name.

---

## Sharing files on the internet (web sharing)

Since 3.4.0 the add-on can publish folders and files through links. The share site is a **separate web application on its own port** (default 8101). Nothing from the admin interface exists on that port: a bug on the public site cannot reach the file manager, the Samba settings or the drive tools.

### Prerequisites

1. `admin_password_enabled: true` with a non-empty `admin_password`. The public site refuses to start without it, on purpose.
2. `sharing_enabled: true`.
3. A reverse proxy with TLS in front of port 8101. The add-on never terminates TLS itself. Nginx Proxy Manager (available as an add-on) is the tested path.
4. `share_public_url` set to the address people will use, e.g. `https://files.example.com`. It is only used to build the links shown in the Sharing tab.

### Nginx Proxy Manager

Create a proxy host for your domain pointing at the HA host on port 8101 (scheme `http`), request a Let's Encrypt certificate and enable *Force SSL*. In the **Advanced** tab add:

```
client_max_body_size 0;
proxy_request_buffering off;
proxy_buffering off;
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
```

Without `client_max_body_size` every upload fails at nginx's 1 MB default; without `proxy_buffering off` nginx spools a whole folder download to disk before the browser sees the first byte.

**Forward host:** use the LAN IP of your Home Assistant host (e.g. `192.168.1.10`), scheme **http**, port `share_port`. The add-on does not speak TLS itself; a proxy host set to `https` hangs and ends in a 502/504.

**About `share_bind`:** keep `0.0.0.0` when Nginx Proxy Manager runs as a Home Assistant add-on. That add-on lives in its own container network and cannot reach the host's loopback, so a site bound to `127.0.0.1` is unreachable for it. Use `127.0.0.1` only for a reverse proxy running directly on the host (or an add-on with `host_network`). Because this add-on uses `host_network`, the `ports:` entry in the add-on UI is informational - the port is open on the LAN as soon as the site listens.

**Do not reuse a port that another add-on maps** (the start log then says "Port ... ist bereits belegt"). Each add-on needs its own port.

### How links work

- A link is a 22-character token that cannot be guessed. Every failed password on a link counts against the link (20 per 15 minutes) *and* against the IP (10 per 15 minutes); a link locked this way shows "Locked" in the Sharing tab with a one-click unlock.
- An unknown, disabled, expired or exhausted link, and a link whose folder is no longer mounted, all show the *same* page. Nobody can tell whether a token exists.
- Changing a link's password, access mode or account list ends every session on that link immediately. "Generate new link" replaces the token.
- Share accounts are separate from the Samba users. Their password hashes live in `/data/share_accounts.json` (mode 600) and, like all settings, in the reinstall-safe copy under `/config/.simplenas/auto` - which means they are part of your Home Assistant backups.
- HTML and SVG files are never shown inline on the share site, only offered as downloads. A visitor-uploaded page served from your own domain would otherwise run scripts against every later visitor.

### Brute-force protection (with or without Cloudflare)

- Tokens are 22 characters from a 57-symbol alphabet (about 127 bits). Guessing one is not a realistic attack; scanning for them gets the address banned after 20 unknown links in 10 minutes.
- Failed passwords count per address (10 per 15 min), per link (20, regardless of how many addresses take part) and per account (10). Every lockout doubles on repetition: 15 min, 30, 60 ... up to 24 h.
- Each failure also sleeps 0.15-0.35 s, so even the allowed attempts are slow.
- Link passwords and account passwords need at least 8 characters; use longer ones for anything that matters.
- The real visitor address is taken from `X-Forwarded-For` (Nginx Proxy Manager) or `CF-Connecting-IP` (Cloudflare), but only when the request comes from an address in `share_trusted_proxies`. Without a proxy the socket address is used. Either way the limits apply to the visitor, not to the proxy.
- Lockouts live in memory; a restart of the add-on clears them. Persistent bans belong in a firewall - see the CrowdSec section below.

### CrowdSec integration

The add-on ships a parser, three scenarios and an acquisition file for CrowdSec and can install them into the CrowdSec add-on's configuration with one click (both add-ons see `/config`).

1. Set `share_log_export_path` to `/share/simplenas/share_access.log` and restart Simple NAS. The access log is now also written there, where the CrowdSec add-on can read it.
2. In the Sharing tab click **Set up CrowdSec**. This copies `parsers/s01-parse/simplenas-share.yaml`, `scenarios/simplenas-share.yaml` and `acquis.d/simplenas-share.yaml` into `/config/.storage/crowdsec/config/`.
3. Restart the CrowdSec add-on. `cscli metrics` then shows the `simplenas-share` source and `cscli scenarios list` the three scenarios: `simplenas/share-bf` (5 failed passwords in ~50 s), `simplenas/share-scan` (10 unknown links in ~5 min) and `simplenas/share-locked` (the add-on locked the address itself).

Bans then reach whatever bouncer you run. Two things to know:

- With the **firewall bouncer** on the Home Assistant host, bans only bite for traffic that reaches the host directly or through Nginx Proxy Manager. Traffic through a **Cloudflare tunnel** arrives from the tunnel container, so the offender's address is never seen by the host firewall. For that path use CrowdSec's Cloudflare bouncer (it pushes decisions into Cloudflare's firewall) or Cloudflare's own WAF rules.
- The log records the visitor address as the add-on sees it (`X-Forwarded-For` / `CF-Connecting-IP` from trusted proxies). That is the address CrowdSec bans.

### Virus scanning

Enable `share_clamav_enabled` and make sure the ClamAV add-on exposes clamd on TCP 3310 (`TCPSocket`/`TCPAddr` in its clamd configuration). The Sharing tab's **Test** button sends `PING` and then the EICAR test string through the scanner and reports both. With `share_clamav_on_error: reject` (default) uploads are refused while the scanner is down - silently accepting would defeat the point of enabling it.

### Access log

Every view, login, failed login, download and lock-out is written to `/data/share_access.log` (JSON lines, rotated at `share_log_max_mb`). The Sharing tab shows the last entries with filters. The log records link ids and paths relative to the link, never tokens or absolute paths.

### Known limitation

This add-on runs as root with `full_access` because of its drive-management features. A code-execution flaw in the public site would therefore be a compromise of the whole host. The share site is small, has no upload path yet, sets strict headers and rate limits, but the honest mitigation is the reverse proxy being the only way in and `share_bind: 127.0.0.1` wherever possible. Running the share site as a separate unprivileged process is on the roadmap.

## Reinstall-safe backup

Every time you save a setting (share, user, group, mount) the add-on writes a backup to `/config/.simplenas/auto/`. This directory survives an add-on uninstall/reinstall because it lives in the persistent `/config` volume.

On the next start after a fresh install the backup is automatically detected and restored.

You can also create **manual snapshots** from the Backup tab (up to 10 kept). Each snapshot can be individually restored or deleted.

---

## ⚠️ Important: disable Protection Mode

This add-on needs `CAP_SYS_ADMIN` to run `mount` / `umount`. Home Assistant only grants the add-on's elevated permissions (`full_access`, `privileged`, `apparmor: false`) when **Protection Mode is turned OFF**.

**If Protection Mode is still ON, every mount fails** with:

```
mount: permission denied (are you root?)
... Operation not permitted
```

(and NTFS additionally fails to create `/dev/fuse` because `/dev` is read-only).

**Fix:**

1. Open the **Simple NAS** add-on page in Home Assistant
2. Go to the **Info** tab
3. Turn **OFF** the **Protection mode** toggle
4. **Restart** the add-on

You can confirm it worked in the add-on log: at startup the helper prints its capabilities, and `CapEff` should be a non-zero value (an all-zero `CapEff` means no capabilities = Protection Mode still on).

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

**Mount fails with "permission denied (are you root?)" / "Operation not permitted"**  
Protection Mode is still enabled. Turn it OFF in the add-on Info tab and restart — see the "Important: disable Protection Mode" section above. This is the #1 cause of mount failures on a fresh install.

**Drive not remounted after reboot**  
Upgrade to v3.0.49 or later. Older versions used `fstype: auto` when restoring mounts on startup, which fails on some USB devices. The current version saves and reuses the detected filesystem type (e.g. `ext4`) so the drive mounts reliably on every start.

**Share shows "path not found"**  
The drive is not mounted. Go to the Drives tab and mount it first.

**Share disappears after reboot**  
The drive was saved using a raw `/dev/sdX` path with an older version. Delete the mount entry and re-mount the drive — Simple NAS will now save the stable by-id path automatically.

**Can't see the NAS in Windows Network Browser**  
Make sure `wsdd` is running (check add-on logs). Windows 10/11 uses WS-Discovery instead of NetBIOS.

**SSL certificate folder is read-only**  
Upgrade to v3.0.32 or later — the `/ssl` mapping was changed from `ro` to `rw`.

**Conflict with official Samba add-on**  
Set `smb_port` to a value other than `445` in Simple NAS configuration. See section above.

**Samba share unavailable right after HA restart**  
This is a start-order race condition: HA Core checks the network path before Simple NAS has finished loading. Use the automation described above to automatically reload shares once the add-on is ready.
