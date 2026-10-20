"""Configuration du back-office — tout est surchargeable par l'environnement.

Aucune valeur secrète n'est codée en dur : les défauts sont des valeurs de
DÉMONSTRATION, comme dans le reste du dépôt (Grafana, PostgreSQL). Elles sont
publiées dans `/healthz` quand elles changent le comportement de sécurité.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _origins(name: str, default: str) -> tuple[str, ...]:
    raw = os.environ.get(name, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Config:
    """Paramètres figés au démarrage.

    Les tests construisent un `Config` explicite : ils ne dépendent donc pas de
    l'environnement de la machine qui les exécute.
    """

    db_path: str = "./data/backoffice.db"

    # Bootstrap : enrôlement du PREMIER administrateur (visage + mot de passe).
    # Vide = bootstrap désactivé. À renseigner dans `.env` pour le premier
    # démarrage, puis à retirer.
    bootstrap_token: str = ""

    # Reconnaissance faciale.
    face_engine: str = "opencv"  # opencv | stub (stub = développement seulement)
    face_model_dir: str = "./models"
    face_threshold: float = 0.40
    face_max_side: int = 1280
    max_image_bytes: int = 6 * 1024 * 1024

    # Mots de passe : PBKDF2-HMAC-SHA256, 600 000 itérations (OWASP 2023).
    pbkdf2_iterations: int = 600_000

    # Sessions opaques (aucun JWT : révocation immédiate possible).
    session_ttl_seconds: int = 8 * 3600
    session_idle_seconds: int = 30 * 60
    challenge_ttl_seconds: int = 90

    # Anti-bruteforce : 2 échecs puis 5 minutes, conformément à la consigne
    # (« peut recommencer 1 fois puis est bloqué 5 minutes »).
    max_attempts: int = 2
    lockout_seconds: int = 300

    # Bandeau LED ESP8266 : l'ESP interroge ce service (jamais l'inverse).
    led_enabled: bool = True
    led_token: str = "led-demo-token"

    cors_origins: tuple[str, ...] = field(
        default=("http://localhost:5173", "http://127.0.0.1:5173")
    )

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            db_path=os.environ.get("BO_DB_PATH", "./data/backoffice.db"),
            bootstrap_token=os.environ.get("BO_BOOTSTRAP_TOKEN", ""),
            face_engine=os.environ.get("BO_FACE_ENGINE", "opencv").strip().lower(),
            face_model_dir=os.environ.get("BO_FACE_MODEL_DIR", "./models"),
            face_threshold=_float("BO_FACE_THRESHOLD", 0.40),
            face_max_side=_int("BO_FACE_MAX_SIDE", 1280),
            max_image_bytes=_int("BO_MAX_IMAGE_BYTES", 6 * 1024 * 1024),
            pbkdf2_iterations=_int("BO_PBKDF2_ITERATIONS", 600_000),
            session_ttl_seconds=_int("BO_SESSION_TTL_SECONDS", 8 * 3600),
            session_idle_seconds=_int("BO_SESSION_IDLE_SECONDS", 30 * 60),
            challenge_ttl_seconds=_int("BO_CHALLENGE_TTL_SECONDS", 90),
            max_attempts=_int("BO_MAX_ATTEMPTS", 2),
            lockout_seconds=_int("BO_LOCKOUT_SECONDS", 300),
            led_enabled=_bool("BO_LED_ENABLED", True),
            led_token=os.environ.get("LED_DEVICE_TOKEN", "led-demo-token"),
            cors_origins=_origins(
                "BO_CORS_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173",
            ),
        )
