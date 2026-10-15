# ============================================================
#  Simple NAS – Notfall-Wiederherstellung (Windows)
#
#  Holt Daten aus dem restic-Archiv auf der Hetzner Storage Box zurück,
#  ohne Home Assistant. Der Weg ist derselbe wie im Add-on: restic spricht
#  die Box über rclone (SFTP) an.
#
#  Passwörter werden nur eingetippt und nirgends gespeichert: rclone und
#  restic bekommen sie über Umgebungsvariablen dieses Prozesses.
#  Gestartet wird über WIEDERHERSTELLEN-Windows.cmd.
# ============================================================
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "Simple NAS – Notfall-Wiederherstellung"

$hier   = Split-Path -Parent $MyInvocation.MyCommand.Path
$paket  = Split-Path -Parent $hier
$bin    = Join-Path $hier "windows-amd64"
$restic = Join-Path $bin "restic.exe"
$rclone = Join-Path $bin "rclone.exe"
$einst  = Join-Path $paket "einstellungen.txt"

foreach ($p in $restic, $rclone) {
  if (-not (Test-Path $p)) { Write-Host "Programm fehlt: $p" -ForegroundColor Red; exit 1 }
  try { Unblock-File $p } catch {}
}

function Frage($text, $vorgabe) {
  $a = Read-Host $(if ($vorgabe) { "$text [$vorgabe]" } else { $text })
  if ([string]::IsNullOrWhiteSpace($a)) { return $vorgabe } else { return $a.Trim() }
}
function Geheim($text) {
  $s = Read-Host $text -AsSecureString
  $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}
# Passwort für rclone „verschleiern" (rclone obscure). Über die Standardeingabe,
# damit es nicht in der Prozessliste steht. Vorsicht BOM: die PowerShell-Pipe
# und .NET (Eingabestrom mit Console.InputEncoding) setzen je nach Rechner eine
# UTF-8-BOM vor das Passwort – rclone nähme sie als Teil des Passworts.
# Daher Eingabe-Kodierung ohne BOM und die Bytes selbst schreiben.
function RcloneMitEingabe($argumente, $eingabe) {
  $utf8 = New-Object Text.UTF8Encoding $false
  [Console]::InputEncoding = $utf8
  $psi = New-Object Diagnostics.ProcessStartInfo $rclone, $argumente
  $psi.UseShellExecute = $false; $psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true
  $psi.StandardOutputEncoding = $utf8
  $p = [Diagnostics.Process]::Start($psi)
  $bytes = $utf8.GetBytes($eingabe)
  $p.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length); $p.StandardInput.Close()
  $aus = $p.StandardOutput.ReadToEnd(); $p.WaitForExit()
  if ($p.ExitCode -ne 0) { throw "rclone $argumente fehlgeschlagen" }
  return $aus.TrimEnd("`r", "`n")
}
function Verschleiern($geheim) {
  $aus = (RcloneMitEingabe "obscure -" $geheim).Trim()
  # Gegenprobe: kommt beim Zurückrechnen nicht genau das Eingetippte heraus,
  # hätte die Box ein falsches Passwort bekommen – lieber hier anhalten.
  if (-not $aus -or (RcloneMitEingabe "reveal $aus" "") -cne $geheim) { throw "Das Box-Passwort ließ sich nicht korrekt an rclone übergeben." }
  return $aus
}
function JaNein($text) { return (Read-Host "$text [j/N]") -match '^\s*[jJyY]' }
# restic startet rclone selbst und sucht es im PATH. NICHT über
# -o rclone.program: restic zerlegt den Wert wie eine Kommandozeile, und
# Backslashes oder Leerzeichen im Pfad („C:\Users\Gregor Wolf\…") gehen kaputt.
$env:PATH = "$bin;$env:PATH"
# Ausgabe direkt auf die Konsole (Fortschrittsanzeige); Ergebnis in $LASTEXITCODE
function Restic { & $restic @args }

# ---------- Angaben (ohne Passwörter) aus einstellungen.txt ----------
$e = @{ HOST = ""; PORT = "22"; BENUTZER = ""; PFAD = "simplenas" }
if (Test-Path $einst) {
  foreach ($z in Get-Content $einst -Encoding UTF8) {
    if ($z -match '^\s*([A-Z]+)\s*=\s*(.*?)\s*$' -and $e.ContainsKey($Matches[1])) { $e[$Matches[1]] = $Matches[2] }
  }
}

Write-Host ""
Write-Host "  Simple NAS – Notfall-Wiederherstellung" -ForegroundColor Cyan
Write-Host "  Daten aus dem restic-Archiv auf der Hetzner Storage Box zurückholen"
Write-Host "  (Enter übernimmt den Wert in eckigen Klammern)"
Write-Host ""

