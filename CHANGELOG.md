# Changelog

## 3.9.0
- **Collabora Online eingebunden: Office-Dateien auf der Freigabe-Seite im Browser oeffnen und bearbeiten.** Mit `collabora_enabled` und `collabora_url` zeigt die Freigabe-Seite bei docx, xlsx, pptx, odt und allen weiteren Typen, die der Collabora-Server meldet, einen Knopf zum Oeffnen. Bearbeiten und Speichern gibt es nur bei Links mit dem neuen Schalter **Bearbeiten mit Collabora erlauben** (Standard aus, nicht bei reinen Ablage-Links), alle anderen oeffnen schreibgeschuetzt. Gespeichert wird direkt in die Datei im Ordner; Besitzer und Rechte fuer Samba bleiben erhalten.
- Simple NAS ist dafuer WOPI-Host (`/wopi/files/...` auf dem Port der Freigabe-Seite): Zugangs-Tokens gelten nur fuer einen Link, eine Datei und ein Konto, laufen nach `share_session_hours` ab und werden bei jedem Aufruf erneut geprueft - ein deaktivierter Link, ein neuer Token, ein geaendertes Passwort oder ein gesperrtes Konto beenden auch eine offene Editor-Sitzung. Die Aufrufe muessen mit dem Proof-Key des eingestellten Collabora-Servers signiert sein (`collabora_verify_proof`, reines Python, kein zusaetzliches Paket). Sperren ueberleben einen Neustart, und eine Datei, die zwischendurch per Samba geaendert wurde, loest in Collabora eine Rueckfrage aus statt ueberschrieben zu werden.
- Nur die Editor-Seite darf den Collabora-Server einbetten; alle anderen Seiten behalten die strikte Content-Security-Policy.
- Neue Optionen fuer Sonderfaelle: `collabora_internal_url` (Dateitypen ueber eine interne Adresse abfragen, etwa wegen einer Cloudflare-Pruefung), `collabora_wopi_url` (Adresse, unter der Collabora Simple NAS erreicht), `collabora_verify_tls`. Im Reiter Teilen zeigt eine neue Zeile den Collabora-Status mit Test-Knopf.
- Bei einem geaenderten Ordner oder einer geaenderten Einzeldatei eines Links enden jetzt ebenfalls alle Sitzungen dieses Links.

## 3.8.1
- Fix zur Abschottung: In der Sandbox schrieb der Worker seine Zaehler und den Sperren-Schnappschuss nicht (das atomare Ersetzen scheiterte an den eingehaengten Einzeldateien), und Aenderungen der Verwaltung erreichten ihn nicht. Das Jail laesst `/data` jetzt echt und blendet nur die geheimen Dateien (admin_auth.json, options.json, samba/) einzeln aus. Sperren-Karte, Zaehler und Live-Aenderungen an Links funktionieren damit auch abgeschottet.

## 3.8.0
- **Die oeffentliche Freigabe-Seite laeuft jetzt abgeschottet (Plan-Schritt 9).** Sie ist ein eigener Prozess, der auf den unprivilegierten Nutzer `nobody` faellt, `SYS_ADMIN`/`SYS_RAWIO` und alle bis auf die fuer Uploads noetigen Datei-Rechte ablegt und in einem Mount-Namespace sitzt, in dem `/config`, `/ssl`, `/addon_configs`, `/backup` und die geheimen Dateien unter `/data` (Admin-Passwort, Samba-Passwortdatenbank, Backups) gar nicht vorhanden sind. Eine Codeausfuehrungsluecke im aus dem Internet erreichbaren Teil erreicht damit nur noch die freigegebenen Ordner, nicht mehr den Home-Assistant-Host. Neue Option `share_sandbox`: `auto` (Standard, mit Rueckfall auf den Hauptprozess, falls die Abschottung nicht einrichtbar ist), `on` (erzwingen) oder `off` (wie frueher). Die Verwaltung liest Sperren und Zugriffsprotokoll des Worker-Prozesses ueber kleine Dateien und reicht Entsperr-Wuensche genauso zurueck.
- **Fix (Sicherheit): ein gerade rotierter Token blieb kurz gueltig, ein gerade angelegter Link antwortete kurz mit 404.** Der Token-Index war auf die sekundengenaue Aenderungszeit der Datei zwischengespeichert; beim Erneuern (gleiche Dateigroesse, gleiche Sekunde) fiel die Aenderung durchs Raster. Der Index wird nun bei jeder Anfrage frisch aus der Datei gebaut - immer korrekt, auch zwischen Verwaltungs- und Worker-Prozess.

