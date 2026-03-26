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

1. Verbinde dich per SSH mit deinem Home Assistant
2. Kopiere den **gesamten Ordner** `simple_nas` nach `/addons/`:
   ```
   /addons/simple_nas/
   ```
   Die Struktur muss exakt so aussehen:
   ```
   /addons/simple_nas/
   ├── config.yaml
   ├── build.yaml
   ├── Dockerfile
   ├── run.sh
   └── app/
       ├── app.py
       ├── generate_smb_conf.py
       ├── restore_mounts.py
       └── templates/
           └── index.html
   ```
3. Home Assistant → **Einstellungen → Add-ons → Add-on Store**
4. Oben rechts **⋮ → Lokale Add-ons neu laden**
5. **"Simple NAS"** unter „Lokale Add-ons" → **Installieren**
6. Nach der Installation: **Starten** → **Öffnen**

### Methode 2: GitHub Repository

1. Lade den Inhalt in ein GitHub-Repository hoch
2. HA → Add-on Store → ⋮ → Repositories → URL eintragen
3. Addon installieren

---

## Konfiguration

```yaml
workgroup: WORKGROUP   # Windows-Arbeitsgruppe (Standard: WORKGROUP)
log_level: info
```

---

## Web-GUI

Erreichbar über den **„Öffnen"-Button** im Addon (HA Ingress).

| Tab | Funktion |
|-----|----------|
| **Übersicht** | CPU, RAM, Disk-Status, Samba live-Status |
| **Laufwerke** | Festplatten erkennen, ein-/aushängen |
| **Freigaben** | Samba-Shares erstellen & verwalten |
| **Benutzer** | Samba-Benutzer anlegen & Passwort ändern |

---

## Netzwerkzugriff

| System | Adresse |
|--------|---------|
| Windows | `\\<raspberry-ip>\<freigabe>` |
| macOS | `smb://<raspberry-ip>/<freigabe>` |
| Linux | `smb://<raspberry-ip>/<freigabe>` |

---

## Unterstützte Dateisysteme

ext4, ext3, NTFS (ntfs-3g), FAT32, exFAT, btrfs, XFS

---

## Addon-Struktur

```
simple_nas/
├── config.yaml               # HA Addon-Manifest
├── build.yaml                # Basis-Images je Architektur
├── Dockerfile                # Container-Definition
├── run.sh                    # Start-Skript
└── app/
    ├── app.py                # Flask Web-GUI & REST-API
    ├── generate_smb_conf.py  # smb.conf Generator
    ├── restore_mounts.py     # Mounts nach Neustart wiederherstellen
    └── templates/
        └── index.html        # Web-GUI
```

## Persistente Daten (`/data/`)

| Datei | Inhalt |
|-------|--------|
| `shares.json` | Konfigurierte Freigaben |
| `users.json` | Angelegte Benutzer |
| `mounts.json` | Einhängepunkte (überleben Neustarts) |
