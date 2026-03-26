# Simple NAS – Home Assistant Add-on

Ein schlankes NAS-Add-on für Home Assistant OS. Verwandelt deinen Raspberry Pi (oder andere HA-Hardware) in einen vollwertigen Netzwerkspeicher mit Web-Oberfläche.

![Architectures](https://img.shields.io/badge/arch-aarch64%20|%20amd64%20|%20armv7-blue)
![Version](https://img.shields.io/badge/version-2.2.0-green)

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
- **Persistente Konfiguration** – Shares, User, Gruppen und Mounts überleben Neustarts

## Installation

### Methode 1: GitHub Repository (empfohlen)

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

> **Hinweis:** Samba-Passwörter können aus Sicherheitsgründen nicht persistent gespeichert werden. Nach einem Add-on-Rebuild müssen die Passwörter über die GUI neu gesetzt werden. Die Benutzer selbst werden automatisch wiederhergestellt.

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
- Nach einem Add-on-Rebuild müssen Passwörter neu gesetzt werden
- Benutzer → Passwort-Icon klicken → neues Passwort vergeben

## Lizenz

MIT License