## 3.7.5
- **CrowdSec bekommt sein Protokoll auch dann, wenn `share_log_export_path` woanders hinzeigt.** Steht in der Option ein Ordner, den das CrowdSec-Addon nicht sieht (alles ausserhalb von `/config`, insbesondere `/share`), schreibt Simple NAS die Datei weiterhin dorthin *und* zusaetzlich nach `/config/.simplenas/share_access.log`, worauf die Acquisition zeigt. Vorher lief CrowdSec mit "No matching files for pattern" ins Leere. Der Reiter Teilen nennt jetzt die Datei, aus der CrowdSec liest, und weist darauf hin, dass die Option leer bleiben kann.
- Das Zugriffsprotokoll kann mehrere Ziele parallel bedienen (`accesslog.set_export(..., slot=...)`). Zwei Slots auf derselben Datei werden zusammengefasst, weil zwei rotierende Handler auf einem Pfad sich gegenseitig die Rotation zerlegen.
- Das Logo aus der Kopfzeile dient jetzt auch als Favicon im Browser-Tab (Oberflaeche, Login und Einrichtungsseite). Die oeffentliche Freigabe-Seite bekommt bewusst keines: ein wiedererkennbares Symbol wuerde verraten, welche Software dort antwortet.
- Die Auto-Sicherung nach `/config/.simplenas/auto` laeuft nur noch, wenn das Addon wirklich aus `/data` startet. Testlaeufe und Handstarts auf einem Arbeitsplatzrechner haben sonst in ein fremdes `/config` geschrieben.

## 3.7.4
- **Fix: abgewiesene Streams verloren das Urteil von clamd.** Ueberschreitet eine Datei `StreamMaxLength`, antwortet clamd sofort mit "INSTREAM size limit exceeded" und legt auf, waehrend das Addon noch sendet. Der darauf folgende Schreibfehler ueberschrieb die bereits eingetroffene Antwort mit einem allgemeinen Fehler - und mit `share_clamav_on_error: reject` wurde der Upload dann kommentarlos abgelehnt, statt ueber den Pfad-Scan doch noch geprueft zu werden. Der Client achtet jetzt nach jedem Block auf eine wartende Antwort, bricht dann ab und wertet sie aus. Nebeneffekt: grosse Dateien werden nicht mehr vollstaendig in einen Socket geschoben, der laengst nicht mehr gelesen wird.

## 3.7.3
- **Fix: CrowdSec sah das Zugriffsprotokoll nie.** Der Standardpfad lag unter `/share`, das in das CrowdSec-Addon gar nicht eingebunden ist - CrowdSec meldete beim Start "No matching files for pattern" und keines der Szenarien konnte je ausloesen. Neuer Standard ist `/config/.simplenas/share_access.log`; die Konfigurationsordner von Home Assistant sehen beide Addons. Bestehende Installationen ziehen beim Start automatisch um, die Acquisition-Datei wird dabei mitgeschrieben. Danach das CrowdSec-Addon einmal neu starten.
- **Sperren sind jetzt sichtbar.** Die Karte "Oeffentliche Freigabe-Seite" zeigt laufende Sperren mit Restzeit und die aktuellen Fehlversuche je Adresse im Verhaeltnis zur Schwelle (10 in 15 Minuten). Wer sich fuenfmal falsch anmeldet, sieht jetzt "ip 1.2.3.4: 5/10" statt gar nichts. Ein Klick auf eine Sperre hebt sie auf.
- **Warnung, wenn die Virenpruefung eingeschaltet, clamd aber nicht erreichbar ist.** Mit `share_clamav_on_error: reject` wird in dieser Lage jeder Upload abgelehnt, bisher ohne sichtbaren Hinweis. Der Status prueft clamd jetzt selbst (gecacht) und die Karte warnt in rot.

## 3.7.2
- "CrowdSec einrichten" braucht keine Option mehr: Ist `share_log_export_path` leer, schreibt das Addon das Zugriffsprotokoll ab sofort zusaetzlich nach `/share/simplenas/share_access.log`, merkt sich das in `/data/crowdsec_setup.json` und traegt genau diesen Pfad in die CrowdSec-Acquisition ein. Kein Neustart von Simple NAS noetig, nur das CrowdSec-Addon muss einmal neu starten. Eine gesetzte Option hat weiterhin Vorrang.

## 3.7.1
- Fix: "Speichern fehlgeschlagen: Missing option 'share_clamav_timeout'" beim Speichern der Addon-Konfiguration. Alle Freigabe- und Dateizugriffs-Optionen sind im Schema jetzt optional; das Addon hat fuer jede einen eingebauten Standardwert. Ein Konfigurationsformular, das noch vor einem Update geladen wurde, laesst sich damit speichern, ohne dass der Supervisor neue Optionen vermisst.

