# Changelog

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
