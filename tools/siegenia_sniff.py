"""
mitmproxy addon: loggt nur WebSocket-Frames von/zu 192.168.37.50.
Nutzung:
    mitmdump -p 8888 --ssl-insecure -s siegenia_sniff.py
"""

TARGET_IP = "192.168.37.50"


def websocket_message(flow):
    if TARGET_IP not in flow.request.pretty_host:
        return
    msg = flow.websocket.messages[-1]
    direction = "APP -> AEROPAC" if msg.from_client else "AEROPAC -> APP"
    try:
        text = msg.content.decode("utf-8", errors="replace")
    except Exception:
        text = repr(msg.content)
    print(f"\n[{direction}] {text}")
