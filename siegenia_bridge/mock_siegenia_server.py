#!/usr/bin/env python3
"""
mock_siegenia_server.py — simuliert ein AEROPAC für lokale Tests der Bridge.
Implementiert das verifizierte Protokoll: login, keepAlive, getDeviceParams,
getDeviceState, setDeviceParams (fanlevel + devicestate.deviceactive) und
sendet bei Änderungen unaufgeforderte "update"-Pushes.
"""

import asyncio
import json
import ssl
import os
import subprocess

import websockets

STATE = {
    "fanlevel": 0,
    "deviceactive": False,
    "devicename": "AEROPAC Mock",
    "timer": {
        "enabled": False,
        "duration": {"hour": 0, "minute": 0},
        "remainingtime": {"hour": 0, "minute": 0},
    },
}

CLIENTS = set()


async def push_update(extra_data):
    """Sendet unaufgeforderten update-Push an alle verbundenen Clients."""
    msg = json.dumps({
        "command": "deviceParams",
        "data": extra_data,
        "status": "update",
    })
    for ws in list(CLIENTS):
        try:
            await ws.send(msg)
        except Exception:
            pass


async def handler(ws):
    CLIENTS.add(ws)
    try:
        async for raw in ws:
            req = json.loads(raw)
            cmd = req.get("command")
            rid = req.get("id")

            if cmd == "login":
                await ws.send(json.dumps({
                    "data": {"isadmin": True, "token": "MOCKTOKEN", "user": req.get("user")},
                    "id": rid, "status": "ok",
                }))

            elif cmd == "keepAlive":
                await ws.send(json.dumps({"id": rid, "status": "ok"}))

            elif cmd == "getDeviceState":
                await ws.send(json.dumps({
                    "data": {"deviceactive": STATE["deviceactive"]},
                    "id": rid, "status": "ok",
                }))

            elif cmd == "getDeviceParams":
                await ws.send(json.dumps({
                    "data": {
                        "fanlevel": STATE["fanlevel"],
                        "devicename": STATE["devicename"],
                        "devicestate": {"deviceactive": STATE["deviceactive"]},
                    },
                    "id": rid, "status": "ok",
                }))

            elif cmd == "setDeviceParams":
                params = req.get("params", {})
                # Erst ok quittieren
                await ws.send(json.dumps({"id": rid, "status": "ok"}))
                await asyncio.sleep(0.3)

                if "fanlevel" in params:
                    STATE["fanlevel"] = params["fanlevel"]
                    await push_update({
                        "fanlevel": STATE["fanlevel"],
                        "devicestate": {"deviceactive": STATE["deviceactive"]},
                    })
                elif "devicestate" in params and "deviceactive" in params["devicestate"]:
                    STATE["deviceactive"] = params["devicestate"]["deviceactive"]
                    if not STATE["deviceactive"]:
                        STATE["fanlevel"] = 0
                    await push_update({
                        "fanlevel": STATE["fanlevel"],
                        "devicestate": {"deviceactive": STATE["deviceactive"]},
                    })
                elif "timer" in params:
                    t = params["timer"]
                    if "duration" in t:
                        STATE["timer"]["duration"] = dict(t["duration"])
                        await push_update({"timer": {"duration": STATE["timer"]["duration"]}})
                    elif "enabled" in t:
                        STATE["timer"]["enabled"] = t["enabled"]
                        if t["enabled"]:
                            # Timer gestartet: enabled-Push, dann remainingtime-Push
                            await push_update({"timer": {"enabled": True}})
                            await asyncio.sleep(0.1)
                            await push_update({"timer": {"remainingtime": STATE["timer"]["duration"]}})
                        else:
                            # Timer abgebrochen
                            STATE["timer"]["remainingtime"] = {"hour": 0, "minute": 0}
                            await push_update({"timer": {
                                "enabled": False,
                                "remainingtime": {"hour": 0, "minute": 0},
                            }})

            else:
                await ws.send(json.dumps({"id": rid, "status": "ok"}))
    finally:
        CLIENTS.discard(ws)


def make_self_signed_cert():
    certfile = "/tmp/mock_cert.pem"
    keyfile = "/tmp/mock_key.pem"
    if not os.path.exists(certfile):
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", keyfile, "-out", certfile,
            "-days", "1", "-nodes", "-subj", "/CN=mock",
        ], check=True, capture_output=True)
    return certfile, keyfile


async def main():
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8443
    if len(sys.argv) > 2:
        STATE["devicename"] = sys.argv[2]

    certfile, keyfile = make_self_signed_cert()
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(certfile, keyfile)

    async with websockets.serve(handler, "127.0.0.1", port, ssl=ssl_ctx):
        print(f"Mock-Siegenia-Server '{STATE['devicename']}' "
              f"läuft auf wss://127.0.0.1:{port}/WebSocket")
        await asyncio.Future()  # läuft bis Abbruch


if __name__ == "__main__":
    asyncio.run(main())
