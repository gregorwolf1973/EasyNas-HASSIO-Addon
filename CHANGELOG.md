# Changelog

## 3.2.0
- **Sicherheit:** Der Dateimanager war nicht auf Verzeichnisse begrenzt. Jeder Pfad, den der Browser schickte, wurde als root geoeffnet - auch `/etc`, `/data` oder `/config`. Da die Oberflaeche wegen `host_network` im LAN erreichbar ist und der Passwortschutz standardmaessig aus war, konnte jedes Geraet im Heimnetz jede Datei des Hosts lesen und schreiben.
- Alle 21 Stellen, die einen Pfad vom Browser entgegennehmen, laufen jetzt durch eine gemeinsame Pruefung: Symlinks werden aufgeloest, `..` abgewiesen, und das Ergebnis muss in einem erlaubten Ordner liegen. Neue Option `file_allowed_roots` (Vorgabe `/media`, `/mnt`, `/share`, `/config`, `/addon_configs`).
- `/data`, `/ssl`, `/etc`, `/proc`, `/sys`, `/dev`, `/var`, `/run`, `/boot` und `/root` sind unabhaengig von der Konfiguration gesperrt.
- Einhaengepunkte koennen nur noch unter `/media` oder `/mnt` liegen. Sicherungsauftraege pruefen Quelle und Ziel auch beim Ausfuehren erneut, weil dort rsync als root laeuft.
- Beim Hochladen kann der Dateiname nicht mehr aus dem Zielordner ausbrechen, und vorhandene Dateien werden nicht mehr stillschweigend ueberschrieben.
- Loeschen und Umbenennen sind fuer die erlaubten Wurzeln selbst, alle Einhaengepunkte und alle Freigabeordner gesperrt.
- Die festen Knoepfe `/media` `/mnt` `/` sind durch die tatsaechlich erlaubten Ordner ersetzt, der Pfadpfad endet nicht mehr im Container-Wurzelverzeichnis.

## 3.1.14

- Fix: Absicherung gegen einen Startabsturz. Beim Binden des Webservers
  fragt Python den Hostnamen per Reverse-DNS ab. Liefert der DNS-Server
  einen Namen, der kein gültiges UTF-8 ist, warf das einen
  UnicodeDecodeError und das Addon startete nicht (Supervisor-Status
  "error"). Die Abfrage ist jetzt gekapselt.

