# Changelog

## 3.9.1
- **Fix: Der Download-Knopf im Dateimanager tat nichts (seit 3.0.9).** Der Regex `/\/+$/` stand in einem JavaScript-String fuer das `onclick`-Attribut; dort verliert er die Backslashes, das Attribut enthielt `//+$/` - ein Kommentar - und der Handler brach mit einem SyntaxError ab, ohne eine Anfrage zu senden. Der Knopf ruft jetzt eine benannte Funktion auf.
- **Loeschen auf der Freigabe-Seite.** Neuer Link-Schalter **Loeschen erlauben** (Standard aus): Besucher sehen dann bei Dateien und Ordnern einen Loeschknopf und muessen vorher bestaetigen. Nicht bei Ablage- und Einzeldatei-Links, nie die Link-Wurzel selbst, keine versteckten Dateien oder Symlinks, nur mit CSRF-Token. Eine gerade in Collabora geoeffnete Datei (oder ein Ordner mit einer solchen) wird nicht geloescht. Jeder Loeschvorgang steht im Zugriffsprotokoll.
- **Fix: Ablage hochgeladener Dateien.** Links ohne gespeicherte Ablage-Einstellung (vor deren Einfuehrung angelegt) zeigten im Dialog "Direkt im Ordner", der Server legte aber Tagesordner an. Beide nutzen jetzt dieselbe Vorgabe. "Unterordner je Konto" wurde bei Passwort- und oeffentlichen Links stillschweigend ignoriert; der Dialog lehnt diese Kombination jetzt mit einer Meldung ab.
- **Fix: CrowdSec stand nach jedem Neustart von Simple NAS auf "nicht aktiv".** Bei abgeschotteter Freigabe-Seite schreibt der Worker das Protokoll und ein Kopier-Thread reicht es an CrowdSec weiter. Die Statusanzeige fragte aber nur, ob der Hauptprozess selbst in die Datei schreibt - das ist dort nie der Fall. Das Weiterreichen lief also, nur die Anzeige verlangte einen Klick auf "CrowdSec einrichten". Die Anzeige erkennt den Kopier-Thread jetzt; der Knopf richtet im abgeschotteten Betrieb den Kopier-Thread aus, statt einen Protokoll-Handler anzulegen, der nie eine Zeile bekommt, und startet keinen zweiten.

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

## 3.1.11
- Fix: add-on could fail to build/install because `hd-idle` (added in 3.1.10) was in the main `apk add` line — if the package is missing from the base image repos, the whole build aborted
- `hd-idle` is now installed in a separate, non-fatal step (tries default repos, then edge/community, otherwise continues) — the spindown feature degrades gracefully if the package is unavailable

## 3.1.10
- New: HDD spindown via `hdd_idle_seconds` option — mounted drives spin down after N seconds of inactivity to save power and reduce noise/wear (0 = disabled, default)
- Uses I/O-based `hd-idle` (watches `/proc/diskstats`), which works on USB-SATA bridges where `hdparm -S` does not
- Safe by design: hd-idle runs with a global `-i 0` default and is only pointed at the base disks of drives listed in `mounts.json` — the HA system disk is never spun down
- Added `hd-idle` to the Dockerfile and `hd_idle_args.py` helper that maps mounted devices to their base disks (e.g. `sdb1 → sdb`)

