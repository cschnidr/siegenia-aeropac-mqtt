#!/usr/bin/env python3
"""
test_against_mock.py — startet den Mock-Server und testet das siegenia.py-Modul:
- Login + Verbindung
- initialer State-Pull
- set_fanlevel -> erwartet update-Push
- set_active(False) -> erwartet update-Push mit fanlevel 0
"""

import subprocess
import sys
import threading
import time

# siegenia.py mit gepatchtem Port/Pfad testen: der Mock lauscht auf 8443
from siegenia import SiegeniaDevice

updates = []


def on_update(data):
    updates.append(data)
    print(f"  [TEST] on_update: {data}")


def on_connect():
    print("  [TEST] on_connect")


def main():
    # Mock-Server starten
    proc = subprocess.Popen(
        [sys.executable, "mock_siegenia_server.py"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    time.sleep(2.5)  # Server hochfahren lassen (inkl. Cert-Erstellung)

    try:
        dev = SiegeniaDevice(
            ip="127.0.0.1", user="admin", password="8304", port=8443,
            on_update=on_update, on_connect=on_connect,
            logger=lambda m: print(f"  [LOG] {m}"),
        )
        dev.start()
        time.sleep(2)

        assert dev.connected, "FAIL: Gerät nicht verbunden"
        print("PASS: Verbindung + Login OK")

        # warten auf initialen State-Pull
        time.sleep(1)
        assert any("fanlevel" in u for u in updates), "FAIL: kein initialer fanlevel-Pull"
        print("PASS: initialer State-Pull OK")

        # set_fanlevel
        updates.clear()
        dev.set_fanlevel(3)
        time.sleep(1.5)
        got = [u for u in updates if u.get("fanlevel") == 3]
        assert got, f"FAIL: kein Push mit fanlevel=3, updates={updates}"
        print("PASS: set_fanlevel(3) -> Push mit fanlevel=3")

        # set_active(False) -> fanlevel sollte 0 werden
        updates.clear()
        dev.set_active(False)
        time.sleep(1.5)
        off = [u for u in updates
               if u.get("devicestate", {}).get("deviceactive") is False]
        assert off, f"FAIL: kein 'aus'-Push, updates={updates}"
        assert off[-1].get("fanlevel") == 0, "FAIL: fanlevel nicht 0 nach aus"
        print("PASS: set_active(False) -> Push mit deviceactive=false, fanlevel=0")

        # set_timer_duration
        updates.clear()
        dev.set_timer_duration(2, 30)
        time.sleep(1.5)
        timer_dur = [u for u in updates
                     if u.get("timer", {}).get("duration") == {"hour": 2, "minute": 30}]
        assert timer_dur, f"FAIL: kein Push mit timer.duration=2h30m, updates={updates}"
        print("PASS: set_timer_duration(2, 30) -> Push mit timer.duration")

        # set_timer_enabled(True) -> enabled-Push + remainingtime-Push
        updates.clear()
        dev.set_timer_enabled(True)
        time.sleep(1.5)
        timer_on = [u for u in updates if u.get("timer", {}).get("enabled") is True]
        assert timer_on, f"FAIL: kein Push mit timer.enabled=true, updates={updates}"
        timer_rem = [u for u in updates if "remainingtime" in u.get("timer", {})]
        assert timer_rem, f"FAIL: kein remainingtime-Push nach Timer-Start, updates={updates}"
        print("PASS: set_timer_enabled(True) -> enabled-Push + remainingtime-Push")

        # set_timer_enabled(False) -> enabled=false + remainingtime=0
        updates.clear()
        dev.set_timer_enabled(False)
        time.sleep(1.5)
        timer_off = [u for u in updates
                     if u.get("timer", {}).get("enabled") is False
                     and u.get("timer", {}).get("remainingtime") == {"hour": 0, "minute": 0}]
        assert timer_off, f"FAIL: kein korrekter Abbruch-Push, updates={updates}"
        print("PASS: set_timer_enabled(False) -> enabled=false + remainingtime=0:00")

        dev.stop()
        time.sleep(0.5)
        print("\n=== ALLE TESTS BESTANDEN ===")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
