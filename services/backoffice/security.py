"""Primitives de sécurité du back-office.

Choix assumés :

* **PBKDF2-HMAC-SHA256**, 600 000 itérations, sel aléatoire de 16 octets.
  Aucune dépendance native (bcrypt/argon2 demandent une compilation) ; PBKDF2
  est recommandé par le NIST SP 800-63B et suffisant pour un back-office.
* **Sessions opaques** : le client reçoit 32 octets aléatoires, le serveur ne
  stocke que leur SHA-256. Un vol de base ne donne donc pas de session
  utilisable, et une révocation est immédiate (contrairement à un JWT).
* **Comparaisons à temps constant** (`hmac.compare_digest`) pour les mots de
  passe et les jetons.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
ROLES = ("admin", "operator", "auditor")
MIN_PASSWORD_LENGTH = 10


class PolicyError(ValueError):
    """Violation d'une règle de saisie (nom d'utilisateur, mot de passe)."""


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def validate_username(username: str) -> str:
    normalized = normalize_username(username)
    if not USERNAME_RE.match(normalized):
        raise PolicyError(
            "identifiant invalide : 3 à 32 caractères, minuscules, chiffres, . _ -"
        )
    return normalized


def validate_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise PolicyError(
            f"mot de passe trop court : {MIN_PASSWORD_LENGTH} caractères minimum"
        )
    if len(password) > 256:
        raise PolicyError("mot de passe trop long (256 caractères maximum)")
    return password


def validate_role(role: str) -> str:
    if role not in ROLES:
        raise PolicyError(f"rôle inconnu : {role!r} (attendu : {', '.join(ROLES)})")
    return role


def hash_password(password: str, iterations: int = 600_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Hachage factice : vérifié quand l'identifiant n'existe pas, pour que le temps
# de réponse ne permette pas d'énumérer les comptes. Le sel est fixe (ce n'est
# pas un secret) et le nombre d'itérations est celui de la configuration.
_DUMMY_SALT = b"shadow-settle-dummy"


def dummy_verify(password: str, iterations: int = 600_000) -> bool:
    hashlib.pbkdf2_hmac(
        "sha256", (password or "").encode("utf-8"), _DUMMY_SALT, iterations
    )
    return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_challenge_id() -> str:
    return secrets.token_urlsafe(18)


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest((left or "").encode("utf-8"), (right or "").encode("utf-8"))