## 3.1.9
- Fix (#8): share creation crashed with `PermissionError: Operation not permitted` on exFAT/FAT32 drives — these filesystems have no POSIX permission model, so `os.chmod()` fails. New `ensure_share_dir()` helper tolerates the failure and creates the share anyway (permissions are irrelevant on those filesystems)
- Fix (#7): macOS Finder / iOS Files app now show correct filenames and metadata — added `vfs objects = catia fruit streams_xattr` plus fruit tuning to the global Samba config (harmless for Windows/Linux clients)
- Fix (#7): mobile web-GUI file list reflows into a 3-row grid below 600 px so long file/folder names no longer push size/date/action buttons off-screen
- Fix: clear diagnosis for the #1 mount failure — `mount: permission denied (are you root?)` / `Operation not permitted`. This is not a filesystem problem but the result of **Protection Mode still being enabled**, which makes HA strip the add-on's `CAP_SYS_ADMIN`
- Mount helper now detects permission errors, logs `CapEff` (all-zero = no capabilities) and prints a clear instruction: turn OFF Protection Mode in the add-on Info tab and restart
- The GUI error message now says exactly this instead of showing a cryptic mount error
- DOCS.md: new prominent "disable Protection Mode" section + troubleshooting entry

## 3.1.7
- Fix: NTFS drives failed to mount with `Failed to create '/dev/fuse': Read-only file system` / `ntfs-3g-mount: fuse device is missing` — HA OS add-on containers expose no `/dev/fuse`, so the userspace ntfs-3g driver cannot run
- Mount helper now uses the in-kernel `ntfs3` driver (Linux 5.15+, no FUSE required) and only falls back to `ntfs-3g` if the kernel driver is unavailable — applies to explicit NTFS selection, auto-detection and the brute-force loop
- `fuseblk` (how `/proc/mounts` reports old ntfs-3g mounts) is now recognized on restore and normalized to the `ntfs` tag in the drive list

## 3.1.6
- Safety: migration step in `run.sh` now strips any leftover bind-mount entries / fields from `/data/mounts.json` on startup — closes a remaining footgun from the removed 3.0.38–3.0.47 bind-mount feature where `rm -rf /share/<name>` could recurse through a still-active bind and wipe the underlying drive
- DOCS.md: prominent warning section for users upgrading from 3.0.38–3.0.47 with safety instructions (use `rmdir` instead of `rm -rf`, verify with `mount | grep`)

## 3.1.5
- New: Warning banner across all tabs when Home Assistant's add-on "Protection mode" (gesicherter Modus) is active — explains that drive mounting/management is blocked and points to the fix
- Detection via `CapEff` in `/proc/self/status` (CAP_SYS_ADMIN bit); reported by `/api/status` as `protection_mode`

## 3.1.4
- New: Drive health (SMART) card on the Overview page lists every disk with a colored status dot — click an entry to open the full SMART modal
- Overview card layout is now consistent: every card uses the same flex-column structure with the action / progress area always pinned to the bottom (e.g. CPU usage bar, RAM bar, Disk bar, Samba restart button, SMART refresh button all align on the same baseline)
- Overview SMART data is cached for 5 minutes to keep page loads snappy; a "Refresh" button forces a re-query

## 3.1.3
- New: SMART status modal for every drive in the Drives tab
- Pulse-icon button next to each drive opens a popup with health status (PASSED / FAILED), model, serial, firmware, capacity, drive type (SSD vs RPM), temperature, power-on hours, power-cycle count, ATA error count, and a curated table of SMART attributes
- NVMe drives show wear used, available spare, media errors, unsafe shutdowns
- Backend uses `smartctl --json` with automatic fallback through several USB-bridge modes (`sat`, `scsi`, `usbjmicron`, `usbprolific`, `usbsunplus`) so most USB enclosures work too
- Cleanly degrades to a "SMART data not available" hint when the USB bridge filters SMART commands
- Dockerfile: `smartmontools` added

## 3.1.2
- New: double-click a file in the Files tab to preview it in a new browser tab — PDFs use the browser's built-in viewer, images / videos / audio play inline, text files open in the existing editor
- New backend endpoint `/api/files/view` serves files inline with proper MIME type and HTTP Range support (videos/audio are seekable without full download)
- Files that the browser can preview show a pointer cursor and a "Double-click to open" tooltip; everything else still uses the Download button as before

## 3.1.1
- Fix: **"Error: Partition(s) on /dev/sdX are being used"** when initializing a partition table
  - Server: pre-checks `/proc/mounts` and returns a clear "Unmount first: …" message listing exactly which partition + mountpoint is blocking, instead of forwarding parted's terse error
  - UI: when opening the Initialize-partition-table modal, mounted partitions are listed up-front and the Initialize button is hard-disabled (typing the confirm string won't bypass it)

## 3.1.0
- Fix: disk rows no longer say "not mounted" — disks themselves are never mounted, only their partitions are. Disk rows now show the partition count instead (e.g. "3 Partitionen"), or nothing if the disk has no partitions yet
- Version bump 3.0.x → 3.1.0 marking the Disk-Manager line as feature-complete (partition management, phantom-disk filter, mountpoint reporting, collapse/expand, busy-cursor, FIFO race fix)

## 3.0.67
- New: hourglass / wait cursor on the entire page while a long disk-manager operation (Format, Create, Init) is running — extra visual signal that something is happening

## 3.0.66
- Fix: **multiple `Format` clicks queueing up** — long operations now disable their button and show an in-button spinner so the user can't accidentally fire several mkfs in a row
  - Applies to Format, Create partition, and Initialize partition table
- Bump server-side MKFS timeout from 5 min → 30 min (mkfs.ntfs on multi-TB drives easily exceeds 5 min)

## 3.0.65
- New: **collapse/expand partitions per disk** in the Drives tab — click the disk icon (▸/▾ caret indicates state) to hide or show sda1, sda2, … under the parent disk
- Partition rows are now slightly indented under their parent
- State is persisted in `localStorage` (`expandedDisks`), default is collapsed

## 3.0.64
- Fix: **`FIFO-Fehler: [Errno 6] No such device or address: '/tmp/mount_cmd'`** when issuing disk-manager commands back-to-back
  - `mount_helper.sh` opens the FIFO persistently on fd 3 (+ dummy writer on fd 4) so there's no moment between iterations where the FIFO has no reader
  - `app.py` retries the FIFO open on `ENXIO` for ~2s before giving up

## 3.0.63
- Fix: **create partition failed with 500** on fresh disks — the free-space entry from `parted` starts at 0.02 MiB (inside the GPT primary header) and end-MiB equal to disk size overflows the GPT backup header
  - Server: align start UP to whole MiB (min 1 MiB), align end DOWN to whole MiB
  - UI: when user keeps the default size (= all free space), send `end_mib: "100%"` so parted handles the backup-header margin itself

## 3.0.62
- Fix: hide phantom disks (empty card-reader slots / USB hub ports without medium) — checks `/sys/block/<name>/size` and filters in `/api/drives`; `/api/disk/<name>/partitions` returns 410 if no medium
- Improve: each partition in `/api/disk/<name>/partitions` now reports its real `mountpoint` (string) from `/proc/mounts`, not just a bool
- `_mounted_paths()` resolves `realpath` both ways so `/dev/disk/by-id/...` matches `/dev/sdb2`
- (Ports v3.1.1 + v3.1.2 from sibling project NotSoSimpleNas)

## 3.0.61
- New: **Disk Manager** — manage partition tables and formatting directly from the Drives tab
  - Gear icon on every non-system drive card opens the disk manager modal
  - Shows partition layout with sizes, FS types, labels, mount status, and free-space gaps
  - Create partition in free space, delete partition, format partition (ext4 / exfat / ntfs / vfat / ext3 / ext2)
  - Initialize partition table (GPT or MBR/msdos) on uninitialized disks
  - Safety: system disks blocked entirely; mounted partitions require unmount first; every destructive action requires typing the device/partition name to confirm
- Dockerfile: added `parted`, `dosfstools`, `ntfs-3g-progs`
- `mount_helper.sh`: new actions `PARTLIST`, `PARTMKLABEL`, `PARTADD`, `PARTRM`, `MKFS`, `PARTPROBE`
- `app.py`: new endpoints `GET /api/disk/<name>/partitions`, `POST /api/disk/<name>/init`, `POST /api/disk/<name>/partition`, `DELETE /api/disk/<name>/partition/<n>`, `POST /api/disk/<name>/partition/<n>/format`

## 3.0.60
- New: sortable column header in Files tab — click **Name**, **Größe** or **Datum** to sort, click again to reverse direction (▲ / ▼ indicator)
- Sort key and direction persist in `localStorage` (keys `nas-files-sort-key`, `nas-files-sort-dir`) — defaults to name ascending
- Folders and files are sorted within their own group (folders always shown first)
- Date format unified to `dd/mm/yy HH:MM` (no longer locale-dependent) and labeled accordingly in the column header

## 3.0.59
- Files tab: size bars, file sizes, dates and action buttons now align in fixed columns across all rows
- `.factions` column has fixed width (170 px) with right-aligned buttons — delete (rightmost) lines up across rows regardless of how many actions a row has
- Each action button has a fixed 24×24 px slot so icons stay on the same grid

## 3.0.58
- Fix: size bars rendered all gray — colored fill was invisible because the inner `<span>` is inline by default and ignored `width`/`height`
- `.fbar-fill` now `display:block`, `.fbar` now `display:inline-block` — colored fill renders correctly in both the Files list and the Settings legend
- Selector prefix `.file-row` dropped from `.fbar` rules so the Settings color legend also displays bars

## 3.0.57
- New: dedicated **Settings** tab in the navigation — moved the "Show size bars" toggle out of the Files toolbar into a proper Settings section
- Settings tab uses a real on/off toggle switch (not a button) for the size-bars option
- Color legend is shown next to the toggle so the meaning of each bar color is obvious without hovering files
- Files tab toolbar is back to upload / mkdir only — no clutter

## 3.0.56
- New: optional "Size bars" view in the Files tab — colored bars next to each entry visualize file and folder size at a glance (blue ≤10 MB, green ≤50 MB, yellow ≤100 MB, orange ≤1 GB, red >1 GB)
- Folder sizes are computed recursively on demand (only when the toggle is on); per-folder walk capped at 8 s to keep large trees responsive — incomplete sizes are marked with `≈`
- Bar length is logarithmic relative to the largest entry in the current view so a 10 MB file remains visible next to a 1 GB one
- API: `/api/files` accepts new query param `dir_size=1` to recursively compute directory sizes
- Toggle state persists in `localStorage` (key `nas-show-sizebars`); off by default
- i18n strings added in DE and EN (`btn_sizebars`, `computing_sizes`, `size_truncated`)

## 3.0.55
- Fix: shares pointing to a subdirectory under `/mnt/...` were marked `available = no` even when the underlying drive was mounted, because the subdirectory existed only on the container overlay (created before the mount) and was hidden by the live filesystem after mounting
- `generate_smb_conf.py` now auto-creates the missing subdirectory if its parent is a real mount point — share becomes immediately usable
- `/mnt/` paths now follow the same "real mount required" rule as `/media/` (was treated as always-available before)

## 3.0.54
- Default UI language switched to English (`en`); DE/EN toggle remains in the header
- README, root README and CHANGELOG translated to English; new entries from now on are written in English
- Add-on description (HA Add-on Store) translated to English
- Translation files (`de.yaml`, `en.yaml`) extended with the missing options `nas_name`, `smb_port`, `web_gui_enabled`

## 3.0.53
- Fix: `mount -t ext4` failed with `fsconfig() failed: Can't open blockdev` — HA OS blocks the new kernel mount API (`fsconfig`/`fsopen` syscalls) via seccomp
- `mount_helper.sh` now automatically falls back to `busybox mount` on `fsconfig()` errors — this uses the old `mount(2)` syscall which is still permitted
- Applies to all mount paths: direct, auto-detect, brute-force loop

## 3.0.52
- Fix: USB drives were not mounted at boot (race condition) — `restore_mounts.py` now retries the mount up to 5× with 3 s delay if the device is not yet ready (`Can't open blockdev`)
- Fix: wrong API endpoint in DOCS.md — `/api/reload-samba` does not exist, the correct one is `/api/samba/restart`

## 3.0.51
- Fix: backup-job card — Start / Edit / Delete buttons overflowed the card boundary on long paths
- Card layout restructured: text container (`min-width:0; flex:1`) shrinks if needed, button row (`flex-shrink:0`) always stays fully visible
- Source and destination paths split onto separate lines with line-wrap (`word-break:break-all`)

## 3.0.50
- Fix: drive view and user tab kept showing German text (`Einhängen`, `nicht eingehängt`, `Samba-Benutzer` etc.) after switching language — dynamically built JS content was not re-rendered on language change
- `toggleLang()` now reloads the active tab so all text appears immediately in the chosen language

## 3.0.49
- Fix: drives were no longer mounted after HA restart — `restore_mounts.py` used `fstype: "auto"` instead of the `resolved_fstype` (e.g. `"ext4"`) detected at mount time, which failed on USB devices

## 3.0.48
- Bind-mount feature removed: HA OS uses slave mount namespaces (`master:118`) — mounts from an add-on container do not propagate to other containers or HA Core. The feature was therefore ineffective.
- Mount dialog: checkbox and bind path field removed

## 3.0.47
- FS tag now stays visible even on unmounted drives
- New file `/data/fstype_memory.json` remembers the last known FS type per device — populated on every mount and on every drive listing from `/proc/mounts`, never auto-cleared
- Survives container restarts, unmount and reinstall (`/data` is persistent)

## 3.0.46
- FS tag display gets two additional sources because HA OS blocks raw block-device reads (`blkid` / `file -s` on block devices) with `EPERM`
- Source 2: `/proc/mounts` — reliably shows the FS type of every currently mounted device
- Source 3: `mounts.json` with new field `resolved_fstype` — at mount time, the actual kernel-used type is read from `/proc/mounts` and stored (not just "auto")
- The drive list now reliably shows the FS tag (e.g. `ext4`) for mounted devices and also for later-unmounted ones whose last mount type is known

## 3.0.45
- Drive overview: FS-type tag (`ext4`, `ntfs`, …) is reliably shown again
- When `lsblk` reports no FSTYPE, `blkid` / `blkid -p` / `file -sL` are queried as fallback — same escalation as in the mount helper

## 3.0.44
- Mount auto-detection significantly more robust
- Symlinks (e.g. `/dev/disk/by-id/usb-…`) are resolved before probing via `readlink -f`
- Fourth detection step: `file -s` reads the superblock magic directly (added the `file` package)
- Brute-force fallback: tries `ext4 → ext3 → ext2 → ntfs-3g → vfat → exfat → btrfs → xfs` in turn if all detectors come up empty — fixes USB devices where `blkid` / `lsblk` return nothing but `mount -t ext4` works

## 3.0.43
- Bind-mount name under `/share/<name>` is now editable in the mount dialog (input field directly below the checkbox)
- FS auto-detection in the helper extended to three stages: `blkid` → `blkid -p` (low-level probe) → `lsblk -no FSTYPE`
- Better helper logs (which method detected, which driver was tried)

## 3.0.42
- Mount "Auto" now uses `blkid` for FS detection first — works around the misleading "Can't open blockdev" error from `fsconfig()` on USB devices
- NTFS is automatically mounted via `ntfs-3g` (instead of kernel NTFS read-only)
- Falls back to bare `mount` if explicit `-t` fails
- Added `psmisc` (`fuser`) and `lsof` — busy diagnostics on unmount now work properly

## 3.0.41
- Fix: Mount button did not respond — `JSON.stringify(by_id)` contained double quotes that broke the `onclick="…"` attribute → "Unexpected end of input" when rendering the page
- Quotes are now HTML-escaped to `&quot;`

## 3.0.40
- Fix: Unmount button referenced the old `isSdaDevice` function → JS error, click did nothing
- Unmount dialog now uses the `system_device` flag from the drive API

## 3.0.39
- More robust drive unmounting
- Open Samba handles are closed before `umount` (`smbcontrol close-share`)
- Bind mounts under `/share/` automatically fall back to `umount -l` on "busy"
- For busy drives: GUI shows blocking processes (`fuser` / `lsof`) and offers "Force unmount"

## 3.0.38
- New: bind-mount of mounted drives to `/share/<name>` — accessible by HA Core and other add-ons
- Mount dialog: checkbox "Make accessible to HA Core / other add-ons" (on by default)
- Bind mounts are saved in `mounts.json` and restored automatically on restart
- On unmount the bind is removed first, then the actual mount
- `mount_helper.sh`: new `BIND` action with `mount --make-shared`

## 3.0.37
- Fix: HA backup location sometimes vanished after a restart (race condition)
- `smb.conf` is now generated twice — before and after restoring mounts

## 3.0.36
- All log messages translated to English (run.sh, app.py)

## 3.0.35
- New: option `web_gui_enabled` — web GUI can be fully disabled (smaller attack surface, Samba-only operation)

## 3.0.34
- Existing `/dev/sdX` entries are automatically migrated to `/dev/disk/by-id/` paths on startup

## 3.0.33
- Mounts use stable `/dev/disk/by-id/` paths — survive USB re-numbering after a reboot
- System devices are detected via `/proc/mounts` (no longer just `sda`)
- Inactive shares are marked `available = no` in `smb.conf`
- English documentation (`DOCS.md`) added

## 3.0.32
- Fix: `/ssl` changed from `ro` to `rw` (certificate folder was read-only)
- New: configurable `smb_port` for parallel operation with the official Samba add-on

## 3.0.31
- New: macOS junk files are globally hidden / deleted (`.DS_Store`, `._*`, `.TemporaryItems`, …) via Samba `veto files` + `delete veto files`

## 3.0.0
- New: Files tab — full file browser with upload, download, copy, move, rename, delete
- New: Backup tab — create backup jobs, run manually, auto-clean old backups (rsync-based)
- New: file upload directly via the web GUI (up to 10 GB)
- New: file download directly from the browser
- New: create folder in the file browser
- Dockerfile: rsync added

## 2.2.0
- New: web GUI port configurable (default: 8099)
- New: Samba passwords are saved persistently and restored automatically after restart

## 2.1.0
- Repository renamed to EasyNas-HASSIO-Addon
- Full README with installation guide, quick start, Nextcloud guide
- CHANGELOG added
- Add-on icon and logo
- Translations (DE/EN)
- All fixes from 2.0.x consolidated

## 2.0.9
- Fix: users and groups are restored automatically after container restart
- Fix: password change re-creates missing Samba entries automatically

## 2.0.8
- Browser: "Go to" label for navigation, /share button removed
- Mount field: clearer wording (name or full path)

## 2.0.7
- Fix: public shares now correctly ignore `valid users`
- Samba: SMB2/SMB3 protocol range, better NTLM compatibility

## 2.0.4
- Fix: share directories get correct permissions (2775)
- Fix: smb.conf reload restarts Samba automatically if not running
- Samba: `force user = root`, `force group = root` for full compatibility

## 2.0.0
- New: group management with member assignment
- New: users and groups as chips in the share configuration
- New: dark/light theme (HA design)
- New: sda protection with confirmation dialog
- New: create folder in the browser
- New: folder browser for mount paths with quick navigation

## 1.1.2
- Fix: wsdd as a script instead of pip package (HA wheels index does not have it)

## 1.1.1
- New: network discovery (Avahi for Linux/macOS, WSDD for Windows 10/11)

## 1.0.9
- Fix: `apparmor: false` and `privileged: SYS_ADMIN` for mount operations

## 1.0.8
- All mount points changed from /mnt to /media
- First release
