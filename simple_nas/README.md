# Simple NAS – Home Assistant Add-on

Ein schlankes NAS-Add-on für Home Assistant OS. Verwandelt deinen Raspberry Pi (oder andere HA-Hardware) in einen vollwertigen Netzwerkspeicher mit Web-Oberfläche.

![Architectures](https://img.shields.io/badge/arch-aarch64%20|%20amd64%20|%20armv7-blue)
![Version](https://img.shields.io/badge/version-3.1.0-green)

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

## Host-Mount Modus (Addon-übergreifend)

**Standardmäßig** werden USB-Festplatten nur innerhalb des Simple NAS Containers gemountet. Andere Addons (z.B. Frigate, Nextcloud, Plex) und HA-Automationen können **nicht** direkt auf die gemounteten Laufwerke zugreifen.

**Mit aktiviertem Host-Mount** werden Laufwerke auf Host-Ebene gemountet. Dadurch sind sie unter `/media/MOUNT_NAME` für **alle Addons** und HA-Automationen sichtbar.

### Aktivierung

1. Simple NAS → **Konfiguration**
2. **Host-Mount** auf `true` setzen
3. Addon **neu starten**

### Wie funktioniert es?

| | Container-Mount (Standard) | Host-Mount |
|---|---|---|
| **Sichtbarkeit** | Nur Simple NAS | Alle Addons + HA Core |
| **Pfad im Addon** | `/media/nas` | `/media/nas` |
| **Pfad für andere Addons** | ❌ nicht sichtbar | ✅ `/media/nas` |
| **HA Automationen** | ❌ kein Zugriff | ✅ `/media/nas` |
| **Technik** | `mount` im Container | `nsenter` → Host-Namespace |
| **Risiko** | Gering | Mittel (Host-Dateisystem-Zugriff) |

### Anwendungsbeispiele

**Frigate Kamera-Aufnahmen auf USB-Festplatte:**
1. Host-Mount aktivieren
2. USB-Festplatte als `frigate` einhängen (→ `/media/frigate`)
3. In Frigate-Konfiguration: `recordings → retain → path: /media/frigate`

**HA Automation speichert Kamera-Snapshots:**
1. Host-Mount aktivieren
2. Festplatte als `snapshots` einhängen
3. In Automation: `filename: /media/snapshots/cam_{{ now().strftime('%Y%m%d_%H%M%S') }}.jpg`

### Sicherheitshinweise

- Host-Mount erfordert `host_pid: true` und `privileged: SYS_ADMIN` (bereits in der Addon-Konfiguration enthalten)
- Mounts werden über `nsenter` im Host-Mount-Namespace ausgeführt — das Addon hat damit Zugriff auf das Host-Dateisystem
- Bei Problemen kann der Host-Mount-Modus jederzeit in der Konfiguration deaktiviert werden
- Beim Aushängen werden sowohl der Host-Mount als auch der Container-Mount entfernt

## Unterstützte Dateisysteme

ext4, ext3, NTFS (ntfs-3g), FAT32 (vfat), exFAT, btrfs, XFS

## Netzwerk-Discovery

Das Add-on startet automatisch:

- **Avahi** (mDNS/DNS-SD) – NAS erscheint in Linux-Dateimanagern (Nautilus, Dolphin) und macOS Finder
- **WSDD** (WS-Discovery) – NAS erscheint im Windows 10/11 Explorer unter „Netzwerk"
- **nmbd** (NetBIOS) – klassische Windows-Netzwerkumgebung

## Host-Mount (für alle Addons sichtbar)

Standardmäßig sind gemountete USB-Laufwerke **nur innerhalb des Simple NAS Addons** sichtbar. Andere Addons (Frigate, Nextcloud, etc.) und HA-Automationen können nicht direkt auf die Dateien zugreifen.

Mit der **Host-Mount** Option wird das Laufwerk auf Host-Ebene gemountet. Dadurch erscheint es unter `/media/` in **allen** Addons und ist auch über HA-Automationen (z.B. Kamera-Snapshots speichern) erreichbar.

### Aktivierung

1. Gehe zu **Einstellungen → Add-ons → Simple NAS → Konfiguration**
2. Setze `host_mount` auf **true**
3. **Add-on neu starten**

### Wie es funktioniert

| Modus | Mount-Pfad | Sichtbar für |
|-------|-----------|-------------|
| **Container-Mount** (Standard) | `/media/NAME` oder `/mnt/NAME` | Nur Simple NAS Addon |
| **Host-Mount** | `/mnt/data/supervisor/media/NAME` auf dem Host | Alle Addons, HA Core, Automationen |

Technisch nutzt der Host-Mount `nsenter --mount=/proc/1/ns/mnt` um den `mount`-Befehl im Mount-Namespace des Host-Systems auszuführen. Dadurch wird der Mount nicht im isolierten Container-Namespace, sondern direkt auf dem Host registriert. Der HA-Supervisor macht `/mnt/data/supervisor/media/` automatisch als `/media/` in allen Containern verfügbar.

### Voraussetzungen

- `host_pid: true` ist in der Addon-Konfiguration gesetzt (ab v3.1.0 automatisch)
- `privileged: SYS_ADMIN` ist erforderlich (bereits gesetzt)
- Funktioniert nur mit Home Assistant OS (nicht mit Supervised)

### Beispiel: Kamera-Snapshots auf USB-Festplatte

1. USB-Festplatte anschließen und mit Host-Mount unter `nas` einhängen
2. In einer HA-Automation:
   ```yaml
   service: camera.snapshot
   data:
     entity_id: camera.wohnzimmer
     filename: /media/nas/snapshots/{{ now().strftime('%Y%m%d_%H%M%S') }}.jpg
   ```

### Beispiel: Frigate-Aufnahmen auf USB-Festplatte

1. Host-Mount aktivieren, Festplatte als `frigate` mounten
2. In der Frigate-Konfiguration den Aufnahmepfad auf `/media/frigate` setzen

### Hinweise

- Bei aktivem Host-Mount werden **neue** Mounts auf dem Host ausgeführt. Bestehende Container-Mounts müssen neu erstellt werden.
- Beim Aushängen wird sowohl der Host-Mount als auch ein eventueller Container-Bind-Mount entfernt.
- Falls der Host-Namespace nicht erreichbar ist, fällt das Addon automatisch auf Container-Mount zurück.

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
