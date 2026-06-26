# AGENTS.md

MQTT-Bridge für SIEGENIA AEROPAC. Python-Daemon, der das proprietäre
WebSocket-Protokoll der Geräte auf MQTT übersetzt. Kein Framework.

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r siegenia_bridge/requirements.txt
cp siegenia_bridge/config.example.yaml siegenia_bridge/config.yaml   # dann editieren
```

## Run / Test

```bash
# Bridge starten
./venv/bin/python siegenia_bridge/bridge.py siegenia_bridge/config.yaml

# Automatischer Test (Modul gegen Mock-Server, kein echtes Gerät nötig)
cd siegenia_bridge && python3 test_against_mock.py

# Mock-Gerät manuell (Port, Name)
python3 siegenia_bridge/mock_siegenia_server.py 8443 "AEROPAC Test"

# Direktsteuerung ohne MQTT
python3 tools/siegenia_cli.py <ip> <user> <password> status|on|off|watch
python3 tools/siegenia_cli.py <ip> <user> <password> fan <0-7>
```

Nach Änderungen an `siegenia.py` oder `bridge.py` IMMER `test_against_mock.py`
laufen lassen — er muss mit `=== ALLE TESTS BESTANDEN ===` enden.

## Protokoll-Constraints (verifiziert, nicht raten)

Diese Punkte sind empirisch gegen echte Hardware (AEROPAC, FW 1.7.7) ermittelt.
Sie sind nicht aus der Doku ableitbar — nicht „vereinfachen" oder umschreiben:

- **Ausschalten** ist `setDeviceParams {"devicestate": {"deviceactive": false}}`.
  `fanlevel: 0` allein schaltet das Gerät NICHT aus. Häufiger Fehlschluss.
- **Nicht pollen nach einem Set.** Das Gerät quittiert `setDeviceParams` sofort
  mit `status: ok`, übernimmt den Wert aber asynchron. Ein direkt folgendes
  `getDeviceParams` liefert den ALTEN Wert. Stattdessen auf den unaufgeforderten
  Push (`status: "update"`) warten — den verarbeitet `on_update`.
- **Pro Account nur EINE Session.** Die offizielle App nutzt `user`; die Bridge
  sollte `admin` nutzen, sonst werfen sich App und Bridge gegenseitig raus.
- **Heartbeat zwingend:** `keepAlive` regelmässig (Default 25s), sonst trennt
  das Gerät. Gemessener Timeout ohne Ping: ~61s. Intervall per Config einstellbar.
- **Schreib-Queue mit Mindestabstand** (`min_write_interval`) ist Absicht: das
  Gerät verträgt keine schnell aufeinanderfolgenden Set-Befehle. Nicht entfernen.
- **Close-Frame ist nicht RFC-konform** (`Invalid close opcode`). Das ist ein
  normales Verbindungsende, kein Fehler — wird in `_receive_loop` abgefangen.

Volldetails in `docs/PROTOCOL.md`.

## Konventionen

- paho-mqtt: Code muss mit 1.x UND 2.x laufen (CallbackAPIVersion.VERSION1 in
  try/except). Signatur `on_connect(client, userdata, flags, rc)` beibehalten.
- Topic-Schema ist öffentliche API: `siegenia/<id>/status/*` (retained) und
  `siegenia/<id>/set/*`. Bei Änderungen README + HA-Discovery synchron halten.
- HA-Discovery ist optional (Config-Flag, default aus). Default nie auf true.

## Boundaries

- `config.yaml` und `config.*.yaml` enthalten Passwörter — NIE committen
  (stehen in `.gitignore`). Nur `config.example.yaml` mit Dummy-Werten.
- Keine echten Geräte-IPs, Passwörter oder Tokens in Code, Tests oder Commits.
- `siegenia.py` ist der getestete Protokoll-Kern — Änderungen dort nur mit
  laufendem `test_against_mock.py`.
