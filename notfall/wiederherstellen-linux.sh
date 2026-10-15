#!/usr/bin/env bash
# ============================================================
#  Simple NAS – Notfall-Wiederherstellung (Linux)
#
#  Holt Daten aus dem restic-Archiv auf der Hetzner Storage Box zurück,
#  ohne Home Assistant. Der Weg ist derselbe wie im Add-on: restic spricht
#  die Box über rclone (SFTP) an.
#
#  Passwörter werden nur eingetippt und nirgends gespeichert: rclone und
#  restic bekommen sie über Umgebungsvariablen dieses Prozesses.
#  Aufruf:  bash wiederherstellen-linux.sh
# ============================================================
set -u

PAKET="$(cd "$(dirname "$0")" && pwd)"
case "$(uname -m)" in
  x86_64|amd64)  ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) echo "Prozessor $(uname -m) wird nicht unterstützt (nur x64 und ARM64)."; exit 1 ;;
esac
BIN="$PAKET/programme/linux-$ARCH"
RESTIC="$BIN/restic"; RCLONE="$BIN/rclone"
EINST="$PAKET/einstellungen.txt"
for p in "$RESTIC" "$RCLONE"; do
  [ -f "$p" ] || { echo "Programm fehlt: $p"; exit 1; }
  chmod +x "$p" 2>/dev/null   # ZIP-Dateien verlieren das Ausführrecht
done

frage() {   # text vorgabe -> $ANTWORT
  local a
  if [ -n "$2" ]; then read -r -p "$1 [$2]: " a; else read -r -p "$1: " a; fi
  ANTWORT="${a:-$2}"
}
geheim() { local a; read -r -s -p "$1: " a; echo >&2; GEHEIM="$a"; }
janein() { local a; read -r -p "$1 [j/N]: " a; [[ "$a" =~ ^[[:space:]]*[jJyY] ]]; }
# restic startet rclone selbst und sucht es im PATH – nicht über
# -o rclone.program, das zerlegt den Wert und scheitert an Leerzeichen.
export PATH="$BIN:$PATH"
restic_() { "$RESTIC" "$@"; }

# ---------- Angaben (ohne Passwörter) aus einstellungen.txt ----------
HOST=""; PORT=22; BENUTZER=""; PFAD=simplenas
if [ -f "$EINST" ]; then
  while IFS='=' read -r k v; do
    k="${k//[[:space:]]/}"; v="$(echo "$v" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    case "$k" in HOST|PORT|BENUTZER|PFAD) printf -v "$k" '%s' "$v" ;; esac
  done < "$EINST"
fi

echo
echo "  Simple NAS – Notfall-Wiederherstellung"
echo "  Daten aus dem restic-Archiv auf der Hetzner Storage Box zurückholen"
echo "  (Enter übernimmt den Wert in eckigen Klammern)"
echo

while true; do
  frage "Storage Box (z. B. u123456.your-storagebox.de)" "$HOST"; HOST="$ANTWORT"
  [ -z "$BENUTZER" ] && [[ "$HOST" =~ ^(u[0-9]+) ]] && BENUTZER="${BASH_REMATCH[1]}"
  frage "Benutzer (z. B. u123456)" "$BENUTZER"; BENUTZER="$ANTWORT"
  frage "Port" "$PORT"; PORT="$ANTWORT"
  frage "Pfad auf der Box (im Add-on: Zielpfad)" "$PFAD"; PFAD="$ANTWORT"
  geheim "Passwort der Storage Box"; BOXPW="$GEHEIM"
  geheim "Archiv-Passwort (restic)"; export RESTIC_PASSWORD="$GEHEIM"; GEHEIM=""

  LEER="$(mktemp)"; trap 'rm -f "$LEER"' EXIT
  export RCLONE_CONFIG="$LEER"
  export RCLONE_CONFIG_NAS_TYPE=sftp RCLONE_CONFIG_NAS_HOST="$HOST" RCLONE_CONFIG_NAS_PORT="$PORT" RCLONE_CONFIG_NAS_USER="$BENUTZER"
  export RCLONE_CONFIG_NAS_KNOWN_HOSTS_FILE=none   # wie im Add-on ohne Host-Key-Prüfung, ohne Dauerhinweis
  RCLONE_CONFIG_NAS_PASS="$(printf '%s' "$BOXPW" | "$RCLONE" obscure -)"; export RCLONE_CONFIG_NAS_PASS; BOXPW=""
  export RESTIC_REPOSITORY="rclone:nas:$PFAD"

  echo; echo "Verbinde und lese die Sicherungsstände …"
  restic_ snapshots && break
  echo
  echo "Das hat nicht geklappt – Angaben bitte prüfen (Meldung oben)."
  echo "  · 'wrong password'           -> Archiv-Passwort falsch"
  echo "  · 'Is there a repository'    -> Pfad auf der Box falsch"
  echo "  · 'ssh: handshake failed'    -> Benutzer/Passwort der Box falsch oder IP gesperrt"
  janein "Nochmal versuchen?" || exit 1
done

NEU="$(printf 'HOST=%s\nPORT=%s\nBENUTZER=%s\nPFAD=%s' "$HOST" "$PORT" "$BENUTZER" "$PFAD")"
ALT="$( [ -f "$EINST" ] && tr -d '\r' < "$EINST" )"
if [ "$NEU" != "$ALT" ] && janein "Diese Angaben (ohne Passwörter) für das nächste Mal speichern?"; then
  printf '%s\n' "$NEU" > "$EINST"
fi

stand() { frage "Sicherungsstand (ID aus der Liste, Enter = neuester)" latest; STAND="$ANTWORT"; }
ziel() {
  frage "Zielordner" "$HOME/SimpleNAS-wiederhergestellt"; ZIEL="$ANTWORT"
  if [ -d "$ZIEL" ] && [ -n "$(ls -A "$ZIEL" 2>/dev/null)" ]; then
    janein "Der Ordner ist nicht leer – gleichnamige Dateien werden überschrieben. Trotzdem?" || return 1
  fi
  mkdir -p "$ZIEL"
}

while true; do
  echo
  echo "  [1] Sicherungsstände anzeigen"
  echo "  [2] Inhalt ansehen (ein Ordner)"
  echo "  [3] ALLES wiederherstellen"
  echo "  [4] Einen Ordner oder eine Datei wiederherstellen"
  echo "  [5] Archiv prüfen"
  echo "  [0] Beenden"
  read -r -p "Auswahl: " w
  case "$w" in
    1) restic_ snapshots ;;
    2) stand; frage "Ordner im Archiv" /; restic_ ls "$STAND" "$ANTWORT" ;;
    3) stand; ziel && restic_ restore "$STAND" --target "$ZIEL" && echo && echo "Fertig: $ZIEL" ;;
    4) stand; frage "Pfad im Archiv (wie unter [2] angezeigt, z. B. /share/Fotos/2024)" ""; P="$ANTWORT"
       [ -n "$P" ] && ziel && restic_ restore "$STAND" --target "$ZIEL" --include "$P" && echo && echo "Fertig: $ZIEL (mit dem vollen Pfad darunter)" ;;
    5) if janein "Auch alle Daten lesen (dauert lange, lädt das ganze Archiv herunter)?"; then restic_ check --read-data; else restic_ check; fi ;;
    0) exit 0 ;;
  esac
done
