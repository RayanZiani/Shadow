#!/usr/bin/env python3
"""hardware_bridge.py — pont read-only entre remediation-engine et le terminal.

    remediation-engine  --SSE /events-->  CE PONT  --USB série-->  ESP32

Principes (cf. docs/hardware/CONCEPT-PHYSIQUE.md) :

* Le pont ne publie RIEN vers remediation-engine. Il est strictement en lecture.
* Si le pont tombe, la remédiation continue : aucun abonné n'est obligatoire.
* À chaque (re)connexion, le pont relit /state pour se resynchroniser, puis
  reprend le flux /events. Les doublons sont filtrés par `seq`.
* En cas de port série absent, le pont continue de tourner et retente : il ne
  doit jamais faire échouer la démo.

Usage :
    python hardware_bridge.py --list-ports
    python hardware_bridge.py --port COM3
    python hardware_bridge.py --port COM3 --api http://localhost:8084
    python hardware_bridge.py --dry-run                 # sans matériel
    python hardware_bridge.py --replay                  # répétition, sans attaque

Dépendances : pyserial (pip install pyserial). Le reste est stdlib.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - dépend de la machine de démo
    serial = None
    list_ports = None


# États alignés sur remediation-engine et sur le firmware.
VALID_STATES = {
    "IDLE",
    "NORMAL_TX",
    "UNDER_ATTACK",
    "REMEDIATING",
    "RECOVERED_TX",
    "REMEDIATION_FAILED",
}

RECONNECT_DELAY = 2.0
SERIAL_RETRY_DELAY = 3.0

# Console Windows en cp1252 : sans ça, les accents deviennent des '?' dans les
# logs, ce qui rend la sortie inutilisable en cas de diagnostic pendant la démo.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def list_serial_ports() -> None:
    if list_ports is None:
        log("pyserial absent : pip install pyserial")
        return
    ports = list(list_ports.comports())
    if not ports:
        log("aucun port série détecté")
        return
    for port in ports:
        log(f"{port.device:<12} {port.description}")


class SerialLink:
    """Port série tolérant aux pannes : ouvert à la demande, jamais bloquant."""

    def __init__(self, port: str | None, baud: int, dry_run: bool) -> None:
        self.port = port
        self.baud = baud
        self.dry_run = dry_run
        self._serial = None
        self._last_attempt = 0.0

    def _ensure_open(self) -> bool:
        if self.dry_run:
            return True
        if self._serial is not None and self._serial.is_open:
            return True
        if serial is None:
            return False
        if time.time() - self._last_attempt < SERIAL_RETRY_DELAY:
            return False
        self._last_attempt = time.time()

        port = self.port or self._autodetect()
        if not port:
            log("aucun port série : nouveau essai dans quelques secondes")
            return False
        try:
            self._serial = serial.Serial(port, self.baud, timeout=0.5, write_timeout=1.0)
            # L'ESP32 redémarre à l'ouverture du port : on lui laisse le temps
            # de booter avant le premier octet, sinon la trame est perdue.
            time.sleep(1.5)
            self._serial.reset_input_buffer()
            log(f"port série ouvert : {port} @ {self.baud}")
            return True
        except Exception as exc:  # noqa: BLE001 - on ne veut jamais crasher
            log(f"échec d'ouverture du port {port}: {exc}")
            self._serial = None
            return False

    def _autodetect(self) -> str | None:
        if list_ports is None:
            return None
        for candidate in list_ports.comports():
            description = (candidate.description or "").lower()
            if any(tag in description for tag in ("cp210", "ch340", "ftdi", "usb serial")):
                return candidate.device
        return None

    def send(self, frame: str) -> None:
        if not self._ensure_open():
            return
        if self.dry_run:
            log(f"DRY-RUN -> {frame}")
            return
        try:
            assert self._serial is not None
            self._serial.write((frame + "\n").encode("ascii", "ignore"))
            self._serial.flush()
            log(f"-> {frame}")
        except Exception as exc:  # noqa: BLE001
            log(f"écriture série échouée: {exc}")
            self.close()

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:  # noqa: BLE001
                pass
            self._serial = None


def build_frame(event: dict, rich: bool) -> str:
    state = event.get("state", "IDLE")
    if state not in VALID_STATES:
        state = "IDLE"
    frame = f"STATE:{state}"
    if rich:
        detail = event.get("detail") or {}
        score = detail.get("anomaly_score")
        ring_n = detail.get("ring_n")
        if isinstance(score, (int, float)):
            frame += f";SCORE:{score:.2f}"
        if isinstance(ring_n, int):
            frame += f";N:{ring_n}"
    return frame


def fetch_state(api: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{api}/state", timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        log(f"état initial indisponible ({exc})")
        return None


def stream_events(api: str):
    """Générateur SSE minimal en stdlib. Rend un événement par `data:`."""
    request = urllib.request.Request(f"{api}/events", headers={"Accept": "text/event-stream"})
    with urllib.request.urlopen(request, timeout=45) as response:
        buffer: list[str] = []
        while True:
            raw = response.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("data:"):
                buffer.append(line[5:].strip())
            elif line == "" and buffer:
                payload = "\n".join(buffer)
                buffer.clear()
                try:
                    yield json.loads(payload)
                except ValueError:
                    log(f"trame SSE illisible ignorée: {payload[:120]}")


def run(api: str, link: SerialLink, rich: bool) -> None:
    last_seq = -1
    while True:
        try:
            initial = fetch_state(api)
            if initial and initial.get("seq", -1) > last_seq:
                last_seq = initial["seq"]
                link.send(build_frame(initial, rich))

            for event in stream_events(api):
                seq = event.get("seq", -1)
                if seq <= last_seq:
                    continue  # doublon après reconnexion
                last_seq = seq
                link.send(build_frame(event, rich))

        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - reconnexion systématique
            log(f"flux interrompu ({exc}) — reconnexion dans {RECONNECT_DELAY:.0f}s")
            time.sleep(RECONNECT_DELAY)


def replay(link: SerialLink) -> None:
    """Répétition sans attaque : rejoue la séquence exacte du Clip Chrono.

    C'est ce mode qui permet de régler les LEDs, le cadrage et le rythme sans
    toucher au PC attaquant ni à Ansible.
    """
    script = [
        ("NORMAL_TX", 3.0),
        ("UNDER_ATTACK", 4.0),
        ("REMEDIATING", 6.0),
        ("RECOVERED_TX", 5.0),
    ]
    log("mode répétition — Ctrl+C pour arrêter")
    while True:
        for state, duration in script:
            link.send(f"STATE:{state}")
            time.sleep(duration)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pont remediation-engine -> ESP32")
    parser.add_argument("--api", default="http://localhost:8084", help="URL de remediation-engine")
    parser.add_argument("--port", default=None, help="port série (ex: COM3)")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--rich", action="store_true", help="ajoute SCORE et N à la trame")
    parser.add_argument("--dry-run", action="store_true", help="n'ouvre pas le port série")
    parser.add_argument("--replay", action="store_true", help="rejoue la séquence de démo")
    parser.add_argument("--list-ports", action="store_true", help="liste les ports série")
    args = parser.parse_args()

    if args.list_ports:
        list_serial_ports()
        return 0

    link = SerialLink(args.port, args.baud, args.dry_run)
    log(f"source : {args.api}")
    try:
        if args.replay:
            replay(link)
        else:
            run(args.api, link, args.rich)
    except KeyboardInterrupt:
        log("arrêt demandé")
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
