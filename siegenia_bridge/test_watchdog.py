#!/usr/bin/env python3
"""
test_watchdog.py — verifies the keepAlive watchdog detects a *silent* device
failure (the socket still looks ESTABLISHED but the device stops answering).

Starts a mock server that accepts login + state queries but silently drops
every keepAlive. The SiegeniaDevice must then, after missed_heartbeat_limit
unanswered pings, force the connection closed and fire on_disconnect
(-> MQTT online=offline) instead of staying falsely online.
"""

import asyncio
import json
import os
import ssl
import subprocess
import threading
import time

import websockets

from siegenia import SiegeniaDevice

PORT = 18444


def _make_self_signed_cert():
    certfile = "/tmp/mock_cert.pem"
    keyfile = "/tmp/mock_key.pem"
    if not os.path.exists(certfile):
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", keyfile, "-out", certfile,
            "-days", "1", "-nodes", "-subj", "/CN=mock",
        ], check=True, capture_output=True)
    return certfile, keyfile


async def _silent_handler(ws):
    """Answers login and state queries but NEVER replies to keepAlive."""
    async for raw in ws:
        req = json.loads(raw)
        cmd = req.get("command")
        rid = req.get("id")
        if cmd == "login":
            await ws.send(json.dumps({"data": {"isadmin": True}, "id": rid, "status": "ok"}))
        elif cmd == "keepAlive":
            pass  # silent failure: no reply
        elif cmd in ("getDeviceParams", "getDeviceState"):
            await ws.send(json.dumps({
                "data": {"fanlevel": 0, "devicestate": {"deviceactive": False}},
                "id": rid, "status": "ok",
            }))
        else:
            await ws.send(json.dumps({"id": rid, "status": "ok"}))


async def _serve(stop):
    certfile, keyfile = _make_self_signed_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile, keyfile)
    async with websockets.serve(_silent_handler, "127.0.0.1", PORT, ssl=ctx):
        await stop


def _run_server_in_thread():
    loop = asyncio.new_event_loop()
    stop = loop.create_future()

    def runner():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_serve(stop))

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    return loop, stop


def main():
    loop, stop = _run_server_in_thread()
    time.sleep(2.0)

    events = {"connect": 0, "disconnect": 0}
    logs = []
    dev = SiegeniaDevice(
        ip="127.0.0.1", user="admin", password="x", port=PORT,
        on_connect=lambda: events.__setitem__("connect", events["connect"] + 1),
        on_disconnect=lambda: events.__setitem__("disconnect", events["disconnect"] + 1),
        logger=lambda m: logs.append(m),
        heartbeat_interval=2,
        heartbeat_timeout=2,
        missed_heartbeat_limit=2,
    )
    dev.start()
    time.sleep(3)
    assert dev.connected, "FAIL: device did not connect to mock"
    assert events["connect"] >= 1, "FAIL: on_connect not fired"
    print("PASS: connected to silent mock")

    # Wait for the watchdog: 2 pings * (2s interval + 2s timeout) + margin
    time.sleep(12)

    assert events["disconnect"] >= 1, \
        f"FAIL: watchdog did not fire on_disconnect, logs={logs}"
    assert any("dead" in l or "tot" in l for l in logs), \
        f"FAIL: no watchdog log line, logs={logs}"
    print("PASS: silent failure detected -> on_disconnect fired, reconnect started")

    dev.stop()
    time.sleep(0.5)
    loop.call_soon_threadsafe(lambda: stop.set_result(None))
    print("\n=== WATCHDOG TEST PASSED ===")


if __name__ == "__main__":
    main()
