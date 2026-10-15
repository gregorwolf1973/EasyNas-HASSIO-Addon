# Notfall-Paket – Daten ohne Home Assistant zurückholen

Dieser Ordner ist der **Bauplan**, nicht das fertige Werkzeug. Bitte die
Dateien **nicht einzeln herunterladen** – es fehlen die Programme restic
und rclone, und der Starter meldet nur „Hier fehlen die Programme“.

## Paket bauen

Einmal vorab erledigen, solange alles läuft – nicht erst im Notfall:

```bash
git clone https://github.com/gregorwolf1973/EasyNas-HASSIO-Addon.git
cd EasyNas-HASSIO-Addon
bash notfall/paket-bauen.sh            # mit --arm64 auch für Linux auf ARM (z. B. Raspberry Pi)
```

Unter Windows in der Git Bash, unter Linux in jedem Terminal. Heraus kommt
`notfall/dist/SimpleNAS-Notfall-<datum>.zip` (rund 80 MB). Das Skript lädt
restic und rclone von den offiziellen GitHub-Releases und prüft sie gegen
deren SHA256-Prüfsummen; ohne Angabe nimmt es jeweils die neueste Version,
feste Versionen gehen so: `bash notfall/paket-bauen.sh 0.19.1 1.75.2`.

Das ZIP auf einen USB-Stick **und** an einen zweiten Ort legen – nicht aufs
NAS. Daneben das ausgedruckte **Notfallblatt** (Knopf am Backup-Job im
Add-on): darauf stehen die Zugangsdaten und das Archiv-Passwort.

## Im Notfall

ZIP **komplett** auspacken, dann

- **Windows:** Doppelklick auf `WIEDERHERSTELLEN-Windows.cmd`
- **Linux:** `bash wiederherstellen-linux.sh`

Alles Weitere steht in der `LIESMICH.txt` im Paket.

## Aufbau des Pakets

```
SimpleNAS-Notfall/
  WIEDERHERSTELLEN-Windows.cmd   Starter Windows
  wiederherstellen-linux.sh      Starter Linux
  LIESMICH.txt                   Anleitung
  programme/                     alles Technische – oben stehen nur die
    wiederherstellen.ps1         drei Dinge, die man im Notfall braucht
    windows-amd64/               restic.exe, rclone.exe
    linux-amd64/                 restic, rclone
    VERSIONEN.txt
```

Unterstützt werden restic-Jobs auf einer Hetzner Storage Box oder einem
SFTP-Server mit Passwort. Für andere Ziele beschreibt das Notfallblatt den
Weg von Hand.
