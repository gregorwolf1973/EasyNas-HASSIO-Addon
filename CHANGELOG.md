# Changelog

## 3.2.1
- **Neu: Dateizugriff in den Einstellungen.** Dort stehen jetzt die freigegebenen Ordner, lassen sich einzeln hinzufuegen und entfernen, und ein Schalter "Zugriff auf alle Dateien" gibt das gesamte Dateisystem frei. Damit kommst du wieder an alles heran, wenn dir die Standardordner aus 3.2.0 zu eng sind. Die Einstellung wird in `/data/file_access.json` gespeichert und ueberlebt Neustarts.
- Auch bei vollem Zugriff bleiben `/data`, `/proc`, `/sys` und `/dev` gesperrt. In `/data` liegen die Passwort-Hashes des Addons selbst, die anderen drei sind keine echten Dateien.
- **Sitzungen gehaertet:** Cookies sind jetzt nur ueber HTTP lesbar, auf gleiche Seite beschraenkt und laufen nach 12 Stunden ab. Wird das Admin-Passwort geaendert, werden alle offenen Sitzungen ungueltig.
- **CSRF-Schutz:** Aendernde Aufrufe brauchen ein Sitzungs-Token. Eine fremde Seite kann damit nicht mehr im Hintergrund Dateien loeschen lassen, waehrend du angemeldet bist.
- **Einrichtungsmodus:** Ist der Passwortschutz eingeschaltet, aber kein Passwort gesetzt, schaltete sich der Schutz bisher stillschweigend ab und die Oberflaeche war offen. Jetzt sperrt sich das Addon und zeigt eine Anleitung, bis ein Passwort konfiguriert ist.
- **Dateinamen werden maskiert,** bevor sie in die Oberflaeche geschrieben werden. Ein Dateiname mit Anfuehrungszeichen oder HTML konnte vorher Javascript im Browser ausfuehren.

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

