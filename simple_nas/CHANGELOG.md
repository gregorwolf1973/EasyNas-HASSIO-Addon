# Changelog

## 3.0.60
- New: sortable column header in Files tab — click **Name**, **Größe** or **Datum** to sort, click again to reverse direction (▲ / ▼ indicator)
- Sort key and direction persist in `localStorage` (keys `nas-files-sort-key`, `nas-files-sort-dir`) — defaults to name ascending
- Folders and files are sorted within their own group (folders always shown first)
- Date format unified to `dd/mm/yy HH:MM` (no longer locale-dependent) and labeled accordingly in the column header

## 3.0.59
- Files tab: size bars, file sizes, dates and action buttons now align in fixed columns across all rows
- `.factions` column has fixed width (170 px) with right-aligned buttons — delete (rightmost) lines up across rows regardless of how many actions a row has
- Each action button has a fixed 24×24 px slot so icons stay on the same grid

## 3.0.58
- Fix: size bars rendered all gray — colored fill was invisible because the inner `<span>` is inline by default and ignored `width`/`height`
- `.fbar-fill` now `display:block`, `.fbar` now `display:inline-block` — colored fill renders correctly in both the Files list and the Settings legend
- Selector prefix `.file-row` dropped from `.fbar` rules so the Settings color legend also displays bars

## 3.0.57
- New: dedicated **Settings** tab in the navigation — moved the "Show size bars" toggle out of the Files toolbar into a proper Settings section
- Settings tab uses a real on/off toggle switch (not a button) for the size-bars option
- Color legend is shown next to the toggle so the meaning of each bar color is obvious without hovering files
- Files tab toolbar is back to upload / mkdir only — no clutter

## 3.0.56
- New: optional "Size bars" view in the Files tab — colored bars next to each entry visualize file and folder size at a glance (blue ≤10 MB, green ≤50 MB, yellow ≤100 MB, orange ≤1 GB, red >1 GB)
- Folder sizes are computed recursively on demand (only when the toggle is on); per-folder walk capped at 8 s to keep large trees responsive — incomplete sizes are marked with `≈`
- Bar length is logarithmic relative to the largest entry in the current view so a 10 MB file remains visible next to a 1 GB one
- API: `/api/files` accepts new query param `dir_size=1` to recursively compute directory sizes
- Toggle state persists in `localStorage` (key `nas-show-sizebars`); off by default
- i18n strings added in DE and EN (`btn_sizebars`, `computing_sizes`, `size_truncated`)

## 3.0.55
- Fix: shares pointing to a subdirectory under `/mnt/...` were marked `available = no` even when the underlying drive was mounted, because the subdirectory existed only on the container overlay (created before the mount) and was hidden by the live filesystem after mounting
- `generate_smb_conf.py` now auto-creates the missing subdirectory if its parent is a real mount point — share becomes immediately usable
- `/mnt/` paths now follow the same "real mount required" rule as `/media/` (was treated as always-available before)

## 3.0.54
- Default UI language switched to English (`en`); DE/EN toggle remains in the header
- README, root README and CHANGELOG translated to English; new entries from now on are written in English
- Add-on description (HA Add-on Store) translated to English
- Translation files (`de.yaml`, `en.yaml`) extended with the missing options `nas_name`, `smb_port`, `web_gui_enabled`

## 3.0.53
- Fix: `mount -t ext4` failed with `fsconfig() failed: Can't open blockdev` — HA OS blocks the new kernel mount API (`fsconfig`/`fsopen` syscalls) via seccomp
- `mount_helper.sh` now automatically falls back to `busybox mount` on `fsconfig()` errors — this uses the old `mount(2)` syscall which is still permitted
- Applies to all mount paths: direct, auto-detect, brute-force loop

## 3.0.52
- Fix: USB drives were not mounted at boot (race condition) — `restore_mounts.py` now retries the mount up to 5× with 3 s delay if the device is not yet ready (`Can't open blockdev`)
- Fix: wrong API endpoint in DOCS.md — `/api/reload-samba` does not exist, the correct one is `/api/samba/restart`

