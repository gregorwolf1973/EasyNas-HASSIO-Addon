# Changelog

## 3.0.50
- Fix: Laufwerksansicht und Benutzer-Tab zeigten nach Sprachumschaltung weiterhin deutschen Text (`Einhängen`, `nicht eingehängt`, `Samba-Benutzer` etc.) — dynamisch generierte JS-Inhalte wurden beim Sprachwechsel nicht neu gerendert
- `toggleLang()` lädt jetzt den aktiven Tab neu, sodass alle Texte sofort in der gewählten Sprache erscheinen

## 3.0.49
- Fix: Laufwerke wurden nach HA-Neustart nicht mehr eingehängt — `restore_mounts.py` benutzte `fstype: "auto"` statt des beim Mount erkannten `resolved_fstype` (z.B. `"ext4"`), was auf USB-Geräten fehlschlug

## 3.0.48
- Bind-Mount-Feature entfernt: HA-OS verwendet Slave-Mount-Namespaces (`master:118`) — Mounts aus einem Add-on-Container propagieren nicht zu anderen Containern oder HA Core. Die Funktion war damit wirkungslos.
- Mount-Dialog: Checkbox und Bind-Pfad-Feld entfernt

## 3.0.47
- FS-Tag bleibt jetzt auch bei ausgehängten Laufwerken sichtbar
- Neues File `/data/fstype_memory.json` merkt sich den letzten bekannten FS-Typ pro Device — wird bei jedem Mount und bei jedem Drive-Listing aus `/proc/mounts` gespeist und nie automatisch geleert
- Übersteht Container-Neustarts, Aushängen und Reinstall (`/data` ist persistent)

