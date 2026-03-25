# Simple NAS – Home Assistant Addon

Ein schlankes NAS-Addon für Home Assistant OS mit:
- **Samba-Server** (SMB/CIFS) für Windows, macOS & Linux
- **Web-GUI** zur Verwaltung direkt in Home Assistant
- **Laufwerksverwaltung**: Externe Festplatten ein-/aushängen
- **Freigabenverwaltung**: Samba-Shares erstellen & konfigurieren
- **Benutzerverwaltung**: Samba-Benutzer mit Passwörtern

---

## Installation

### Methode 1: Lokales Addon (empfohlen)

1. Verbinde dich per SSH oder Samba mit deinem Home Assistant
2. Kopiere den Ordner `simple-nas-addon` nach:
   ```
   /addons/simple_nas/
   ```
3. Öffne Home Assistant → **Einstellungen → Add-ons → Add-on Store**
4. Klicke oben rechts auf **⋮ → Lokale Add-ons neu laden**
5. Das Addon **"Simple NAS"** erscheint nun unter „Lokale Add-ons"
6. Klicke auf **Installieren**

### Methode 2: Eigenes Repository

1. Erstelle ein GitHub-Repository mit dem Addon-Inhalt
2. Füge es in HA unter **Add-on Store → Repositories** hinzu

---

## Konfiguration

Nach der Installation, im Addon unter **Konfiguration**:

```yaml
workgroup: WORKGROUP   # Windows-Arbeitsgruppe (Standard: WORKGROUP)
log_level: info
```

---

## Web-GUI

Die GUI ist über **HA Ingress** erreichbar:
- Im Addon auf **"Öffnen"** klicken
- Oder direkt: `http://<ha-ip>:8099`

### Tabs:
| Tab | Funktion |
|-----|----------|
| **Übersicht** | CPU, RAM, Disk-Status, Samba-Dienste |
| **Laufwerke** | Festplatten erkennen, ein-/aushängen |
| **Freigaben** | Samba-Shares erstellen & verwalten |
| **Benutzer** | Samba-Benutzer anlegen/Passwort ändern |

---

## Netzwerkzugriff

Nach dem Einrichten der Freigaben erreichbar unter:

- **Windows:** `\\<raspberrypi-ip>\<freigabe-name>`
- **macOS:** `smb://<raspberrypi-ip>/<freigabe-name>`
- **Linux:** `smb://<raspberrypi-ip>/<freigabe-name>`

---

## Dateisysteme

Unterstützte Dateisysteme für externe Festplatten:
- **ext4 / ext3** – Linux-Dateisysteme
- **NTFS** – Windows (via ntfs-3g)
- **FAT32 / exFAT** – USB-Sticks & externe Platten
- **btrfs / XFS** – moderne Linux-FS

---

## Addon-Struktur

```
simple-nas-addon/
├── config.yaml               # HA Addon-Manifest
├── Dockerfile                # Container-Definition
├── run.sh                    # Start-Skript
└── app/
    ├── app.py                # Flask Web-GUI (API + Frontend)
    ├── generate_smb_conf.py  # smb.conf Generator
    ├── restore_mounts.py     # Laufwerke nach Neustart wiederherstellen
    └── templates/
        └── index.html        # Web-GUI
```

---

## Gespeicherte Daten (`/data/`)

| Datei | Inhalt |
|-------|--------|
| `shares.json` | Konfigurierte Freigaben |
| `users.json` | Angelegte Benutzer |
| `mounts.json` | Einhängepunkte (persistent über Neustarts) |
