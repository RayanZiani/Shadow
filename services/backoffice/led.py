"""Pilotage du bandeau LED ESP8266 — contrat d'état, sans broker.

L'ESP8266 est **client** : il interroge `GET /led/state` toutes les 300 ms.
Pourquoi pas un POST du serveur vers l'ESP ? Parce que le PC hôte est le point
d'accès : la carte est derrière le NAT du partage de connexion, et un conteneur
Docker qui doit joindre une IP du sous-réseau du hotspot est une cause de panne
classique en démonstration. Dans ce sens (ESP → hôte), il n'y a ni NAT, ni
règle de pare-feu entrante à ouvrir.

Conséquence assumée : la latence est de 300 ms au pire. Le firmware applique
lui-même les temporisations (30 s de vert fixe, 10 clignotements rouges), donc
il continue à obéir même si le back-office disparaît.

**Double minuterie, volontaire.** Le service expire aussi ses états : si la
carte redémarre en plein milieu d'une séquence, elle relit un état déjà expiré
et ne rallume pas un vert vieux de deux minutes. Sans cela, un ESP qui reboote
rejouerait le dernier vert indéfiniment — exactement le mensonge que le
firmware cherche à éviter.
"""
from __future__ import annotations

import threading
import time

from security import constant_time_equals

VALID_STATES = ("off", "green_blink", "green_solid", "red_blink", "red_solid")

# Le firmware a les mêmes valeurs par défaut. Elles sont envoyées explicitement
# dans chaque trame pour qu'une évolution du contrat ne casse pas la carte.
DEFAULT_SOLID_SECONDS = 30
DEFAULT_RED_BLINKS = 10
DEFAULT_BLINK_MS = 180  # demi-période du rouge clignotant, côté firmware
GREEN_BLINK_TTL = 120  # vert clignotant : abandon si le mot de passe n'arrive pas


def _duration(state: str, seconds: int, blinks: int) -> float:
    """Durée pendant laquelle l'état reste valide côté service."""
    if state in ("green_solid", "red_solid"):
        return float(seconds or DEFAULT_SOLID_SECONDS)
    if state == "red_blink":
        count = blinks or DEFAULT_RED_BLINKS
        # 10 flashs = 10 × (allumé + éteint) ; marge d'un cycle.
        return (count * 2 + 1) * (DEFAULT_BLINK_MS / 1000.0)
    if state == "green_blink":
        return float(GREEN_BLINK_TTL)
    return 0.0


class LedController:
    def __init__(self, token: str, enabled: bool = True):
        self._token = token or ""
        self._enabled = enabled
        self._lock = threading.Lock()
        self._state = "off"
        self._seconds = 0
        self._blinks = 0
        self._seq = 1
        self._changed_at = time.time()
        self._expires_at = 0.0
        self._last_seen: float | None = None
        self._last_device: str | None = None
        self._polls = 0

    # --- Commandes (appelées par les routes d'authentification) -------------

    def set_state(self, state: str, seconds: int = 0, blinks: int = 0) -> None:
        if state not in VALID_STATES:
            raise ValueError(f"état LED inconnu : {state!r}")
        if state == "green_solid":
            seconds = seconds or DEFAULT_SOLID_SECONDS
        if state == "red_solid":
            seconds = seconds or DEFAULT_SOLID_SECONDS
        if state == "red_blink":
            blinks = blinks or DEFAULT_RED_BLINKS
        now = time.time()
        with self._lock:
            self._state = state
            self._seconds = int(seconds)
            self._blinks = int(blinks)
            self._seq += 1
            self._changed_at = now
            duration = _duration(state, self._seconds, self._blinks)
            self._expires_at = now + duration if duration else 0.0

    def off(self) -> None:
        self.set_state("off")

    def _expire_if_needed(self, now: float) -> None:
        """Passe à `off` si la séquence est terminée. Appelé sous verrou."""
        if self._state != "off" and self._expires_at and now >= self._expires_at:
            self._state = "off"
            self._seconds = 0
            self._blinks = 0
            self._expires_at = 0.0
            self._seq += 1
            self._changed_at = now

    # --- Lecture par l'ESP8266 ---------------------------------------------

    def snapshot(self, device: str, token: str) -> dict:
        if not constant_time_equals(token, self._token):
            raise PermissionError("jeton du bandeau LED invalide")
        now = time.time()
        with self._lock:
            self._expire_if_needed(now)
            self._last_seen = now
            self._last_device = (device or "unknown")[:64]
            self._polls += 1
            return {
                "enabled": self._enabled,
                "seq": self._seq,
                "state": self._state if self._enabled else "off",
                "seconds": self._seconds,
                "blinks": self._blinks,
                "remaining": (
                    round(max(0.0, self._expires_at - now), 1) if self._expires_at else 0
                ),
                "ts": now,
            }

    # --- Observabilité ------------------------------------------------------

    def health(self) -> dict:
        with self._lock:
            self._expire_if_needed(time.time())
            last_seen = (
                None if self._last_seen is None else round(time.time() - self._last_seen, 3)
            )
            return {
                "enabled": self._enabled,
                "state": self._state,
                "seq": self._seq,
                "seconds": self._seconds,
                "blinks": self._blinks,
                "last_seen_seconds": last_seen,
                "last_device": self._last_device,
                "polls": self._polls,
            }

    @property
    def state(self) -> str:
        with self._lock:
            self._expire_if_needed(time.time())
            return self._state