## 3.0.46
- FS-Tag-Anzeige bekommt zwei zusätzliche Quellen, weil HA-OS Raw-Block-Reads (`blkid`/`file -s` auf Blockdevices) per `EPERM` blockiert
- Quelle 2: `/proc/mounts` — zeigt den FS-Typ jedes aktuell gemounteten Devices verlässlich
- Quelle 3: `mounts.json` mit neuem Feld `resolved_fstype` — beim Mount wird der vom Kernel tatsächlich verwendete Typ aus `/proc/mounts` gelesen und gespeichert (nicht nur „auto")
- Damit zeigt die Drive-Liste den FS-Tag (z.B. `ext4`) sicher für gemountete Geräte und auch für später wieder offline genommene, deren letzter Mount-Typ bekannt ist

## 3.0.45
- Laufwerks-Übersicht: FS-Typ-Tag (`ext4`, `ntfs`, …) wird wieder zuverlässig angezeigt
- Wenn `lsblk` keinen FSTYPE meldet, wird `blkid` / `blkid -p` / `file -sL` als Fallback befragt — dieselbe Eskalation wie beim Mount-Helper

## 3.0.44
- Mount-Auto-Erkennung deutlich robuster
- Symlink (z.B. `/dev/disk/by-id/usb-…`) wird vor dem Probe per `readlink -f` aufgelöst
- Vierte Erkennungsstufe: `file -s` liest direkt die Superblock-Magic (Paket `file` ergänzt)
- Brute-Force-Fallback: nacheinander `ext4 → ext3 → ext2 → ntfs-3g → vfat → exfat → btrfs → xfs` versuchen, falls alle Detektoren leer kommen — fixt USB-Geräte, bei denen `blkid`/`lsblk` nichts melden, `mount -t ext4` aber funktioniert

## 3.0.43
- Bind-Mount-Name unter `/share/<name>` ist jetzt im Mount-Dialog frei änderbar (Eingabefeld direkt unter der Checkbox)
- FS-Auto-Erkennung im Helper auf drei Stufen erweitert: `blkid` → `blkid -p` (low-level Probe) → `lsblk -no FSTYPE`
- Bessere Logs im Helper (welche Methode hat erkannt, welcher Treiber wurde versucht)

## 3.0.42
- Mount „Automatisch" benutzt jetzt zuerst `blkid` zur FS-Erkennung — umgeht den irreführenden „Can't open blockdev"-Fehler von `fsconfig()` bei USB-Geräten
- NTFS wird automatisch über `ntfs-3g` (statt Kernel-NTFS-RO) gemountet
- Fallback auf nackten `mount`-Befehl, falls explizites `-t` fehlschlägt
- `psmisc` (`fuser`) und `lsof` ergänzt — Busy-Diagnose beim Aushängen funktioniert jetzt richtig

## 3.0.41
- Fix: Einhängen-Knopf reagierte nicht — `JSON.stringify(by_id)` enthielt doppelte Anführungszeichen, die das `onclick="…"`-Attribut zerschossen → „Unexpected end of input" beim Seitenrendern
- Anführungszeichen werden jetzt als `&quot;` HTML-escaped

## 3.0.40
- Fix: Aushängen-Knopf hatte einen Verweis auf die alte `isSdaDevice`-Funktion → JS-Fehler, Klick ohne Wirkung
- Unmount-Dialog nutzt jetzt das `system_device`-Flag der Drive-API

## 3.0.39
- Robusteres Aushängen von Laufwerken
- Vor `umount` werden offene Samba-Handles geschlossen (`smbcontrol close-share`)
- Bind-Mounts unter `/share/` fallen bei „busy" automatisch auf `umount -l` zurück
- Bei belegtem Laufwerk: GUI zeigt blockierende Prozesse (`fuser`/`lsof`) und bietet „Aushängen erzwingen"

## 3.0.38
- Neu: Bind-Mount eingehängter Laufwerke nach `/share/<name>` — Zugriff durch HA Core und andere Add-ons
- Mount-Dialog: Checkbox „Auch für HA Core / andere Add-ons zugänglich machen" (standardmäßig an)
- Bind-Mounts werden in `mounts.json` gespeichert und beim Neustart automatisch wiederhergestellt
- Beim Aushängen wird der Bind zuerst entfernt, dann der eigentliche Mount
- `mount_helper.sh`: neue `BIND`-Aktion mit `mount --make-shared`

## 3.0.37
- Fix: HA-Backup-Speicherort verschwand manchmal nach Neustart (Race Condition)
- `smb.conf` wird jetzt zweimal generiert — vor und nach dem Restore der Mounts

## 3.0.36
- Alle Log-Meldungen ins Englische übersetzt (run.sh, app.py)

## 3.0.35
- Neu: Option `web_gui_enabled` — Web-GUI komplett deaktivierbar (geringere Angriffsfläche, Samba-only-Betrieb)

## 3.0.34
- Bestehende `/dev/sdX`-Einträge werden beim Start automatisch zu `/dev/disk/by-id/`-Pfaden migriert

## 3.0.33
- Mounts verwenden stabile `/dev/disk/by-id/`-Pfade — überleben USB-Neusortierung nach Reboot
- System-Geräte werden über `/proc/mounts` erkannt (nicht mehr nur `sda`)
- Inaktive Shares werden in `smb.conf` als `available = no` markiert
- Englische Dokumentation (`DOCS.md`) ergänzt

## 3.0.32
- Fix: `/ssl` von `ro` auf `rw` geändert (Zertifikatsordner war read-only)
- Neu: konfigurierbarer `smb_port` für Parallelbetrieb mit dem offiziellen Samba-Add-on

## 3.0.31
- Neu: macOS-Junk-Dateien werden global ausgeblendet/gelöscht (`.DS_Store`, `._*`, `.TemporaryItems`, …) via Samba `veto files` + `delete veto files`

## 3.0.0
- Neu: Dateien-Tab — vollständiger Filebrowser mit Upload, Download, Kopieren, Verschieben, Umbenennen, Löschen
- Neu: Backup-Tab — Backup-Jobs erstellen, manuell starten, alte Backups automatisch aufräumen (rsync-basiert)
- Neu: Datei-Upload direkt über die Web-GUI (bis 10 GB)
- Neu: Datei-Download direkt aus dem Browser
- Neu: Ordner erstellen im Filebrowser
- Dockerfile: rsync hinzugefügt

## 2.2.0
- Neu: Web-GUI Port konfigurierbar (Standard: 8099)
- Neu: Samba-Passwörter werden persistent gespeichert und nach Neustart automatisch wiederhergestellt

## 2.1.0
- Repository umbenannt zu EasyNas-HASSIO-Addon
- Komplette README mit Installationsanleitung, Schnellstart, Nextcloud-Guide
- CHANGELOG hinzugefügt
- Addon-Icon und Logo
- Übersetzungen (DE/EN)
- Alle Fixes aus 2.0.x konsolidiert

## 2.0.9
- Fix: Benutzer und Gruppen werden nach Container-Neustart automatisch wiederhergestellt
- Fix: Passwort-Änderung erstellt fehlende Samba-Einträge automatisch neu

## 2.0.8
- Browser: "Gehe zu" Label für Navigation, /share Button entfernt
- Mount-Feld: Klarere Beschriftung (Name oder voller Pfad)

## 2.0.7
- Fix: Öffentliche Shares ignorieren jetzt `valid users` korrekt
- Samba: SMB2/SMB3 Protokollbereich, bessere NTLM-Kompatibilität

## 2.0.4
- Fix: Share-Verzeichnisse bekommen korrekte Permissions (2775)
- Fix: smb.conf Reload startet Samba automatisch neu falls nicht laufend
- Samba: `force user = root`, `force group = root` für volle Kompatibilität

## 2.0.0
- Neu: Gruppenverwaltung mit Mitglieder-Zuordnung
- Neu: Benutzer und Gruppen als Chips in der Freigabe-Konfiguration
- Neu: Dark/Light Theme (HA-Design)
- Neu: sda-Schutz mit Bestätigungs-Dialog
- Neu: Ordner erstellen im Browser
- Neu: Ordner-Browser für Mount-Pfade mit Quick-Navigation

## 1.1.2
- Fix: wsdd als Script statt pip-Paket (HA Wheels-Index hat es nicht)

## 1.1.1
- Neu: Netzwerk-Discovery (Avahi für Linux/macOS, WSDD für Windows 10/11)

## 1.0.9
- Fix: `apparmor: false` und `privileged: SYS_ADMIN` für Mount-Operationen

## 1.0.8
- Alle Mountpoints von /mnt auf /media geändert
- Erster Release
