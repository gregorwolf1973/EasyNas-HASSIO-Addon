# Changelog

## 3.1.0
- Neu: Host-Mount Modus — Laufwerke auf Host-Ebene mounten via nsenter
- Mounts sind mit Host-Mount für ALLE Addons und HA-Automationen unter /media/ sichtbar
- Konfigurierbar über host_mount: true/false in den Addon-Einstellungen
- host_pid: true hinzugefügt für nsenter Host-Namespace-Zugriff
- Fallback auf Container-Mount wenn Host-Namespace nicht erreichbar

## 3.1.0
- Neu: Host-Mount Modus — Laufwerke auf Host-Ebene mounten (sichtbar für alle Addons und HA-Automationen)
- Neu: Toggle in der Addon-Konfiguration (host_mount: true/false)
- Neu: nsenter-basierter Mount in den Host-Mount-Namespace
- Neu: Automatischer Fallback auf Container-Mount wenn Host-Namespace nicht erreichbar
- Doku: Ausführliche Anleitung für Host-Mount mit Anwendungsbeispielen (Frigate, HA Automationen)

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
