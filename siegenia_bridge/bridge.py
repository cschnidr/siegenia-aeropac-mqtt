#!/usr/bin/env python3
"""
bridge.py — MQTT-Bridge für SIEGENIA AEROPAC (und kompatible Geräte).

Verbindet ein oder mehrere SIEGENIA-Geräte per WebSocket mit einem MQTT-Broker:
- Push-Updates vom Gerät  ->  MQTT state-Topics
- MQTT command-Topics     ->  setDeviceParams am Gerät

Topic-Schema (plain, gut lesbar):
    siegenia/<device_id>/status/online        -> "online" | "offline"   (retained, LWT)
    siegenia/<device_id>/status/fanlevel       -> 0..7                    (retained)
    siegenia/<device_id>/status/active         -> "true" | "false"       (retained)
    siegenia/<device_id>/status/raw            -> komplettes JSON         (retained)
    siegenia/<device_id>/set/fanlevel          <- 0..7
    siegenia/<device_id>/set/active            <- "true"|"false"|"ON"|"OFF"|"1"|"0"

Konfiguration über config.yaml (siehe config.example.yaml).
"""

import json
import os
import signal
import sys
import threading
import time
import warnings

# paho-mqtt 2.x warnt bei CallbackAPIVersion.VERSION1 — harmlos, wir unterdrücken es
warnings.filterwarnings("ignore", category=DeprecationWarning, module="paho")

import paho.mqtt.client as mqtt
import yaml

from siegenia import SiegeniaDevice


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


