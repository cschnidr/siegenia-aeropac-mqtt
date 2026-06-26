#!/usr/bin/env python3
"""
siegenia_cli.py — Standalone-CLI zum Testen/Steuern eines SIEGENIA-Geräts,
ohne MQTT/Bridge. Nutzt das siegenia.py-Modul aus ../siegenia_bridge.

Beispiele:
    # Aktuellen Zustand anzeigen (Account 'user' wie die App)
    python3 siegenia_cli.py 192.168.37.50 user 8304 status

    # Lüfterstufe setzen (0-7)
    python3 siegenia_cli.py 192.168.37.50 user 8304 fan 3

    # Ein- / Ausschalten
    python3 siegenia_cli.py 192.168.37.50 user 8304 on
    python3 siegenia_cli.py 192.168.37.50 user 8304 off

    # Live auf Push-Updates lauschen (Ctrl+C zum Beenden)
    python3 siegenia_cli.py 192.168.37.50 user 8304 watch
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "siegenia_bridge"))
from siegenia import SiegeniaDevice  # noqa: E402


def _resolve_active(state):
    """Ermittelt den aktuellen Aktiv-Zustand. Der verschachtelte
    devicestate.deviceactive ist neuer/aktueller als ein evtl. vorhandener
    Top-Level-deviceactive aus dem initialen Pull."""
    ds = state.get("devicestate")
    if isinstance(ds, dict) and "deviceactive" in ds:
        return ds["deviceactive"]
    return state.get("deviceactive")


def _fmt_state(state):
    fanlevel = state.get("fanlevel", "?")
    active = _resolve_active(state)
    return f"Aktueller Stand: fanlevel={fanlevel}, aktiv={active}"


def main():
    ap = argparse.ArgumentParser(description="SIEGENIA CLI (ohne MQTT)")
    ap.add_argument("ip")
    ap.add_argument("user")
    ap.add_argument("password")
    ap.add_argument("action", choices=["status", "fan", "on", "off", "watch"])
    ap.add_argument("value", nargs="?", help="Wert für 'fan' (0-7)")
    ap.add_argument("--port", type=int, default=443)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    last = {}

    def on_update(data):
        last.update(data)
        if args.action == "watch":
            print(f"[update] {json.dumps(data)}", flush=True)

    dev = SiegeniaDevice(
        ip=args.ip, user=args.user, password=args.password, port=args.port,
        on_update=on_update,
        logger=(lambda m: print(f"[log] {m}")) if args.verbose else None,
    )
    dev.start()

    # auf Verbindung warten
    for _ in range(50):
        if dev.connected:
            break
        time.sleep(0.1)
    if not dev.connected:
        print("FEHLER: Keine Verbindung zum Gerät.")
        dev.stop()
        sys.exit(1)

    try:
        if args.action == "status":
            time.sleep(1.5)  # initialen Pull abwarten
            active = _resolve_active(last)
            print(f"Gerät:       {last.get('devicename', '?')}")
            print(f"Aktiv:       {active}")
            print(f"Lüfterstufe: {last.get('fanlevel', '?')}")
            if last.get("warnings"):
                print(f"Warnungen:   {last['warnings']}")

        elif args.action == "fan":
            if args.value is None:
                print("FEHLER: 'fan' braucht einen Wert 0-7 (z.B. 'fan 3').")
                print("Zum Ausschalten nutze stattdessen das Kommando 'off'.")
                sys.exit(1)
            try:
                level = int(args.value)
            except ValueError:
                print(f"FEHLER: '{args.value}' ist keine gültige Lüfterstufe.")
                print("'fan' erwartet eine Zahl 0-7. Zum Ausschalten: 'off'.")
                sys.exit(1)
            if not 0 <= level <= 7:
                print(f"FEHLER: Stufe {level} ausserhalb des Bereichs 0-7.")
                sys.exit(1)
            if level > 0:
                dev.set_active(True)
                time.sleep(1.0)
            dev.set_fanlevel(level)
            time.sleep(1.5)
            print(f"fanlevel gesetzt auf {level}. {_fmt_state(last)}")

        elif args.action == "on":
            dev.set_active(True)
            time.sleep(1.5)
            print(f"Eingeschaltet. {_fmt_state(last)}")

        elif args.action == "off":
            dev.set_active(False)
            time.sleep(1.5)
            print(f"Ausgeschaltet. {_fmt_state(last)}")

        elif args.action == "watch":
            print("Lausche auf Push-Updates (Ctrl+C zum Beenden) ...")
            while True:
                time.sleep(1)

    except KeyboardInterrupt:
        pass
    finally:
        dev.stop()
        time.sleep(0.3)


if __name__ == "__main__":
    main()