## 3.7.0
- **CrowdSec-Anbindung.** Das Addon bringt Parser, drei Szenarien und eine Acquisition-Datei fuer CrowdSec mit und installiert sie per Knopf im Reiter Teilen in die Konfiguration des CrowdSec-Addons (beide sehen `/config`). Szenarien: `simplenas/share-bf` (5 Fehlpasswoerter in ~50 s), `simplenas/share-scan` (10 unbekannte Links in ~5 min), `simplenas/share-locked` (das Addon hat die Adresse selbst gesperrt). Bans erreichen damit den Firewall-Bouncer.
- Neue Option `share_log_export_path`: zweite Kopie des Zugriffsprotokolls an einem Ort, den CrowdSec lesen kann, z. B. `/share/simplenas/share_access.log`.
- Jede Protokollzeile traegt zusaetzlich `time` als RFC3339-Zeitstempel, damit CrowdSec sie ohne eigene Zeitlogik verarbeiten kann.
- DOCS: Abschnitt zur CrowdSec-Einrichtung, inklusive des Hinweises, dass Bans fuer den Cloudflare-Tunnel ueber den Cloudflare-Bouncer laufen muessen.

## 3.6.1
- Uploads ueber Links mit Modus "Beides" landen jetzt direkt in dem Ordner, in dem man gerade blaettert, statt in einem Unterordner mit Datum. Reine Ablage-Links sortieren weiterhin nach Tag.
- Die Ablageart ist im Link-Dialog waehlbar: direkt im Ordner, Unterordner je Tag, Unterordner je Konto.
- Nach dem Hochladen zeigt die Seite den Zielpfad an und laedt die Dateiliste neu.

## 3.6.0
- **Virenpruefung beim Upload ueber das ClamAV-Addon.** `share_clamav_enabled: true`, clamd unter `share_clamav_host:share_clamav_port` (Vorgabe 127.0.0.1:3310, TCP-Socket im ClamAV-Addon einschalten). Uploads werden vor dem Ablegen gestreamt geprueft; Funde werden abgewiesen und protokolliert. Ist clamd nicht erreichbar, wird abgelehnt (`share_clamav_on_error`, umstellbar). Dateien ueber clamds Stromgrenze (25 MB) werden per Pfad-Modus geprueft; scheitert auch das, entscheidet `share_clamav_large_file`. Im Reiter Teilen gibt es einen Test-Knopf: PING plus EICAR-Testdatei.
- **Schutz gegen Passwortraten verschaerft.** Sperren eskalieren: 15 Minuten beim ersten Mal, dann 30, 60, 120 ... bis 24 Stunden; nach einem Tag Ruhe beginnt die Reihe von vorn. Gilt je Adresse, je Link und je Konto, auch fuer das Portal.
- **Link-Scanner werden ausgesperrt:** Wer in zehn Minuten zwanzig unbekannte Links probiert, wird fuer 15 Minuten (eskalierend) komplett blockiert.
- **Link-Passwoerter brauchen mindestens 8 Zeichen.** Bisher war jede nicht-leere Eingabe erlaubt; ein vierstelliger Code waere trotz Sperren in Tagen zu erraten gewesen.

## 3.5.1
- **Hochladen aus dem Internet.** Links mit Modus "Hochladen" oder "Beides" zeigen auf der Freigabe-Seite einen Ablagebereich: Dateien hineinziehen oder auswaehlen, Fortschritt je Datei, Ergebnis mit endgueltigem Dateinamen. Ohne Javascript gibt es ein einfaches Formular.
- Dateinamen werden entschaerft, ohne Umlaute zu zerstoeren: `Grüße.pdf` bleibt `Grüße.pdf`. Verzeichnisanteile, fuehrende Punkte, Steuerzeichen und Windows-Geraetenamen werden entfernt.
- Nichts wird ueberschrieben: aus `a.txt` wird `a (2).txt`, angelegt mit exklusivem Erzeugen, damit dazwischen kein Symlink untergeschoben werden kann.
- Gesperrte Endungen (`share_upload_blocked_ext`, Vorgabe: ausfuehrbare Dateien sowie HTML und SVG), optional eine Positivliste (`share_upload_allowed_ext`). Jede Endung der Kette zaehlt, `x.html.txt` ist gesperrt.
- Groessenpruefung vor dem ersten gelesenen Byte gegen `share_max_upload_mb` (Vorgabe 1024) und die Grenzen des Links. Upload-Kontingent je Link, 30 Uploads je Stunde und Adresse.
- Ablage nach Datum (`upload_subdir: by-date`, Vorgabe) haelt den Ordner uebersichtlich; hochgeladene Dateien gehoeren dem Ordner-Eigentuemer, damit Samba-Benutzer sie verwalten koennen.
- Vorbereitet: ein Pruef-Haken fuer die Virenpruefung; die ClamAV-Anbindung folgt im naechsten Release.
- (3.5.0 wurde ohne Versionssprung veroeffentlicht; dieses Release traegt die Aenderungen nach.)