## 3.0.51
- Fix: backup-job card — Start / Edit / Delete buttons overflowed the card boundary on long paths
- Card layout restructured: text container (`min-width:0; flex:1`) shrinks if needed, button row (`flex-shrink:0`) always stays fully visible
- Source and destination paths split onto separate lines with line-wrap (`word-break:break-all`)

## 3.0.50
- Fix: drive view and user tab kept showing German text (`Einhängen`, `nicht eingehängt`, `Samba-Benutzer` etc.) after switching language — dynamically built JS content was not re-rendered on language change
- `toggleLang()` now reloads the active tab so all text appears immediately in the chosen language

## 3.0.49
- Fix: drives were no longer mounted after HA restart — `restore_mounts.py` used `fstype: "auto"` instead of the `resolved_fstype` (e.g. `"ext4"`) detected at mount time, which failed on USB devices

## 3.0.48
- Bind-mount feature removed: HA OS uses slave mount namespaces (`master:118`) — mounts from an add-on container do not propagate to other containers or HA Core. The feature was therefore ineffective.
- Mount dialog: checkbox and bind path field removed

## 3.0.47
- FS tag now stays visible even on unmounted drives
- New file `/data/fstype_memory.json` remembers the last known FS type per device — populated on every mount and on every drive listing from `/proc/mounts`, never auto-cleared
- Survives container restarts, unmount and reinstall (`/data` is persistent)

## 3.0.46
- FS tag display gets two additional sources because HA OS blocks raw block-device reads (`blkid` / `file -s` on block devices) with `EPERM`
- Source 2: `/proc/mounts` — reliably shows the FS type of every currently mounted device
- Source 3: `mounts.json` with new field `resolved_fstype` — at mount time, the actual kernel-used type is read from `/proc/mounts` and stored (not just "auto")
- The drive list now reliably shows the FS tag (e.g. `ext4`) for mounted devices and also for later-unmounted ones whose last mount type is known

## 3.0.45
- Drive overview: FS-type tag (`ext4`, `ntfs`, …) is reliably shown again
- When `lsblk` reports no FSTYPE, `blkid` / `blkid -p` / `file -sL` are queried as fallback — same escalation as in the mount helper

## 3.0.44
- Mount auto-detection significantly more robust
- Symlinks (e.g. `/dev/disk/by-id/usb-…`) are resolved before probing via `readlink -f`
- Fourth detection step: `file -s` reads the superblock magic directly (added the `file` package)
- Brute-force fallback: tries `ext4 → ext3 → ext2 → ntfs-3g → vfat → exfat → btrfs → xfs` in turn if all detectors come up empty — fixes USB devices where `blkid` / `lsblk` return nothing but `mount -t ext4` works

## 3.0.43
- Bind-mount name under `/share/<name>` is now editable in the mount dialog (input field directly below the checkbox)
- FS auto-detection in the helper extended to three stages: `blkid` → `blkid -p` (low-level probe) → `lsblk -no FSTYPE`
- Better helper logs (which method detected, which driver was tried)

## 3.0.42
- Mount "Auto" now uses `blkid` for FS detection first — works around the misleading "Can't open blockdev" error from `fsconfig()` on USB devices
- NTFS is automatically mounted via `ntfs-3g` (instead of kernel NTFS read-only)
- Falls back to bare `mount` if explicit `-t` fails
- Added `psmisc` (`fuser`) and `lsof` — busy diagnostics on unmount now work properly

## 3.0.41
- Fix: Mount button did not respond — `JSON.stringify(by_id)` contained double quotes that broke the `onclick="…"` attribute → "Unexpected end of input" when rendering the page
- Quotes are now HTML-escaped to `&quot;`

