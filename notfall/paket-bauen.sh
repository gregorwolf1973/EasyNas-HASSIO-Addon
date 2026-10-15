#!/usr/bin/env bash
# ============================================================
#  Baut das Notfall-Paket: SimpleNAS-Notfall-<datum>.zip
#
#  Inhalt: die Wiederherstellungs-Skripte plus restic und rclone fuer
#  Windows (x64) und Linux (x64, mit --arm64 auch ARM64), damit im
#  Ernstfall nichts installiert oder heruntergeladen werden muss.
#  Die Programme kommen von den offiziellen GitHub-Releases und werden
#  gegen deren SHA256SUMS geprueft.
#
#  Aufruf:  bash notfall/paket-bauen.sh [--arm64] [restic-version] [rclone-version]
#           ohne Versionen: jeweils das neueste Release
# ============================================================
set -euo pipefail

HIER="$(cd "$(dirname "$0")" && pwd)"
ARM64=0
if [ "${1:-}" = "--arm64" ]; then ARM64=1; shift; fi

neuestes() {   # erst ganz lesen: grep -m1 in der Pipe ließe curl mit pipefail scheitern
  local j v; j="$(curl -fsSL "https://api.github.com/repos/$1/releases/latest")"
  # Nur das Feld selbst herausschneiden: die API liefert das JSON mal mehrzeilig,
  # mal in EINER Zeile – ein Muster über die ganze Zeile griff dann ein anderes Feld.
  v="$(grep -o '"tag_name": *"[^"]*"' <<<"$j" | head -1 | sed -E 's/.*"v?([^"]+)"$/\1/')"
  [[ "$v" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || { echo "Neueste Version von $1 nicht lesbar ('$v') – Version beim Aufruf angeben." >&2; exit 1; }
  echo "$v"
}
RESTIC_V="${1:-$(neuestes restic/restic)}"
RCLONE_V="${2:-$(neuestes rclone/rclone)}"
echo "restic $RESTIC_V, rclone $RCLONE_V"

CACHE="$HIER/.cache"
BAU="$HIER/.bau/SimpleNAS-Notfall"
AUS="$HIER/dist"
ZIP="$AUS/SimpleNAS-Notfall-$(date +%Y-%m-%d).zip"
mkdir -p "$CACHE" "$AUS"
rm -rf "$HIER/.bau"
rm -f "$ZIP"   # gleich zu Beginn: ein abgebrochener Lauf soll kein altes ZIP als Ergebnis zurücklassen
mkdir -p "$BAU/programme"

laden() {   # url datei
  [ -s "$CACHE/$2" ] || { echo "  lade $2"; curl -fsSL -o "$CACHE/$2.tmp" "$1" && mv "$CACHE/$2.tmp" "$CACHE/$2"; }
}
pruefen() { # summen-datei datei
  local soll ist
  soll="$(grep -E "[ *]$2\$" "$CACHE/$1" | head -1 | cut -d' ' -f1)"
  ist="$(sha256sum "$CACHE/$2" | cut -d' ' -f1)"
  [ -n "$soll" ] && [ "$soll" = "$ist" ] || { echo "PRUEFSUMME FALSCH: $2 (soll '$soll', ist '$ist')" >&2; rm -f "$CACHE/$2"; exit 1; }
}

RS="https://github.com/restic/restic/releases/download/v$RESTIC_V"
RC="https://downloads.rclone.org/v$RCLONE_V"
laden "$RS/SHA256SUMS" "restic-$RESTIC_V-SHA256SUMS"
laden "$RC/SHA256SUMS" "rclone-$RCLONE_V-SHA256SUMS"

ZIELE="windows:amd64 linux:amd64"
[ $ARM64 = 1 ] && ZIELE="$ZIELE linux:arm64"
TMP="$HIER/.bau/tmp"
for z in $ZIELE; do
  os="${z%%:*}"; arch="${z##*:}"
  ordner="$BAU/programme/$os-$arch"
  mkdir -p "$ordner" "$TMP"
  if [ "$os" = windows ]; then
    f="restic_${RESTIC_V}_windows_${arch}.zip"; laden "$RS/$f" "$f"; pruefen "restic-$RESTIC_V-SHA256SUMS" "$f"
    unzip -q -o "$CACHE/$f" -d "$TMP"; mv "$TMP"/restic_*.exe "$ordner/restic.exe"
    exe=.exe
  else
    f="restic_${RESTIC_V}_linux_${arch}.bz2"; laden "$RS/$f" "$f"; pruefen "restic-$RESTIC_V-SHA256SUMS" "$f"
    bzip2 -dc "$CACHE/$f" > "$ordner/restic"
    exe=
  fi
  f="rclone-v${RCLONE_V}-${os}-${arch}.zip"; laden "$RC/$f" "$f"; pruefen "rclone-$RCLONE_V-SHA256SUMS" "$f"
  unzip -q -o "$CACHE/$f" -d "$TMP"; mv "$TMP/rclone-v${RCLONE_V}-${os}-${arch}/rclone$exe" "$ordner/rclone$exe"
  chmod +x "$ordner"/* 2>/dev/null || true
  rm -rf "$TMP"
done

# Windows-Dateien mit CRLF ins Paket – im Repository stehen sie mit LF, und
# ein unter Linux gebautes Paket bekäme sonst LF-Dateien für cmd.exe/Notepad.
crlf() { sed 's/\r*$/\r/' "$1" > "$2"; }
crlf "$HIER/WIEDERHERSTELLEN-Windows.cmd" "$BAU/WIEDERHERSTELLEN-Windows.cmd"
crlf "$HIER/LIESMICH.txt" "$BAU/LIESMICH.txt"
crlf "$HIER/wiederherstellen.ps1" "$BAU/programme/wiederherstellen.ps1"
tr -d '\r' < "$HIER/wiederherstellen-linux.sh" > "$BAU/wiederherstellen-linux.sh"   # bash verträgt kein CR
chmod +x "$BAU/wiederherstellen-linux.sh"
printf 'restic %s\r\nrclone %s\r\ngebaut %s\r\n' "$RESTIC_V" "$RCLONE_V" "$(date +%Y-%m-%d)" > "$BAU/programme/VERSIONEN.txt"

if command -v zip >/dev/null; then
  (cd "$HIER/.bau" && zip -q -r -9 "$ZIP" SimpleNAS-Notfall)
elif [ -x /c/Windows/System32/tar.exe ]; then
  (cd "$HIER/.bau" && /c/Windows/System32/tar.exe -a -c -f "$(cygpath -w "$ZIP")" SimpleNAS-Notfall)
else
  echo "Weder zip noch Windows-tar gefunden - der Ordner liegt fertig unter $BAU" >&2; exit 1
fi
rm -rf "$HIER/.bau"
echo "fertig: $ZIP ($(du -h "$ZIP" | cut -f1))"
