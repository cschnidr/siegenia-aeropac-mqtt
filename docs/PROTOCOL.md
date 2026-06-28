# SIEGENIA WebSocket-Protokoll (AEROPAC)

Dokumentiert die per Reverse-Engineering und Live-Mitschnitt (mitmproxy gegen die
originale Siegenia Comfort App) ermittelten Protokoll-Details. Getestet gegen ein
**AEROPAC**, Hardware 1.11, Firmware (softwareversion) **1.7.7**, `type: 1`.

## Verbindung

- URL: `wss://<IP>:443/WebSocket`
- TLS: selbstsigniertes Zertifikat → Client muss Zertifikatsprüfung deaktivieren
- Nachrichten: JSON, jeweils mit fortlaufender `id`
- Antworten korrelieren über dieselbe `id`
- Zusätzlich sendet das Gerät **unaufgeforderte** Nachrichten (Pushes), erkennbar
  an `"status": "update"` und/oder `"command": "deviceParams"` ohne passende `id`.

## Ablauf

1. `getDevice` (optional, vor Login) → Geräte-Metadaten inkl. `type`
2. `login` mit `user` + `password`
3. `getDeviceState`, `getDeviceParams`, `getDeviceDetails`
4. zyklisch `keepAlive` (Default 25s; gemessener Geräte-Timeout ohne Ping ~61s.
   Die offizielle App pingt alle ~7s, der ioBroker-Adapter alle 10s — beide sehr
   konservativ. 25s lässt komfortablen Puffer bis zum 61s-Timeout.)

## Befehle (verifiziert)

### Login
```json
{"command":"login","user":"admin","password":"....","long_life":false,"id":N}
```
Antwort:
```json
{"data":{"isadmin":true,"token":"...","user":"admin"},"id":N,"status":"ok"}
```
Die App nutzt `user:"user"` (nicht-admin) mit `long_life:true`; beides
funktioniert. **Wichtig:** Das Gerät erlaubt pro Account offenbar nur **eine**
aktive WebSocket-Session. Ein zweiter Login mit demselben Account wird direkt
nach dem Connect abgewiesen (Verbindung bricht ab, bevor `login` beantwortet
wird). **Empfehlung für eine Dauer-Bridge:** den `admin`-Account verwenden, da
die App `user` belegt — so haben Bridge und App getrennte Sessions und stören
sich nicht.

### Lüfterstufe setzen (0–7)
```json
{"command":"setDeviceParams","params":{"fanlevel":3},"id":N}
```

### Gerät ein-/ausschalten  ← der eigentliche Power-Schalter
```json
{"command":"setDeviceParams","params":{"devicestate":{"deviceactive":false}},"id":N}
```
**Wichtig:** `fanlevel: 0` allein schaltet das Gerät NICHT aus. Der korrekte
Aus-Befehl ist `devicestate.deviceactive = false`. Beim Ausschalten setzt das
Gerät `fanlevel` selbst auf 0.

### Timer starten (Dauer-Countdown)

**Wichtig:** Starten erfordert immer **zwei** separate `setDeviceParams`-Befehle in
dieser Reihenfolge. Ein kombiniertes Schreiben funktioniert nicht.

**Schritt 1 – Dauer setzen** (max. 18h):
```json
{"command":"setDeviceParams","params":{"timer":{"duration":{"hour":4,"minute":0}}},"id":N}
```
Push danach:
```json
{"command":"deviceParams","data":{"timer":{"duration":{"hour":4,"minute":0}}},"status":"update"}
```

**Schritt 2 – Timer aktivieren:**
```json
{"command":"setDeviceParams","params":{"timer":{"enabled":true}},"id":N}
```
Pushes danach (zwei separate Nachrichten):
```json
{"command":"deviceParams","data":{"timer":{"enabled":true}},"status":"update"}
{"command":"deviceParams","data":{"timer":{"remainingtime":{"hour":3,"minute":59}}},"status":"update"}
```

