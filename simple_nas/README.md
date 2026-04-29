# Simple NAS – Home Assistant Add-on

Ein schlankes NAS-Add-on für Home Assistant OS. Verwandelt deinen Raspberry Pi (oder andere HA-Hardware) in einen vollwertigen Netzwerkspeicher mit Web-Oberfläche.

![Architectures](https://img.shields.io/badge/arch-aarch64%20|%20amd64%20|%20armv7-blue)
![Version](https://img.shields.io/badge/version-3.0.0-green)

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://buymeacoffee.com/gregorwolf1973)

## Features

- **Samba-Server** (SMB/CIFS) für Windows, macOS & Linux
- **Web-GUI** direkt in Home Assistant (Ingress)
- **Laufwerksverwaltung** – externe USB-Festplatten ein-/aushängen
- **Freigabenverwaltung** – Samba-Shares erstellen, bearbeiten & löschen
- **Benutzer & Gruppen** – Samba-User mit Passwörtern und Gruppenzugehörigkeit
- **Rechteverwaltung** – Freigaben für einzelne Benutzer oder Gruppen einschränken
- **Netzwerk-Discovery** – automatische Erkennung in Windows Explorer (WSDD), Linux Nautilus/Dolphin (Avahi/mDNS) und macOS Finder
- **Dark/Light Theme** – umschaltbar im Header
- **Schutz vor versehentlichem Aushängen** von System-Partitionen (sda-Warnung)
- **Datei-Manager** – Dateien durchsuchen, hochladen, herunterladen, kopieren, verschieben, umbenennen und löschen
- **Backup-Jobs** – Quell-/Zielordner konfigurieren, manuell starten, alte Backups automatisch aufräumen
- **Persistente Konfiguration** – Shares, User, Gruppen, Mounts und Passwörter überleben Neustarts

## Installation

### Methode 1: GitHub Repository (empfohlen)

[![Repository zu Home Assistant hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fgregorwolf1973%2FEasyNas-HASSIO-Addon)

Auf den Button klicken → Repository wird automatisch hinzugefügt → weiter mit Schritt 4.

Oder manuell:
1. In Home Assistant: **Einstellungen → Add-ons → Add-on Store**
2. Oben rechts **⋮ → Repositories**
3. URL eintragen: `https://github.com/gregorwolf1973/EasyNas-HASSIO-Addon`
4. **Simple NAS** erscheint im Store → **Installieren**
5. **Starten** → **In Seitenleiste anzeigen** aktivieren → **Öffnen**

### Methode 2: Lokales Add-on

1. Per SSH oder Samba-Zugriff den Ordner `simple_nas/` nach `/addons/` kopieren:
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
           └── index.html
   ```
2. **Einstellungen → Add-ons → Add-on Store → ⋮ → Lokale Add-ons neu laden**
3. **Simple NAS** unter „Lokale Add-ons" → **Installieren** → **Starten**

## Schnellstart

### 1. Festplatte einhängen

1. USB-Festplatte anschließen
2. Simple NAS öffnen → Tab **Laufwerke**
3. Partition auswählen (z.B. `/dev/sdb1`) → **Einhängen**
4. Name eingeben (z.B. `nas`) → wird zu `/media/nas`, oder vollen Pfad angeben (z.B. `/mnt/nas`)

### 2. Benutzer anlegen

1. Tab **Benutzer & Gruppen** → **+ Neuer Benutzer**
2. Benutzername und Passwort eingeben → **Speichern**

### 3. Freigabe erstellen

1. Tab **Freigaben** → **+ Neue Freigabe**
2. Name und Pfad eingeben (z.B. `/mnt/nas`)
3. Öffentlich oder für bestimmte Benutzer/Gruppen freigeben
4. **Speichern**

### 4. Netzwerkzugriff

| System | Adresse |
|--------|---------|
| **Windows** | `\\192.168.x.x\freigabe` im Explorer eingeben |
| **macOS** | Finder → Gehe zu → Mit Server verbinden → `smb://192.168.x.x/freigabe` |
| **Linux** | Dateimanager → `smb://192.168.x.x/freigabe` |
| **Nextcloud** | Externer Speicher → SMB/CIFS → Host + Share-Name + Credentials |

## Web-GUI

Die Oberfläche ist über den **„Öffnen"-Button** im Add-on erreichbar (HA Ingress).

| Tab | Funktion |
|-----|----------|
| **Übersicht** | CPU, RAM, Disk-Status, Samba-Dienststatus |
| **Laufwerke** | Festplatten erkennen, ein-/aushängen, Schutz für System-Partitionen |
| **Freigaben** | Samba-Shares erstellen, Benutzer/Gruppen zuweisen, öffentlich/privat |
| **Benutzer & Gruppen** | Samba-Benutzer anlegen, Passwörter ändern, Gruppen verwalten |
| **Dateien** | Filebrowser mit Upload, Download, Kopieren, Verschieben, Umbenennen, Löschen |
| **Backup** | Backup-Jobs erstellen, manuell starten, alte Backups automatisch löschen |

### Dark/Light Theme

Umschaltbar über den Mond/Sonnen-Button oben rechts. Die Einstellung wird im Browser gespeichert.

## Konfiguration

In der Add-on-Konfiguration:

```yaml
workgroup: WORKGROUP   # Windows-Arbeitsgruppe (Standard: WORKGROUP)
log_level: info        # trace, debug, info, notice, warning, error, fatal
web_port: 8099         # Port für die Web-Oberfläche (Standard: 8099)
```

> **Hinweis zum Port:** Der Standard-Port 8099 wird vom HA-Ingress-Button ("Öffnen") verwendet. Wenn du den Port änderst, funktioniert Ingress möglicherweise nicht mehr. Greife dann direkt über `http://DEINE-HA-IP:PORT` zu.

## Freigabe-Optionen

| Option | Beschreibung |
|--------|-------------|
| **Name** | Netzwerkname der Freigabe (z.B. `NAS`, `Dokumente`) |
| **Pfad** | Lokaler Pfad auf dem Server (z.B. `/mnt/nas`) |
| **Schreibzugriff** | Benutzer können Dateien erstellen und ändern |
| **Öffentlich** | Zugriff ohne Passwort (Gastzugang) |
| **Benutzer** | Einzelne Samba-Benutzer die Zugriff haben (nur wenn nicht öffentlich) |
| **Gruppen** | Samba-Gruppen die Zugriff haben (nur wenn nicht öffentlich) |

> **Hinweis:** Wenn eine Freigabe auf „Öffentlich" gesetzt ist, haben **alle** Netzwerkteilnehmer Zugriff — die Benutzer/Gruppen-Auswahl hat dann keine Wirkung.

## Unterstützte Dateisysteme

ext4, ext3, NTFS (ntfs-3g), FAT32 (vfat), exFAT, btrfs, XFS

## Netzwerk-Discovery

Das Add-on startet automatisch:

- **Avahi** (mDNS/DNS-SD) – NAS erscheint in Linux-Dateimanagern (Nautilus, Dolphin) und macOS Finder
- **WSDD** (WS-Discovery) – NAS erscheint im Windows 10/11 Explorer unter „Netzwerk"
- **nmbd** (NetBIOS) – klassische Windows-Netzwerkumgebung

## Verwendung mit Nextcloud

1. In Simple NAS: Freigabe erstellen, Benutzer zuweisen (z.B. `admin`)
2. In Nextcloud: **Einstellungen → Verwaltung → Externe Speicher**
3. Konfiguration:
   - Ordnername: beliebig (z.B. `/nas`)
   - Externer Speicher: **SMB/CIFS**
   - Authentifizierung: **Benutzername und Passwort**
   - Host: `192.168.x.x` (IP des Raspberry Pi)
   - Share: Name der Freigabe (exakt wie in Simple NAS, Groß-/Kleinschreibung beachten!)
   - Benutzername/Passwort: ein Samba-Benutzer aus Simple NAS

> **Tipp:** „Globale Anmeldeinformationen" funktioniert oft nicht zuverlässig. Verwende stattdessen „Benutzername und Passwort" direkt.

## Persistente Daten

Alle Konfigurationsdaten werden in `/data/` gespeichert und überleben Add-on-Updates und Neustarts:

| Datei | Inhalt |
|-------|--------|
| `shares.json` | Konfigurierte Freigaben |
| `users.json` | Angelegte Benutzer |
| `groups.json` | Gruppen und Mitgliedschaften |
| `mounts.json` | Einhängepunkte (werden beim Start wiederhergestellt) |

> Alle Daten inklusive Samba-Passwörter werden automatisch gesichert und nach einem Neustart wiederhergestellt.

## Technische Details

- Basiert auf dem offiziellen Home Assistant Base Image (Alpine Linux)
- Samba 4.x mit SMB2/SMB3 Protokoll
- Flask Web-GUI auf Port 8099 (Ingress)
- Mount-Operationen über privilegierten FIFO-Daemon
- Unterstützte Architekturen: aarch64 (Raspberry Pi 4/5), amd64, armv7

## Fehlerbehebung

**Festplatte wird nicht erkannt:**
- Prüfe ob die Festplatte unter **Laufwerke** als Block-Device auftaucht
- USB-Hub ohne Stromversorgung kann Probleme verursachen

**Mount schlägt fehl:**
- Prüfe das Add-on-Log auf Fehlermeldungen
- Das Dateisystem könnte nicht unterstützt werden oder die Partition ist beschädigt
- Versuche einen anderen Dateisystemtyp auszuwählen statt „Automatisch"

**Samba-Share nicht erreichbar:**
- Prüfe ob smbd/nmbd in der Übersicht als „aktiv" angezeigt werden
- Klicke „Samba neu starten"
- Prüfe die Firewall-Einstellungen deines Netzwerks (Port 445 TCP)

**Nextcloud kann Share nicht öffnen:**
- Verwende „Benutzername und Passwort" statt „Globale Anmeldeinformationen"
- Share-Name muss exakt übereinstimmen (Groß-/Kleinschreibung!)
- Samba-Passwort im Simple NAS GUI neu setzen

**Benutzer-Fehler nach Neustart:**
- Passwörter werden automatisch wiederhergestellt
- Falls dennoch Probleme auftreten: Benutzer → Passwort-Icon → neues Passwort vergeben

## Lizenz

MIT License

---

# Simple NAS – Home Assistant Add-on (English)

A lightweight NAS add-on for Home Assistant OS. Turns your Raspberry Pi (or other HA hardware) into a full-featured network storage with web interface.

![Architectures](https://img.shields.io/badge/arch-aarch64%20|%20amd64%20|%20armv7-blue)

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://buymeacoffee.com/gregorwolf1973)

## Features

- **Samba Server** (SMB/CIFS) for Windows, macOS & Linux
- **Web GUI** directly in Home Assistant (Ingress)
- **Drive management** – mount/unmount external USB drives
- **Share management** – create, edit & delete Samba shares
- **Users & Groups** – Samba users with passwords and group membership
- **Access control** – restrict shares to specific users or groups
- **Network discovery** – auto-discovery in Windows Explorer (WSDD), Linux Nautilus/Dolphin (Avahi/mDNS) and macOS Finder
- **Dark/Light theme** – switchable in the header
- **Protection** against accidental unmounting of system partitions
- **File manager** – browse, upload, download, copy, move, rename and delete files
- **Backup jobs** – configure source/destination, run manually, auto-clean old backups
- **Persistent configuration** – shares, users, groups, mounts and passwords survive restarts
- **Admin password protection** – optional login screen for the web interface

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
log_level: info               # trace, debug, info, notice, warning, error, fatal
web_port: 8100                # Port for the web interface (default: 8100)
admin_password_enabled: false # Enable web UI password protection
admin_username: "admin"       # Admin login username
admin_password: ""            # Admin login password (stored encrypted)
```

### Admin Password Protection

When `admin_password_enabled` is set to `true`, the web interface requires a login.
- Set `admin_username` and `admin_password` freely
- The password is stored as a secure hash (pbkdf2:sha256) in `/data/admin_auth.json` — never in plaintext
- To disable protection again, set `admin_password_enabled` to `false` and restart the add-on
- Changing the password in the config and restarting the add-on immediately takes effect

## Web GUI

| Tab | Function |
|-----|----------|
| **Overview** | CPU, RAM, disk status, Samba service status |
| **Drives** | Detect drives, mount/unmount, system partition protection |
| **Shares** | Create Samba shares, assign users/groups, public/private |
| **Users & Groups** | Create Samba users, change passwords, manage groups |
| **Files** | File browser with upload, download, copy, move, rename, delete |
| **Backup** | Create backup jobs, run manually, auto-delete old backups |

## Supported Filesystems

ext4, ext3, NTFS (ntfs-3g), FAT32 (vfat), exFAT, btrfs, XFS

## Network Discovery

The add-on automatically starts:

- **Avahi** (mDNS/DNS-SD) – NAS appears in Linux file managers (Nautilus, Dolphin) and macOS Finder
- **WSDD** (WS-Discovery) – NAS appears in Windows 10/11 Explorer under "Network"
- **nmbd** (NetBIOS) – classic Windows network neighbourhood

## Persistent Data

All configuration data is stored in `/data/` and survives add-on updates and restarts:

| File | Contents |
|------|----------|
| `shares.json` | Configured shares |
| `users.json` | Created users |
| `groups.json` | Groups and memberships |
| `mounts.json` | Mount points (restored on startup) |
| `admin_auth.json` | Hashed admin credentials (if password protection is enabled) |

## Troubleshooting

**Drive not detected:**
- Check if the drive appears under **Drives** as a block device
- An unpowered USB hub can cause issues

**Mount fails:**
- Check the add-on log for error messages
- The filesystem might be unsupported or the partition is corrupted
- Try selecting a specific filesystem type instead of "Auto"

**Samba share unreachable:**
- Check if smbd/nmbd show as "active" in the overview
- Click "Restart Samba"
- Check your network firewall settings (port 445 TCP)

**Cannot log in to web interface:**
- Check that `admin_password_enabled` is `true` and both `admin_username` and `admin_password` are set
- Restart the add-on after changing credentials in the config
- If locked out: set `admin_password_enabled` to `false`, restart, then re-enable with new credentials

## License

MIT License
