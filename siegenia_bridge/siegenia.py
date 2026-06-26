"""
siegenia.py — WebSocket-Client für SIEGENIA Klima-/Lüftungsgeräte (AEROPAC u.a.).

Protokoll rekonstruiert und live verifiziert gegen ein AEROPAC (Firmware 1.7.7):
- wss://<IP>:443/WebSocket, selbstsigniertes Zertifikat (TLS-Verify aus)
- JSON-Messages {"command": ..., "id": <n>, ...}
- Login erforderlich vor allen anderen Commands
- keepAlive in Intervallen (Default 25s), sonst trennt das Gerät die Verbindung.
  Gemessener Geräte-Timeout ohne keepAlive: ~61s (AEROPAC, FW 1.7.7).
- Gerät pusht unaufgefordert {"command":"deviceParams", "status":"update", "data":{...}}
  bei tatsächlichen Zustandsänderungen -> NICHT aktiv pollen nach einem Set

Wichtige verifizierte Befehle:
- Lüfterstufe (0-7):  setDeviceParams {"fanlevel": n}
- Aus:                setDeviceParams {"devicestate": {"deviceactive": false}}
- An:                 setDeviceParams {"devicestate": {"deviceactive": true}}

Basiert konzeptionell auf lib/siegenia.js von Apollon77/ioBroker.siegenia (MIT).
"""

import json
import ssl
import threading
import time

import websocket  # websocket-client