while ($true) {
  $e.HOST     = Frage "Storage Box (z. B. u123456.your-storagebox.de)" $e.HOST
  $e.BENUTZER = Frage "Benutzer (z. B. u123456)" $(if ($e.BENUTZER) { $e.BENUTZER } elseif ($e.HOST -match '^(u\d+)') { $Matches[1] } else { "" })
  $e.PORT     = Frage "Port" $e.PORT
  $e.PFAD     = Frage "Pfad auf der Box (im Add-on: Zielpfad)" $e.PFAD
  $boxPw      = Geheim "Passwort der Storage Box"
  $archivPw   = Geheim "Archiv-Passwort (restic)"

  $env:RCLONE_CONFIG = Join-Path $env:TEMP "simplenas-notfall-leer.conf"
  Set-Content -Path $env:RCLONE_CONFIG -Value "" -Encoding ASCII
  $env:RCLONE_CONFIG_NAS_TYPE = "sftp"
  $env:RCLONE_CONFIG_NAS_HOST = $e.HOST
  $env:RCLONE_CONFIG_NAS_PORT = $e.PORT
  $env:RCLONE_CONFIG_NAS_USER = $e.BENUTZER
  $env:RCLONE_CONFIG_NAS_KNOWN_HOSTS_FILE = "none"   # wie im Add-on ohne Host-Key-Prüfung, ohne Dauerhinweis
  $env:RCLONE_CONFIG_NAS_PASS = Verschleiern $boxPw
  $env:RESTIC_REPOSITORY = "rclone:nas:" + $e.PFAD
  $env:RESTIC_PASSWORD = $archivPw
  Remove-Variable boxPw, archivPw

  Write-Host "`nVerbinde und lese die Sicherungsstände …" -ForegroundColor Cyan
  Restic snapshots
  if ($LASTEXITCODE -eq 0) { break }
  Write-Host "`nDas hat nicht geklappt – Angaben bitte prüfen (Meldung oben)." -ForegroundColor Yellow
  Write-Host "  · 'wrong password'           -> Archiv-Passwort falsch"
  Write-Host "  · 'Is there a repository'    -> Pfad auf der Box falsch"
  Write-Host "  · 'ssh: handshake failed'    -> Benutzer/Passwort der Box falsch oder IP gesperrt"
  if (-not (JaNein "Nochmal versuchen?")) { exit 1 }
}

$neu = "HOST=$($e.HOST)`r`nPORT=$($e.PORT)`r`nBENUTZER=$($e.BENUTZER)`r`nPFAD=$($e.PFAD)`r`n"
$alt = if (Test-Path $einst) { (Get-Content $einst -Raw -Encoding UTF8) } else { "" }
if ($neu -ne $alt -and (JaNein "Diese Angaben (ohne Passwörter) für das nächste Mal speichern?")) {
  Set-Content -Path $einst -Value $neu.TrimEnd() -Encoding UTF8
}

function Stand { return Frage "Sicherungsstand (ID aus der Liste, Enter = neuester)" "latest" }
function Ziel {
  $z = Frage "Zielordner" (Join-Path ([Environment]::GetFolderPath("Desktop")) "SimpleNAS-wiederhergestellt")
  if ((Test-Path $z) -and (Get-ChildItem $z -Force | Select-Object -First 1)) {
    if (-not (JaNein "Der Ordner ist nicht leer – gleichnamige Dateien werden überschrieben. Trotzdem?")) { return $null }
  }
  New-Item -ItemType Directory -Force -Path $z | Out-Null
  return $z
}

while ($true) {
  Write-Host ""
  Write-Host "  [1] Sicherungsstände anzeigen"
  Write-Host "  [2] Inhalt ansehen (ein Ordner)"
  Write-Host "  [3] ALLES wiederherstellen"
  Write-Host "  [4] Einen Ordner oder eine Datei wiederherstellen"
  Write-Host "  [5] Archiv prüfen"
  Write-Host "  [0] Beenden"
  switch (Read-Host "Auswahl") {
    "1" { Restic snapshots }
    "2" {
      $s = Stand; $p = Frage "Ordner im Archiv" "/"
      Restic ls $s $p
    }
    "3" {
      $s = Stand; $z = Ziel
      if ($z) {
        Restic restore $s --target $z
        if ($LASTEXITCODE -eq 0) { Write-Host "`nFertig: $z" -ForegroundColor Green; Invoke-Item $z }
      }
    }
    "4" {
      $s = Stand
      $p = Frage "Pfad im Archiv (wie unter [2] angezeigt, z. B. /share/Fotos/2024)" ""
      if ($p) {
        $z = Ziel
        if ($z) {
          Restic restore $s --target $z --include $p
          if ($LASTEXITCODE -eq 0) { Write-Host "`nFertig: $z (mit dem vollen Pfad darunter)" -ForegroundColor Green; Invoke-Item $z }
        }
      }
    }
    "5" {
      if (JaNein "Auch alle Daten lesen (dauert lange, lädt das ganze Archiv herunter)?") { Restic check --read-data } else { Restic check }
    }
    "0" { exit 0 }
  }
}