## 3.0.40
- Fix: Unmount button referenced the old `isSdaDevice` function → JS error, click did nothing
- Unmount dialog now uses the `system_device` flag from the drive API

## 3.0.39
- More robust drive unmounting
- Open Samba handles are closed before `umount` (`smbcontrol close-share`)
- Bind mounts under `/share/` automatically fall back to `umount -l` on "busy"
- For busy drives: GUI shows blocking processes (`fuser` / `lsof`) and offers "Force unmount"

## 3.0.38
- New: bind-mount of mounted drives to `/share/<name>` — accessible by HA Core and other add-ons
- Mount dialog: checkbox "Make accessible to HA Core / other add-ons" (on by default)
- Bind mounts are saved in `mounts.json` and restored automatically on restart
- On unmount the bind is removed first, then the actual mount
- `mount_helper.sh`: new `BIND` action with `mount --make-shared`

## 3.0.37
- Fix: HA backup location sometimes vanished after a restart (race condition)
- `smb.conf` is now generated twice — before and after restoring mounts

## 3.0.36
- All log messages translated to English (run.sh, app.py)

## 3.0.35
- New: option `web_gui_enabled` — web GUI can be fully disabled (smaller attack surface, Samba-only operation)

## 3.0.34
- Existing `/dev/sdX` entries are automatically migrated to `/dev/disk/by-id/` paths on startup

## 3.0.33
- Mounts use stable `/dev/disk/by-id/` paths — survive USB re-numbering after a reboot
- System devices are detected via `/proc/mounts` (no longer just `sda`)
- Inactive shares are marked `available = no` in `smb.conf`
- English documentation (`DOCS.md`) added

## 3.0.32
- Fix: `/ssl` changed from `ro` to `rw` (certificate folder was read-only)
- New: configurable `smb_port` for parallel operation with the official Samba add-on

## 3.0.31
- New: macOS junk files are globally hidden / deleted (`.DS_Store`, `._*`, `.TemporaryItems`, …) via Samba `veto files` + `delete veto files`

## 3.0.0
- New: Files tab — full file browser with upload, download, copy, move, rename, delete
- New: Backup tab — create backup jobs, run manually, auto-clean old backups (rsync-based)
- New: file upload directly via the web GUI (up to 10 GB)
- New: file download directly from the browser
- New: create folder in the file browser
- Dockerfile: rsync added

## 2.2.0
- New: web GUI port configurable (default: 8099)
- New: Samba passwords are saved persistently and restored automatically after restart

## 2.1.0
- Repository renamed to EasyNas-HASSIO-Addon
- Full README with installation guide, quick start, Nextcloud guide
- CHANGELOG added
- Add-on icon and logo
- Translations (DE/EN)
- All fixes from 2.0.x consolidated

## 2.0.9
- Fix: users and groups are restored automatically after container restart
- Fix: password change re-creates missing Samba entries automatically

## 2.0.8
- Browser: "Go to" label for navigation, /share button removed
- Mount field: clearer wording (name or full path)

## 2.0.7
- Fix: public shares now correctly ignore `valid users`
- Samba: SMB2/SMB3 protocol range, better NTLM compatibility

## 2.0.4
- Fix: share directories get correct permissions (2775)
- Fix: smb.conf reload restarts Samba automatically if not running
- Samba: `force user = root`, `force group = root` for full compatibility

## 2.0.0
- New: group management with member assignment
- New: users and groups as chips in the share configuration
- New: dark/light theme (HA design)
- New: sda protection with confirmation dialog
- New: create folder in the browser
- New: folder browser for mount paths with quick navigation

## 1.1.2
- Fix: wsdd as a script instead of pip package (HA wheels index does not have it)

## 1.1.1
- New: network discovery (Avahi for Linux/macOS, WSDD for Windows 10/11)

## 1.0.9
- Fix: `apparmor: false` and `privileged: SYS_ADMIN` for mount operations

## 1.0.8
- All mount points changed from /mnt to /media
- First release