class SiegeniaDevice:
    def __init__(self, ip, user, password, port=443,
                 on_update=None, on_connect=None, on_disconnect=None,
                 logger=None, long_life=True, heartbeat_interval=25,
                 connect_timeout=30):
        self.ip = ip
        self.user = user
        self.password = password
        self.port = port
        self.long_life = long_life
        # Geräte-Timeout ohne keepAlive gemessen: ~61s (AEROPAC, FW 1.7.7).
        # 25s lässt komfortablen Puffer (Timeout/2 wäre ~30s).
        self.heartbeat_interval = heartbeat_interval
        self.connect_timeout = connect_timeout

        self.on_update = on_update          # callback(data: dict)
        self.on_connect = on_connect        # callback()
        self.on_disconnect = on_disconnect  # callback()
        self.log = logger or (lambda *a, **k: None)

        self.ws = None
        self._req_id = 1
        self._pending = {}          # id -> (event, result-holder)
        self._lock = threading.Lock()

        self._stop = False
        self._connected = False
        self._error_counter = 0
        self._hb_thread = None

    # ---------- öffentliche API ----------

    def start(self):
        """Startet die Verbindung in einem Hintergrund-Thread mit Auto-Reconnect."""
        self._stop = False
        t = threading.Thread(target=self._run_loop, daemon=True)
        t.start()
        return t

    def stop(self):
        self._stop = True
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass

    def set_fanlevel(self, level):
        """Setzt die Lüfterstufe (0-7). 0 ist nicht das echte 'Aus' (siehe set_active)."""
        level = max(0, min(7, int(level)))
        return self._send_command("setDeviceParams", params={"fanlevel": level})

    def set_active(self, active: bool):
        """Schaltet das Gerät ein/aus (der eigentliche Power-Schalter)."""
        return self._send_command(
            "setDeviceParams",
            params={"devicestate": {"deviceactive": bool(active)}},
        )

    def get_device_params(self):
        return self._send_command("getDeviceParams")

    def get_device_state(self):
        return self._send_command("getDeviceState")

    @property
    def connected(self):
        return self._connected

    # ---------- intern ----------

    def _run_loop(self):
        while not self._stop:
            try:
                self._connect_once()
            except Exception as e:
                self.log(f"[{self.ip}] Verbindungsfehler: {e}")

            self._connected = False
            if self.on_disconnect:
                try:
                    self.on_disconnect()
                except Exception:
                    pass

            if self._stop:
                break

            self._error_counter += 1
            delay = min(self._error_counter * 5 + 5, 60)
            self.log(f"[{self.ip}] Reconnect in {delay}s")
            for _ in range(delay):
                if self._stop:
                    break
                time.sleep(1)

    def _connect_once(self):
        url = f"wss://{self.ip}:{self.port}/WebSocket"
        self.log(f"[{self.ip}] Verbinde zu {url}")
        self.ws = websocket.create_connection(
            url,
            sslopt={"cert_reqs": ssl.CERT_NONE},
            origin=f"wss://{self.ip}:{self.port}",
            timeout=self.connect_timeout,
        )

        # Login
        resp = self._send_command(
            "login", user=self.user, password=self.password,
            long_life=self.long_life, _direct=True,
        )
        if not resp or resp.get("status") != "ok":
            raise RuntimeError(f"Login fehlgeschlagen: {resp}")

        self.log(f"[{self.ip}] Login erfolgreich")
        self._connected = True
        self._error_counter = 0

        # Heartbeat starten
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._hb_thread.start()

        if self.on_connect:
            try:
                self.on_connect()
            except Exception:
                pass

        # Initialen Zustand holen, damit MQTT direkt befüllt wird
        self._request_async("getDeviceParams")
        self._request_async("getDeviceState")

        # Empfangs-Schleife
        self._receive_loop()

    def _receive_loop(self):
        while not self._stop:
            try:
                raw = self.ws.recv()
            except Exception as e:
                # SIEGENIA-Geräte schliessen die Verbindung mit einem nicht
                # RFC-6455-konformen Close-Frame ('Invalid close opcode'),
                # was websocket-client als Exception meldet. Das ist ein
                # normales Verbindungsende, kein echter Fehler -> ruhig behandeln.
                msg = str(e)
                if "Invalid close opcode" in msg or "closed" in msg.lower():
                    self.log(f"[{self.ip}] Verbindung beendet (Gerät)")
                else:
                    self.log(f"[{self.ip}] recv beendet: {e}")
                break
            if not raw:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                self.log(f"[{self.ip}] Ungültiges JSON: {raw}")
                continue
            self._handle_message(msg)

    def _handle_message(self, msg):
        msg_id = msg.get("id")

        # Antwort auf eine ID-korrelierte Anfrage?
        if msg_id is not None and msg_id in self._pending:
            event, holder = self._pending.pop(msg_id)
            holder["result"] = msg
            event.set()
            # getDeviceParams/State-Antworten enthalten ebenfalls Daten -> weiterreichen
            data = msg.get("data")
            if isinstance(data, dict):
                self._emit_update(data)
            return

        # Unaufgeforderter Push (status == "update", command == "deviceParams")
        data = msg.get("data")
        if isinstance(data, dict):
            self._emit_update(data)

    def _emit_update(self, data):
        if self.on_update:
            try:
                self.on_update(data)
            except Exception as e:
                self.log(f"[{self.ip}] on_update Fehler: {e}")

    def _heartbeat_loop(self):
        while not self._stop and self._connected:
            # in 1s-Schritten schlafen, damit stop() schnell greift
            for _ in range(self.heartbeat_interval):
                if self._stop or not self._connected:
                    return
                time.sleep(1)
            try:
                self._request_async("keepAlive", extend_session=True)
            except Exception as e:
                self.log(f"[{self.ip}] Heartbeat-Fehler: {e}")
                break

    def _next_id(self):
        with self._lock:
            self._req_id += 1
            return self._req_id

    def _build(self, command, params=None, **extra):
        req = {"command": command, "id": self._next_id()}
        if params is not None:
            req["params"] = params
        req.update(extra)
        return req

    def _request_async(self, command, params=None, **extra):
        """Feuert ein Kommando ab, ohne auf die Antwort zu warten."""
        req = self._build(command, params=params, **extra)
        self.log(f"[{self.ip}] SEND(async): {json.dumps(req)}")
        self.ws.send(json.dumps(req))
        return req["id"]

    def _send_command(self, command, params=None, timeout=5, _direct=False, **extra):
        """
        Sendet ein Kommando und wartet auf die ID-korrelierte Antwort.
        _direct=True wird nur beim Login innerhalb _connect_once genutzt,
        bevor die Empfangs-Schleife läuft (synchroner recv).
        """
        req = self._build(command, params=params, **extra)
        self.log(f"[{self.ip}] SEND: {json.dumps(req)}")

        if _direct:
            self.ws.send(json.dumps(req))
            raw = self.ws.recv()
            return json.loads(raw)

        if not self.ws or not self._connected:
            self.log(f"[{self.ip}] Nicht verbunden, Kommando verworfen: {command}")
            return None

        event = threading.Event()
        holder = {"result": None}
        self._pending[req["id"]] = (event, holder)
        self.ws.send(json.dumps(req))

        if event.wait(timeout):
            return holder["result"]
        self._pending.pop(req["id"], None)
        self.log(f"[{self.ip}] Timeout für {command}")
        return None