class DeviceBridge:
    """Verbindet genau ein SIEGENIA-Gerät mit MQTT."""

    def __init__(self, mqtt_client, base_topic, dev_cfg, discovery_cfg=None):
        self.mqtt = mqtt_client
        self.device_id = dev_cfg["id"]
        self.base = f"{base_topic}/{self.device_id}"
        self.cfg = dev_cfg
        # Anzeigename für HA-Discovery (Default: aus ID abgeleitet)
        self.name = dev_cfg.get("name", f"AEROPAC {self.device_id}")
        self.discovery_cfg = discovery_cfg or {}

        # Letzter bekannter State (für Idempotenz / Logging)
        self.last_state = {}

        # Schreib-Queue mit Debounce: das Gerät verträgt keine schnell
        # aufeinanderfolgenden Set-Befehle gut (verifiziert). Wir sammeln
        # gewünschte Werte und schreiben mit Mindestabstand.
        self._pending_writes = {}
        self._write_lock = threading.Lock()
        self._min_write_interval = dev_cfg.get("min_write_interval", 3.0)
        self._last_write_ts = 0.0

        self.device = SiegeniaDevice(
            ip=dev_cfg["ip"],
            user=dev_cfg["user"],
            password=str(dev_cfg["password"]),
            port=dev_cfg.get("port", 443),
            long_life=dev_cfg.get("long_life", True),
            heartbeat_interval=dev_cfg.get("heartbeat_interval", 25),
            on_update=self._on_device_update,
            on_connect=self._on_device_connect,
            on_disconnect=self._on_device_disconnect,
            logger=log if dev_cfg.get("debug") else (lambda *a, **k: None),
        )

        # Writer-Thread für die Debounce-Queue
        self._writer = threading.Thread(target=self._writer_loop, daemon=True)

    # ---------- Lifecycle ----------

    def start(self):
        self.device.start()
        self._writer.start()
        # Command-Topics abonnieren
        self.mqtt.subscribe(f"{self.base}/set/#")
        log(f"[{self.device_id}] Bridge gestartet, lausche auf {self.base}/set/#")
        # Home Assistant MQTT Discovery (optional)
        if self.discovery_cfg.get("enabled"):
            self._publish_ha_discovery()

    def _publish_ha_discovery(self):
        """Publiziert Home-Assistant-MQTT-Discovery-Configs (retained).
        Nutzt dieselben State-/Command-Topics wie der plain-Modus.
        """
        prefix = self.discovery_cfg.get("prefix", "homeassistant")
        avail = [{
            "topic": f"{self.base}/status/online",
            "payload_available": "online",
            "payload_not_available": "offline",
        }]
        device_block = {
            "identifiers": [f"siegenia_{self.device_id}"],
            "name": self.name,
            "manufacturer": "SIEGENIA",
            "model": "AEROPAC",
        }

        # fan-Entity: An/Aus + Lüfterstufe (Prozent über 7 Stufen)
        fan_cfg = {
            "name": self.name,
            "unique_id": f"siegenia_{self.device_id}_fan",
            "state_topic": f"{self.base}/status/active",
            "state_value_template": "{{ 'ON' if value == 'true' else 'OFF' }}",
            "command_topic": f"{self.base}/set/active",
            "payload_on": "true",
            "payload_off": "false",
            "percentage_state_topic": f"{self.base}/status/fanlevel",
            "percentage_command_topic": f"{self.base}/set/fanlevel",
            "speed_range_min": 1,
            "speed_range_max": 7,
            "availability": avail,
            "device": device_block,
        }
        self.mqtt.publish(
            f"{prefix}/fan/siegenia_{self.device_id}/config",
            json.dumps(fan_cfg), retain=True,
        )

        # zusätzlich ein einfacher Switch (manche bevorzugen das)
        switch_cfg = {
            "name": f"{self.name} Aktiv",
            "unique_id": f"siegenia_{self.device_id}_active",
            "state_topic": f"{self.base}/status/active",
            "command_topic": f"{self.base}/set/active",
            "payload_on": "true",
            "payload_off": "false",
            "availability": avail,
            "device": device_block,
        }
        self.mqtt.publish(
            f"{prefix}/switch/siegenia_{self.device_id}_active/config",
            json.dumps(switch_cfg), retain=True,
        )
        log(f"[{self.device_id}] HA-Discovery publiziert (prefix={prefix})")

    def _remove_ha_discovery(self):
        """Entfernt die Discovery-Configs (leere retained Message)."""
        prefix = self.discovery_cfg.get("prefix", "homeassistant")
        for topic in (
            f"{prefix}/fan/siegenia_{self.device_id}/config",
            f"{prefix}/switch/siegenia_{self.device_id}_active/config",
        ):
            self.mqtt.publish(topic, "", retain=True)

    def stop(self):
        self.device.stop()
        self._publish("status/online", "offline", retain=True)

    # ---------- Geräte-Events -> MQTT ----------

    def _on_device_connect(self):
        self._publish("status/online", "online", retain=True)
        log(f"[{self.device_id}] Gerät verbunden")

    def _on_device_disconnect(self):
        self._publish("status/online", "offline", retain=True)
        log(f"[{self.device_id}] Gerät getrennt")

    def _on_device_update(self, data):
        # data kann fanlevel, devicestate.deviceactive u.a. enthalten
        changed = []

        if "fanlevel" in data:
            self.last_state["fanlevel"] = data["fanlevel"]
            self._publish("status/fanlevel", data["fanlevel"], retain=True)
            changed.append(f"fanlevel={data['fanlevel']}")

        # deviceactive kann an zwei Stellen auftauchen
        active = None
        if "devicestate" in data and isinstance(data["devicestate"], dict):
            if "deviceactive" in data["devicestate"]:
                active = data["devicestate"]["deviceactive"]
        if "deviceactive" in data:
            active = data["deviceactive"]
        if active is not None:
            self.last_state["active"] = active
            self._publish("status/active", "true" if active else "false", retain=True)
            changed.append(f"active={active}")

        # Vollständiges JSON als raw-Topic (nützlich für Debugging / weitere Felder)
        self._publish("status/raw", json.dumps(data), retain=True)

        if changed:
            log(f"[{self.device_id}] Update: {', '.join(changed)}")

    # ---------- MQTT-Commands -> Gerät ----------

    def handle_command(self, topic, payload):
        # topic: siegenia/<id>/set/<what>
        what = topic.rsplit("/", 1)[-1]
        payload = payload.strip()
        log(f"[{self.device_id}] Command empfangen: set/{what} = {payload!r}")

        if what == "fanlevel":
            try:
                level = int(float(payload))
            except ValueError:
                log(f"[{self.device_id}] Ungültiger fanlevel-Wert: {payload!r}")
                return
            with self._write_lock:
                self._pending_writes["fanlevel"] = max(0, min(7, level))

        elif what == "active":
            val = payload.lower() in ("true", "on", "1", "yes", "ja")
            with self._write_lock:
                self._pending_writes["active"] = val

        else:
            log(f"[{self.device_id}] Unbekanntes Command-Topic: {what}")

    # ---------- Debounced Writer ----------

    def _writer_loop(self):
        while True:
            time.sleep(0.5)
            with self._write_lock:
                if not self._pending_writes:
                    continue
                if not self.device.connected:
                    continue
                now = time.time()
                if now - self._last_write_ts < self._min_write_interval:
                    continue
                writes = dict(self._pending_writes)
                self._pending_writes.clear()
                self._last_write_ts = now

            # ausserhalb des Locks ausführen
            for key, value in writes.items():
                try:
                    if key == "fanlevel":
                        # fanlevel > 0 impliziert: Gerät muss aktiv sein
                        if value > 0 and not self.last_state.get("active", False):
                            self.device.set_active(True)
                            time.sleep(1.0)
                        resp = self.device.set_fanlevel(value)
                        log(f"[{self.device_id}] set_fanlevel({value}) -> {self._status(resp)}")
                    elif key == "active":
                        resp = self.device.set_active(value)
                        log(f"[{self.device_id}] set_active({value}) -> {self._status(resp)}")
                except Exception as e:
                    log(f"[{self.device_id}] Schreibfehler {key}={value}: {e}")

    @staticmethod
    def _status(resp):
        if isinstance(resp, dict):
            return resp.get("status", "?")
        return "no-response"

    # ---------- MQTT publish helper ----------

    def _publish(self, subtopic, value, retain=False):
        self.mqtt.publish(f"{self.base}/{subtopic}", value, retain=retain)


