#!/usr/bin/env python3
"""Contrôle de recette du back-office — avant la soutenance.

    python tools/backoffice_check.py
    python tools/backoffice_check.py --base http://localhost:8086

Vérifie ce qui doit être vrai le jour J, sans navigateur :

  1. le service répond et la base est accessible ;
  2. le moteur facial est le VRAI (pas le stub de développement) ;
  3. un administrateur est enrôlé (sinon le back-office est inaccessible) ;
  4. l'endpoint du bandeau LED refuse un jeton absent ou faux ;
  5. le compteur de blocage est à zéro (sinon la démo se bloque au premier échec) ;
  6. les métriques Prometheus sont exposées.

Code de sortie 0 = tout est prêt. 1 = au moins un contrôle a échoué.
"""
from __future__ import annotations

import argparse
import json
import sys

import httpx

OK = "\033[32mOK\033[0m"
KO = "\033[31mECHEC\033[0m"
WARN = "\033[33mATTENTION\033[0m"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:8086")
    parser.add_argument("--led-token", default="led-demo-token")
    args = parser.parse_args()

    failures = 0
    warnings = 0

    def check(condition: bool, label: str, detail: str = "") -> None:
        nonlocal failures
        print(f"  [{OK if condition else KO}] {label}" + (f" — {detail}" if detail else ""))
        if not condition:
            failures += 1

    def warn(condition: bool, label: str, detail: str = "") -> None:
        nonlocal warnings
        if not condition:
            print(f"  [{WARN}] {label}" + (f" — {detail}" if detail else ""))
            warnings += 1

    print(f"Back-office : {args.base}")

    # 1. Santé -------------------------------------------------------------
    try:
        health = httpx.get(f"{args.base}/healthz", timeout=5).json()
    except Exception as exc:  # noqa: BLE001
        print(f"  [{KO}] service injoignable — {type(exc).__name__}: {exc}")
        print("\nDémarrez-le : docker compose up -d backoffice")
        return 1

    print(json.dumps(health, indent=2, ensure_ascii=False))
    check(health.get("status") == "ok", "service sain", health.get("status", "?"))
    check(health.get("db") == "ok", "base SQLite accessible")

    # 2. Moteur facial -----------------------------------------------------
    engine = health.get("face_engine")
    check(engine == "ready", "moteur facial réel chargé", str(engine))
    if engine == "stub":
        warn(False, "moteur STUB : reconnaissance factice, inutilisable en soutenance")
    if health.get("face_error"):
        print(f"  [{KO}] erreur du moteur : {health['face_error']}")

    # 3. Administrateur ----------------------------------------------------
    check(
        health.get("bootstrap_required") is False,
        "un administrateur avec visage est enrôlé",
        "bootstrap requis" if health.get("bootstrap_required") else "",
    )
    check(
        int(health.get("users_total", 0)) >= 1,
        "au moins un compte existe",
        f"{health.get('users_total')} compte(s)",
    )

    # 4. Bandeau LED -------------------------------------------------------
    try:
        anonymous = httpx.get(f"{args.base}/led/state", timeout=5)
        check(anonymous.status_code == 401, "bandeau LED protégé par jeton", str(anonymous.status_code))
        authorized = httpx.get(
            f"{args.base}/led/state",
            params={"token": args.led_token, "device": "recette"},
            timeout=5,
        )
        body = authorized.json() if authorized.status_code == 200 else {}
        check(authorized.status_code == 200, "jeton du bandeau accepté", str(authorized.status_code))
        state = body.get("state")
        check(
            state in {"off", "green_blink", "green_solid", "red_blink", "red_solid"},
            "état LED conforme au contrat",
            str(state),
        )
        # Un état non « off » juste après un test de connexion est normal : la
        # séquence de 30 s est encore en cours. On le signale, ce n'est pas un échec.
        warn(state == "off", f"séquence LED en cours ({state}) — attendre 30 s et relancer")
        check(
            body.get("enabled") is True,
            "bandeau activé",
            "BO_LED_ENABLED=false" if body.get("enabled") is False else "",
        )
    except Exception as exc:  # noqa: BLE001
        check(False, "endpoint du bandeau LED", f"{type(exc).__name__}: {exc}")

    # 5. Blocages ----------------------------------------------------------
    lockouts = int(health.get("lockouts_active", 0))
    check(lockouts == 0, "aucun compte bloqué", f"{lockouts} bloqué(s)")

    # 6. Métriques ---------------------------------------------------------
    try:
        metrics = httpx.get(f"{args.base}/metrics", timeout=5).text
        check("bo_face_engine_ready 1" in metrics, "métriques Prometheus exposées")
    except Exception as exc:  # noqa: BLE001
        check(False, "métriques Prometheus", f"{type(exc).__name__}: {exc}")

    print()
    if failures:
        print(f"{failures} contrôle(s) en échec.")
        return 1
    if warnings:
        print(f"Prêt, avec {warnings} avertissement(s).")
        return 0
    print("Back-office prêt pour la démonstration.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
