# siegenia-aeropac-mqtt

Eine schlanke **MQTT-Bridge für SIEGENIA AEROPAC** (und kompatible SIEGENIA
Klima-/Lüftungsgeräte). Ein kleiner Python-Daemon hält die WebSocket-Verbindung
zum Gerät und spiegelt Zustand & Steuerung auf MQTT – nutzbar mit **openHAB,
Home Assistant, Node-RED, ioBroker, FHEM** oder einfach `mosquitto`.

> Status: funktioniert, live verifiziert gegen ein AEROPAC mit Firmware 1.7.7.

## Warum MQTT statt eines nativen Bindings/Integration?

SIEGENIA-Geräte sprechen ein **proprietäres WebSocket-Protokoll** (TLS mit
selbstsigniertem Zertifikat, JSON-Messages, Login + Heartbeat). Statt für jedes
Smart-Home-System ein eigenes Plugin zu bauen und zu pflegen, übersetzt diese
Bridge das Protokoll **einmal** auf MQTT – das versteht jedes System. Gleiches
Prinzip wie `zigbee2mqtt` oder `tasmota`.

## Features

- Mehrere Geräte gleichzeitig (ein Daemon, beliebig viele AEROPACs)
- Event-getrieben: nutzt die Push-Updates des Geräts statt zu pollen
- Robuste WebSocket-Verbindung mit Heartbeat & Auto-Reconnect
- Schreib-Queue mit Debounce (das Gerät verträgt keine schnellen Set-Folgen)
- Optionales **Home Assistant MQTT Discovery** (per Config-Flag, default aus)
- Standalone-CLI zum Testen/Steuern ohne MQTT

## MQTT-Topics (plain – immer aktiv)

| Topic | Richtung | Werte |
|---|---|---|
| `siegenia/<id>/status/online` | Bridge → | `online` / `offline` (retained) |
| `siegenia/<id>/status/fanlevel` | Bridge → | `0`..`7` (retained) |
| `siegenia/<id>/status/active` | Bridge → | `true` / `false` (retained) |
| `siegenia/<id>/status/raw` | Bridge → | komplettes Geräte-JSON (retained) |
| `siegenia/<id>/set/fanlevel` | → Bridge | `0`..`7` |
| `siegenia/<id>/set/active` | → Bridge | `true`/`false`/`ON`/`OFF`/`1`/`0` |
| `siegenia/bridge/online` | Bridge → | `online` / `offline` (LWT) |

`<id>` ist die in der Config vergebene Geräte-ID.

## Home Assistant (optional)

Mit `homeassistant.discovery: true` publiziert die Bridge Auto-Discovery-Configs
unter `homeassistant/...`. Jedes Gerät erscheint dann in HA automatisch als
**Fan-Entity** (An/Aus + Stufe 1–7) plus ein **Switch**. Es werden dieselben
plain-Topics genutzt. Für andere Systeme einfach `discovery: false` lassen.

## Verifizierte Protokoll-Eckpunkte

- `wss://<ip>:443/WebSocket`, selbstsigniertes Zertifikat (TLS-Verify aus)
- **Lüfterstufe (0–7):** `setDeviceParams {"fanlevel": n}`
- **Aus (wichtig!):** `setDeviceParams {"devicestate": {"deviceactive": false}}`
  – `fanlevel: 0` allein schaltet das Gerät **nicht** aus.
- Heartbeat: `keepAlive` regelmässig (Default 25s), sonst trennt das Gerät.
  Gemessener Timeout ohne Ping: ~61s. Per Config einstellbar.
- Das Gerät **pusht** Zustandsänderungen selbst → die Bridge lauscht, statt zu pollen.
- **Account-Limit:** pro Account nur **eine** aktive Session. Die offizielle App
  nutzt `user`; für die Bridge daher `admin` verwenden, wenn die App parallel
  laufen soll. Details in [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## Installation

```bash
git clone <repo> siegenia-aeropac-mqtt
cd siegenia-aeropac-mqtt

python3 -m venv venv
./venv/bin/pip install -r siegenia_bridge/requirements.txt

cp siegenia_bridge/config.example.yaml siegenia_bridge/config.yaml
nano siegenia_bridge/config.yaml      # Broker, Geräte-IPs, Login anpassen

# Manuell testen
./venv/bin/python siegenia_bridge/bridge.py siegenia_bridge/config.yaml
```

### Als systemd-Service

```bash
sudo cp systemd/siegenia-aeropac-mqtt.service /etc/systemd/system/
# WICHTIG: WorkingDirectory, ExecStart und User/Group in der Unit anpassen —
# die Pfade müssen auf deine tatsächliche Installation zeigen.
# Falsche Pfade → status=200/CHDIR in journalctl. Details stehen als Kommentar
# direkt in der Unit-Datei.
sudo systemctl daemon-reload
sudo systemctl enable --now siegenia-aeropac-mqtt
journalctl -u siegenia-aeropac-mqtt -f
```

## Konfiguration (Mehrere Geräte)

```yaml
mqtt:
  host: "192.168.1.10"
  base_topic: "siegenia"

homeassistant:
  discovery: false            # true für Home Assistant Auto-Discovery

devices:
  - id: "aeropac_eltern"
    ip: "192.168.1.50"
    user: "admin"
    password: "0000"
  - id: "aeropac_kind"
    ip: "192.168.1.51"
    user: "admin"
    password: "0000"
```

Siehe [`config.example.yaml`](siegenia_bridge/config.example.yaml) für alle Optionen.

## CLI (Testen/Steuern ohne MQTT)

```bash
cd tools
python3 siegenia_cli.py <ip> <user> <password> status
python3 siegenia_cli.py <ip> <user> <password> fan 3
python3 siegenia_cli.py <ip> <user> <password> on
python3 siegenia_cli.py <ip> <user> <password> off
python3 siegenia_cli.py <ip> <user> <password> watch     # Live-Updates
```

`tools/siegenia_sniff.py` ist ein mitmproxy-Addon, um den Traffic der originalen
Siegenia-App mitzuschneiden (für weitere Befehle):

```bash
mitmdump -p 8888 --ssl-insecure -s tools/siegenia_sniff.py
```

## Tests

```bash
cd siegenia_bridge
python3 test_against_mock.py            # Modul gegen Mock-Server
# Mock auch manuell startbar (Port + Name):
python3 mock_siegenia_server.py 8443 "AEROPAC Test"
```

## Dank

Protokoll-Grundlage inspiriert von
[Apollon77/ioBroker.siegenia](https://github.com/Apollon77/ioBroker.siegenia) (MIT).

## Lizenz

MIT