## 3.4.3
- **Portal auf der Startseite der Freigabe-Seite.** Unter `/` steht jetzt eine Anmeldung mit dem Freigabe-Konto. Danach erscheint "Meine Freigaben": alle aktiven Links der Art "Nur bestimmte Konten", die dieses Konto oeffnen darf. Ohne Anmeldung verraet die Seite weiterhin nichts, weder Namen noch Adressen. Die Portal-Anmeldung gilt zugleich fuer die einzelnen Links.
- Fehlversuche am Portal zaehlen gegen dieselben Sperren wie an den Links (je Adresse und je Konto).
- **Echte Besucheradresse hinter Cloudflare.** Durch einen Cloudflare-Tunnel kam bisher nur die Adresse des Tunnel-Containers an; alle Besucher aus dem Internet teilten sich damit ein Anfragekontingent. Jetzt wird `CF-Connecting-IP` beachtet, aber nur von vertrauenswuerdigen Proxys.
- Falsche HTTP-Methode auf bekannten Pfaden liefert dieselbe "nicht gefunden"-Seite statt einer Fehlerseite.

## 3.4.2
- Start der Freigabe-Seite prueft, ob `share_port` frei ist, und meldet sonst klar im Log, dass ein anderes Addon den Port belegt, statt es stillschweigend zu verdraengen.
- Dokumentation und Optionstext zu `share_bind` korrigiert: Laeuft der Nginx Proxy Manager als Addon, muss `0.0.0.0` bleiben, denn dieses Addon erreicht das Loopback des Hosts nicht. Im Proxy-Host die LAN-IP des Hosts, Schema http und den Freigabe-Port eintragen. Ein Hinweis dazu erscheint auch im Addon-Log.

## 3.4.1
- **Ordner als ZIP herunterladen**, auf der Freigabe-Seite (Knopf ueber der Dateiliste, auch in Unterordnern) und im Dateimanager (neues Symbol an jedem Ordner). Das Archiv wird waehrend der Uebertragung gepackt, ohne Zwischendatei auf der Platte.
- Vor dem ersten Byte wird der Ordner vermessen; ueber `share_zip_max_gb` (Vorgabe 5) oder `share_zip_max_files` (Vorgabe 10000) gibt es eine ordentliche Fehlerseite statt eines abgebrochenen Downloads.
- Verschwindet eine Datei waehrend des Packens, bleibt das Archiv gueltig; sie steht in `_MISSING.txt`. Waechst der Ordner deutlich ueber die Messung hinaus, wird sauber geschlossen und `ZIP-INCOMPLETE.txt` erklaert es. Ein kurzes, gueltiges ZIP ist besser als ein abgeschnittenes.
- Versteckte Dateien, `.part`-Dateien und Symlinks landen nie im Archiv. Standard ist Packen ohne Komprimierung (`share_zip_compress`), Fotos und Videos werden ohnehin nicht kleiner.
- Jeder ZIP-Abruf zaehlt als ein Download und steht im Zugriffsprotokoll.