### Timer abbrechen
```json
{"command":"setDeviceParams","params":{"timer":{"enabled":false}},"id":N}
```
Push danach:
```json
{"command":"deviceParams","data":{"timer":{"enabled":false,"remainingtime":{"hour":0,"minute":0}}},"status":"update"}
```

**Hinweise zum Timer:**
- `timer.remainingtime` ist **read-only** — niemals schreiben.
- `timer.duration` kann jederzeit neu gesetzt werden, auch während ein Timer läuft
  (Gerät nimmt die neue Dauer erst beim nächsten Start an).
- Nach Kaltstart ist `timer.poweron_time` oft ungültig (z. B. `hour:27`); das ist
  solange folgenlos, wie `timer.enabled: false`. Absolute Wochentimer brauchen eine
  gesetzte Geräteuhr (`clock`); die Bridge setzt die Uhr bewusst nicht.

### keepAlive
```json
{"command":"keepAlive","id":N}        // App-Variante
{"command":"keepAlive","params":{"extend_session":true},"id":N}  // ioBroker-Variante
```

## Push-Updates

Nach einer Zustandsänderung sendet das Gerät unaufgefordert:
```json
{"command":"deviceParams","data":{"devicestate":{"deviceactive":false},"fanlevel":0},"status":"update"}
```

## Beobachtete Eigenheiten / Fallstricke

- **`setDeviceParams` quittiert sofort `status:ok`**, das bedeutet aber nur
  "Befehl angenommen", nicht "Zustand erreicht". Ein *direkt* danach
  abgesetztes `getDeviceParams` kann noch den **alten** Wert liefern
  (gecachter Stand). → Auf den Push warten statt sofort nachfragen.
- **Aktor braucht reale Zeit.** Mechanische Stufenwechsel dauern; mehrere
  Set-Befehle in schneller Folge können sich gegenseitig stören. → Bridge nutzt
  eine Schreib-Queue mit Mindestabstand (`min_write_interval`).
- **Parallele App-Session** kann mit einer eigenen WebSocket-Verbindung
  kollidieren (Befehle werden quittiert, aber nicht ausgeführt). → Bridge sollte
  die einzige dauerhafte Verbindung sein.
- Feld `timer.poweron_time` kann ungültige Werte enthalten (z. B. `hour:27`),
  solange `timer.enabled:false` ist offenbar folgenlos.
- **Nicht-standardkonformer Close-Frame:** Beim Verbindungsende sendet das Gerät
  einen Close-Frame, der nicht RFC 6455 entspricht (`Invalid close opcode`,
  Payload `0x3130` = "10"). WebSocket-Bibliotheken werfen dabei eine Exception.
  Das ist ein normales Verbindungsende und sollte als solches behandelt werden
  (kein echter Fehler).

## Felder aus `getDeviceParams` (AEROPAC)

| Feld | Typ | Schreibbar | Bemerkung |
|---|---|---|---|
| Feld | Typ | Schreibbar | Bemerkung |
|---|---|---|---|
| `fanlevel` | 0–7 | ja | Lüfterstufe |
| `devicestate.deviceactive` | bool | ja | Ein/Aus |
| `devicename` | string | nein | frei vergebener Name |
| `devicefloor` / `devicelocation` | string | nein | Standort-Metadaten |
| `timer.duration` | `{hour, minute}` | ja | Dauer-Countdown; max. 18h |
| `timer.enabled` | bool | ja | Timer starten/abbrechen (siehe oben) |
| `timer.remainingtime` | `{hour, minute}` | **nein** | Restlaufzeit, nur Lesen |
| `timer.poweron_time` | object | ja | Absoluter Wochentimer; braucht gesetzte Uhr |
| `warnings` | array | nein | Warnungen, meist leer |
| `clock` | object | (sync) | App synct Uhrzeit beim Connect; Bridge tut das nicht |
