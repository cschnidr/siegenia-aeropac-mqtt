#!/usr/bin/env python3
"""measure_timeout.py - misst den keepAlive-Timeout eines SIEGENIA-Geraets.

Verbindet sich, loggt ein, sendet dann ABSICHTLICH keinen keepAlive und misst,
nach wie vielen Sekunden das Geraet die Verbindung kappt. Hilft, das
Heartbeat-Intervall fundiert festzulegen (Faustregel: Timeout / 2).

Nutzung:
    python3 measure_timeout.py 192.168.37.50 admin 8304
    python3 measure_timeout.py 192.168.37.50 admin 8304 --max-wait 180
"""
import argparse, json, ssl, time
import websocket

ap = argparse.ArgumentParser()
ap.add_argument("ip")
ap.add_argument("user")
ap.add_argument("password")
ap.add_argument("--port", type=int, default=443)
ap.add_argument("--max-wait", type=float, default=90.0)
args = ap.parse_args()

url = f"wss://{args.ip}:{args.port}/WebSocket"
print("Verbinde zu " + url + " ...")
ws = websocket.create_connection(url, sslopt={"cert_reqs": ssl.CERT_NONE}, origin=url, timeout=5)

login = {"command": "login", "id": 1, "user": args.user, "password": args.password, "long_life": False}
ws.send(json.dumps(login))
resp = json.loads(ws.recv())
if resp.get("status") != "ok":
    print("[FEHLER] Login fehlgeschlagen: " + str(resp))
    ws.close()
    raise SystemExit(1)
print("[OK] Login erfolgreich.")
print("Sende absichtlich keinen keepAlive und warte auf Trennung ...")

ws.settimeout(2.0)
start = time.time()
last = 0
while True:
    elapsed = time.time() - start
    if elapsed >= args.max_wait:
        print("[ERGEBNIS] Nach " + str(int(args.max_wait)) + "s noch NICHT getrennt. Mit --max-wait erhoehen.")
        break
    if int(elapsed) > last:
        last = int(elapsed)
        print("  ... " + str(last) + "s verbunden, keine Trennung")
    try:
        raw = ws.recv()
        if not raw:
            dt = time.time() - start
            print("[ERGEBNIS] Getrennt nach " + str(round(dt, 1)) + "s (leerer Frame).")
            print("  -> Timeout ~" + str(int(dt)) + "s, empfohlenes Heartbeat ~" + str(int(dt/2)) + "s")
            break
    except websocket.WebSocketTimeoutException:
        continue
    except Exception as e:
        dt = time.time() - start
        print("[ERGEBNIS] Getrennt nach " + str(round(dt, 1)) + "s (" + str(e) + ").")
        print("  -> Timeout ~" + str(int(dt)) + "s, empfohlenes Heartbeat ~" + str(int(dt/2)) + "s")
        break

try:
    ws.close()
except Exception:
    pass
