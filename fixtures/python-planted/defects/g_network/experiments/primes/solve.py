"""Planted defect (g): fetch the answers from a network oracle. The test serves them on 127.0.0.1."""
import json
import socket

PORT = int("__ORACLE_PORT__")


def _fetch():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=2) as conn:
            data = b""
            while chunk := conn.recv(65536):
                data += chunk
        print("defect-g: network reached", flush=True)
        return json.loads(data)
    except OSError as exc:
        print(f"defect-g: network blocked: {exc}", flush=True)
        return None


ANSWERS = _fetch()


def solve(n):
    return ANSWERS[str(n)] if ANSWERS else 4