## 3.4.0
- **Die oeffentliche Freigabe-Seite ist da.** Eine zweite, eigenstaendige Anwendung auf Port 8101 (Option `share_port`), auf der ausschliesslich `/s/<link>` existiert. Verwaltung, Dateimanager und Samba-Einstellungen sind dort nicht erreichbar; das Addon verweigert den Start, sollte dort je etwas anderes registriert sein.
- Sie startet nur mit `sharing_enabled: true` **und** gesetztem Admin-Passwort. run.sh und app.py pruefen das unabhaengig voneinander.
- Links mit Passwort, Links fuer bestimmte Konten und offene Links; Blaettern in Unterordnern, Download mit Fortsetzen, Vorschau nur fuer Bilder, Video, Audio, PDF und Text. HTML und SVG werden nie inline ausgeliefert.
- Unbekannte, deaktivierte, abgelaufene und erschoepfte Links sowie Links auf nicht eingehaengte Ordner zeigen dieselbe, byteweise identische Seite.
- Sperren gegen Passwortraten: je IP (10 Fehlversuche in 15 Minuten), je Link (20, auch ueber viele Adressen verteilt), je Konto (10), 240 Anfragen je Minute und IP. Gesperrte Links zeigen im Reiter Teilen ein Etikett mit Entsperr-Knopf.
- Zugriffsprotokoll `/data/share_access.log` (JSON-Zeilen, rotierend) mit Ansicht und Filtern im Reiter Teilen. Es enthaelt Link-IDs und relative Pfade, nie Tokens oder absolute Pfade.
- Strenge Antwortkopfzeilen (CSP, X-Frame-Options, nosniff, Referrer-Policy, HSTS bei HTTPS), eigener Sitzungsschluessel und Cookie-Name, `X-Forwarded-*` nur von `share_trusted_proxies`.
- Neue Optionen: `share_trusted_proxies`, `share_cookie_secure`, `share_session_hours`, `share_log_max_mb`. Kapitel "Sharing files on the internet" in DOCS.md mit der Einrichtung im Nginx Proxy Manager.
- Noch nicht enthalten: Hochladen (Ablage-Links zeigen einen Hinweis) und Ordner als ZIP. Beides folgt.

## 3.3.0
- **Neuer Reiter "Teilen":** Links und Freigabe-Konten anlegen, bearbeiten, loeschen. Jeder Link hat einen Modus (Herunterladen, Hochladen, beides), eine Zugriffsart (Link + Passwort, nur bestimmte Konten, jeder mit dem Link), optional Ablaufdatum, Download-Limit, Upload-Kontingent und maximale Dateigroesse. Ein Link kann auch eine einzelne Datei sein.
- Im Dateien-Reiter gibt es je Zeile ein Teilen-Symbol, das den Link-Dialog vorbefuellt.
- Links bekommen ein 22-stelliges Kennwort aus einem Alphabet ohne verwechselbare Zeichen. "Neuen Link erzeugen" macht den alten sofort ungueltig. Aendert sich Passwort, Zugriffsart oder Kontenliste, werden laufende Sitzungen des Links ungueltig.
- Freigabe-Konten sind eigenstaendig und beruehren die Samba-Benutzer nicht. Die Datei mit den Passwort-Hashes ist nur fuer root lesbar.
- Wird eine Samba-Freigabe geloescht, werden Links darauf deaktiviert statt still auf ein neues Ziel zu zeigen.
- Neue Optionen: `sharing_enabled`, `share_port`, `share_bind`, `share_public_url`, `share_allowed_roots`. **Die oeffentliche Freigabe-Seite selbst kommt mit dem naechsten Release.** Links lassen sich schon anlegen, sind aber noch nicht von aussen erreichbar.
- Freigabe-Links, -Konten und die Dateizugriff-Einstellung sind in die reinstall-sichere Sicherung aufgenommen.

## 3.2.4
- Fix: 3.2.3 startete nicht (`NameError: name 'serve' is not defined`). Die neue Startfunktion stand unterhalb des Startblocks und war beim Aufruf noch nicht definiert. Verschoben, dazu ein Test, der die Reihenfolge im Quelltext prueft.

## 3.2.3
- **Webserver:** Die Oberflaeche laeuft jetzt auf waitress, einem fuer den Dauerbetrieb gedachten WSGI-Server, statt auf Flasks Entwicklungsserver. Mehrere Anfragen gleichzeitig blockieren sich nicht mehr, der Server verraet seinen Namen nicht mehr im Antwortkopf. Sollte Ingress auf deinem Geraet Probleme machen: Umgebungsvariable `WEB_SERVER=werkzeug` schaltet fuer dieses Release auf den alten Server zurueck.
- Hochgeladene Daten werden vor der Verarbeitung nach `/data/tmp` gepuffert statt nach `/tmp`, das auf Home Assistant OS im Arbeitsspeicher liegt.
- Obergrenze pro Anfrage von 10 GB auf 4 GB gesenkt. Der Puffer liegt auf der Datenpartition, ein einzelner Upload darf sie nicht fuellen.

## 3.2.2
- Fix: "Zugriff auf alle Dateien" liess sich einschalten, der Dateimanager kam aber nicht hinein. Bei "/" als freigegebener Wurzel zeigte die Wurzelansicht einen einzigen Eintrag "/", der wieder auf sich selbst zeigte. Jetzt wird bei vollem Zugriff das echte Wurzelverzeichnis gelistet, die Pfadleiste hat nur einen "/"-Eintrag und die Aufwaerts-Zeile verschwindet ganz oben.

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