class Bridge:
    def __init__(self, config):
        self.config = config
        self.base_topic = config.get("mqtt", {}).get("base_topic", "siegenia")
        self.device_bridges = {}

        # Home Assistant Discovery (optional, default aus)
        hcfg = config.get("homeassistant", {})
        self.discovery_cfg = {
            "enabled": hcfg.get("discovery", False),
            "prefix": hcfg.get("discovery_prefix", "homeassistant"),
        }

        mcfg = config["mqtt"]
        client_id = mcfg.get("client_id", "siegenia-bridge")
        # paho-mqtt 2.x verlangt CallbackAPIVersion; 1.x kennt das nicht.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            try:
                self.mqtt = mqtt.Client(
                    mqtt.CallbackAPIVersion.VERSION1, client_id=client_id
                )
            except AttributeError:
                # paho-mqtt 1.x
                self.mqtt = mqtt.Client(client_id=client_id)

        if mcfg.get("username"):
            self.mqtt.username_pw_set(mcfg["username"], mcfg.get("password", ""))

        self.mqtt.on_connect = self._on_mqtt_connect
        self.mqtt.on_message = self._on_mqtt_message

        # Last Will: meldet die Bridge selbst als offline, wenn der Prozess stirbt
        self.mqtt.will_set(
            f"{self.base_topic}/bridge/online", "offline", retain=True
        )

        self._mqtt_host = mcfg["host"]
        self._mqtt_port = mcfg.get("port", 1883)

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            log(f"MQTT verbunden mit {self._mqtt_host}:{self._mqtt_port}")
            client.publish(f"{self.base_topic}/bridge/online", "online", retain=True)
            # Re-Subscribe nach (Re-)Connect
            for db in list(self.device_bridges.values()):
                client.subscribe(f"{db.base}/set/#")
        else:
            log(f"MQTT-Verbindung fehlgeschlagen, rc={rc}")

    def _on_mqtt_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8")
        except Exception:
            payload = ""
        # zugehörige DeviceBridge finden
        parts = msg.topic.split("/")
        # erwartet: <base>/<device_id>/set/<what>
        if len(parts) >= 4 and parts[-2] == "set":
            device_id = parts[-3]
            db = self.device_bridges.get(device_id)
            if db:
                db.handle_command(msg.topic, payload)

    def start(self):
        self.mqtt.connect(self._mqtt_host, self._mqtt_port, keepalive=60)
        self.mqtt.loop_start()

        for dev_cfg in self.config["devices"]:
            db = DeviceBridge(
                self.mqtt, self.base_topic, dev_cfg,
                discovery_cfg=self.discovery_cfg,
            )
            self.device_bridges[dev_cfg["id"]] = db
            db.start()

        disc = "an" if self.discovery_cfg["enabled"] else "aus"
        log(f"Bridge läuft mit {len(self.device_bridges)} Gerät(en). "
            f"HA-Discovery: {disc}.")

    def stop(self):
        log("Bridge wird beendet ...")
        for db in self.device_bridges.values():
            db.stop()
        time.sleep(0.5)
        self.mqtt.loop_stop()
        self.mqtt.disconnect()


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    config_path = os.environ.get("SIEGENIA_CONFIG", "config.yaml")
    if len(sys.argv) > 1:
        config_path = sys.argv[1]
    if not os.path.exists(config_path):
        log(f"Konfiguration nicht gefunden: {config_path}")
        sys.exit(1)

    config = load_config(config_path)
    bridge = Bridge(config)

    stop_event = threading.Event()

    def handle_signal(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    bridge.start()
    try:
        stop_event.wait()
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()
