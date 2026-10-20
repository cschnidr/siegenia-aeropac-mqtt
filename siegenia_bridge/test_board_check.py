#!/usr/bin/env python3
"""
test_board_check.py — verifies the main-board health check in DeviceBridge.

Real-world failure (AEROPAC, FW 1.7.7): the Wi-Fi module loses its link to the
fan's main board. WebSocket, login and keepAlive keep working, so the device
looked "online" for over a day while its state was stale and every write
failed. Only getDevice shows it: hardwareversion "UNAVAILABLE".

The mock answers keepAlive normally and switches getDevice between a healthy
and an unavailable board. The bridge must
  1. publish board=ok / online=online while healthy,
  2. go board=unavailable + online=offline after board_fail_limit failures,
  3. recover to board=ok + online=online once the board answers again,
  4. never overwrite status/raw with getDevice metadata.
"""

import asyncio
import json
import os
import ssl
import subprocess
import threading
import time

import websockets

from bridge import DeviceBridge
from siegenia import board_status

PORT = 18445
BOARD = {"ok": True}

HEALTHY = {"hardwareversion": "1.11", "serialnr": "mock0001", "type": 1}
DEAD = {"hardwareversion": "UNAVAILABLE", "serialnr": "00000000", "type": 0}


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


async def _handler(ws):
    async for raw in ws:
        req = json.loads(raw)
        cmd, rid = req.get("command"), req.get("id")
        if cmd == "login":
            data = {"isadmin": True}
        elif cmd == "getDevice":
            data = dict(HEALTHY if BOARD["ok"] else DEAD)
        elif cmd in ("getDeviceParams", "getDeviceState"):
            data = {"fanlevel": 0, "devicestate": {"deviceactive": False}}
        else:  # keepAlive and anything else: Wi-Fi module answers fine
            data = None
        msg = {"id": rid, "status": "ok"}
        if data is not None:
            msg["data"] = data
        await ws.send(json.dumps(msg))


async def _serve(stop):
    certfile, keyfile = _make_self_signed_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile, keyfile)
    async with websockets.serve(_handler, "127.0.0.1", PORT, ssl=ctx):
        await stop


def _run_server_in_thread():
    loop = asyncio.new_event_loop()
    stop = loop.create_future()

    def runner():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_serve(stop))

    threading.Thread(target=runner, daemon=True).start()
    return loop, stop


class FakeMQTT:
    """Records the last value published per topic."""

    def __init__(self):
        self.last = {}

    def publish(self, topic, value, retain=False):
        self.last[topic] = str(value)

    def subscribe(self, topic):
        pass


def _wait_for(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return pred()


def test_board_status_parsing():
    assert board_status(HEALTHY) == "ok"
    assert board_status(DEAD) == "unavailable"
    assert board_status({"hardwareversion": "UNAVAILABLE"}) == "unavailable"
    assert board_status({"serialnr": "00000000"}) == "unavailable"
    assert board_status({}) is None, "missing fields must not count as failure"
    assert board_status(None) is None
    print("PASS: board_status() parsing")


def main():
    test_board_status_parsing()

    loop, stop = _run_server_in_thread()
    time.sleep(2.0)

    mq = FakeMQTT()
    base = "siegenia/test"
    db = DeviceBridge(mq, "siegenia", {
        "id": "test", "ip": "127.0.0.1", "user": "admin", "password": "x",
        "port": PORT, "heartbeat_interval": 30,
        "board_check_interval": 2, "board_fail_limit": 2,
    })
    db.start()

    online = lambda: mq.last.get(f"{base}/status/online")
    board = lambda: mq.last.get(f"{base}/status/board")

    # 1. healthy
    assert _wait_for(lambda: board() == "ok" and online() == "online", 10), \
        f"FAIL: healthy board not reported, last={mq.last}"
    print("PASS: healthy board -> board=ok, online=online")

    # 4. getDevice metadata must not leak into status/raw
    raw = mq.last.get(f"{base}/status/raw", "")
    assert "hardwareversion" not in raw, f"FAIL: getDevice overwrote raw: {raw}"
    print("PASS: status/raw not overwritten by getDevice")

    # 2. main board dies; keepAlive still answered
    BOARD["ok"] = False
    assert _wait_for(lambda: board() == "unavailable" and online() == "offline", 10), \
        f"FAIL: dead board not detected, last={mq.last}"
    assert db.device.connected, "FAIL: WebSocket should stay connected"
    print("PASS: dead board -> board=unavailable, online=offline (socket still up)")

    # 3. board recovers
    BOARD["ok"] = True
    assert _wait_for(lambda: board() == "ok" and online() == "online", 10), \
        f"FAIL: recovery not reported, last={mq.last}"
    print("PASS: board recovered -> board=ok, online=online")

    db.device.stop()
    time.sleep(0.5)
    loop.call_soon_threadsafe(lambda: stop.set_result(None))
    print("\n=== BOARD CHECK TEST PASSED ===")


if __name__ == "__main__":
    main()
