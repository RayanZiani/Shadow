#!/usr/bin/env python3
"""Chaos engineering — injecter des pannes et vérifier que le SI tient.

    python tools/chaos.py                 # tous les scénarios
    python tools/chaos.py --only ml       # un seul
    python tools/chaos.py --list

Le sujet demande « chaos engineering : injection de défaillances et recette ».
Ce script automatise ce qui se faisait à la main, et le transforme en
affirmation vérifiable : **chaque scénario a un attendu et un code de sortie**.

Principe de rédaction : un scénario n'est valide que s'il vérifie un
**invariant métier**, pas seulement un code HTTP.

| Scénario | Invariant |
|---|---|
| `ml` | arrêter le détecteur ne casse pas le paiement (le ML observe) |
| `remediation` | arrêter le moteur ne casse pas le paiement, et le dashboard le dit |
| `crypto` | arrêter le noyau produit un **503 explicite**, jamais un 500 |
| `shop` | arrêter la boutique laisse le paiement intact (silos indépendants) |
| `token` | retirer le jeton d'administration ferme les routes (503), sans 500 |
| `rag` | arrêter l'assistant RAG ne touche ni la boutique ni le paiement |

Comment arrêter un service : `docker compose stop <service>`. Le script
**redémarre systématiquement** ce qu'il a arrêté, même en cas d'exception —
un outil de chaos qui laisse la pile cassée est un outil dangereux.

Sur un poste sans Docker, les scénarios sont déclarés `SKIP` avec la raison :
on ne prétend pas avoir testé ce qu'on n'a pas testé.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

import httpx

PAYMENT = "http://localhost:8080"
SHOP = "http://localhost:8087"
ML = "http://localhost:8083"
REMEDIATION = "http://localhost:8084"
RAG = "http://localhost:8091"


@dataclass
class Result:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str
    seconds: float = 0.0


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                timeout=10,
            ).returncode
            == 0
        )
    except (subprocess.SubprocessError, OSError):
        return False


def compose(*args: str) -> tuple[int, str]:
    process = subprocess.run(
        ["docker", "compose", *args], capture_output=True, text=True, timeout=180
    )
    return process.returncode, (process.stdout + process.stderr).strip()


def wait_http(url: str, ok: bool = True, timeout: float = 60.0) -> bool:
    """Attend que l'URL réponde (ok=True) ou cesse de répondre (ok=False)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=3.0)
            reachable = response.status_code < 500
        except httpx.HTTPError:
            reachable = False
        if reachable == ok:
            return True
        time.sleep(1.0)
    return False


def payment_works() -> tuple[bool, str]:
    """Un achat complet passe-t-il encore ? C'est l'invariant central."""
    try:
        with httpx.Client(timeout=30.0) as client:
            catalog = client.get(f"{SHOP}/products").json()["products"]
            product = next((p for p in catalog if p["stock"] > 0), None)
            if product is None:
                return False, "aucun article en stock (relancer la pile)"
            cart = client.post(f"{SHOP}/cart").json()["cart"]
            client.post(
                f"{SHOP}/cart/{cart['token']}/items",
                json={"product_id": product["id"], "quantity": 1},
            )
            response = client.post("/checkout" if False else f"{SHOP}/checkout",
                                   json={"cart_token": cart["token"]})
            if response.status_code == 201:
                return True, f"commande {response.json()['order']['reference']} payée"
            return False, f"checkout HTTP {response.status_code}"
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Scénarios
# ---------------------------------------------------------------------------
def scenario_ml() -> Result:
    """Arrêter le détecteur : le paiement doit continuer (le ML observe)."""
    started = time.time()
    code, output = compose("stop", "ml-detector")
    if code != 0:
        return Result("ml", "SKIP", f"docker compose stop a échoué : {output[:120]}")
    try:
        wait_http(f"{ML}/healthz", ok=False, timeout=60)
        ok, detail = payment_works()
        status = "PASS" if ok else "FAIL"
        return Result("ml", status, f"paiement sans ML : {detail}", time.time() - started)
    finally:
        compose("start", "ml-detector")
        wait_http(f"{ML}/healthz", ok=True, timeout=120)


def scenario_remediation() -> Result:
    """Arrêter le moteur : le paiement continue, l'état n'est plus publié."""
    started = time.time()
    code, output = compose("stop", "remediation-engine")
    if code != 0:
        return Result("remediation", "SKIP", f"stop a échoué : {output[:120]}")
    try:
        wait_http(f"{REMEDIATION}/healthz", ok=False, timeout=60)
        ok, detail = payment_works()
        state_down = False
        try:
            httpx.get(f"{REMEDIATION}/state", timeout=3.0)
        except httpx.HTTPError:
            state_down = True
        if ok and state_down:
            return Result(
                "remediation",
                "PASS",
                f"paiement intact, /state injoignable comme attendu ({detail})",
                time.time() - started,
            )
        return Result(
            "remediation",
            "FAIL",
            f"paiement={ok} ({detail}), /state injoignable={state_down}",
            time.time() - started,
        )
    finally:
        compose("start", "remediation-engine")
        wait_http(f"{REMEDIATION}/healthz", ok=True, timeout=120)


def scenario_crypto() -> Result:
    """Arrêter le noyau : 503 explicite, jamais un 500 nu."""
    started = time.time()
    code, output = compose("stop", "monero-crypto-core")
    if code != 0:
        return Result("crypto", "SKIP", f"stop a échoué : {output[:120]}")
    try:
        wait_http("http://localhost:8081/healthz", ok=False, timeout=60)
        with httpx.Client(timeout=30.0) as client:
            # Un paiement direct doit échouer proprement.
            response = client.post(
                f"{PAYMENT}/tx/pay", json={"amount": 100, "reference": "CHAOS"}
            )
            health = client.get(f"{PAYMENT}/healthz").json()
        explicit = response.status_code in (503, 502)
        declared = health.get("crypto_core") == "degraded"
        if explicit and declared:
            return Result(
                "crypto",
                "PASS",
                f"paiement -> HTTP {response.status_code}, /healthz dit "
                f"crypto_core={health.get('crypto_core')}",
                time.time() - started,
            )
        return Result(
            "crypto",
            "FAIL",
            f"paiement -> HTTP {response.status_code} (attendu 503/502), "
            f"crypto_core={health.get('crypto_core')}",
            time.time() - started,
        )
    finally:
        compose("start", "monero-crypto-core")
        wait_http("http://localhost:8081/healthz", ok=True, timeout=120)


def scenario_shop() -> Result:
    """Arrêter la boutique : le paiement direct reste intact (silos)."""
    started = time.time()
    code, output = compose("stop", "shop-api")
    if code != 0:
        return Result("shop", "SKIP", f"stop a échoué : {output[:120]}")
    try:
        wait_http(f"{SHOP}/healthz", ok=False, timeout=60)
        try:
            health = httpx.get(f"{PAYMENT}/healthz", timeout=5.0).json()
            ok = health.get("status") == "ok"
        except httpx.HTTPError:
            ok = False
        if ok:
            return Result(
                "shop",
                "PASS",
                "payment-api toujours sain sans la boutique",
                time.time() - started,
            )
        return Result("shop", "FAIL", "payment-api dégradé sans la boutique", time.time() - started)
    finally:
        compose("start", "shop-api")
        wait_http(f"{SHOP}/healthz", ok=True, timeout=120)


def scenario_token() -> Result:
    """Sans jeton, l'administration est FERMÉE (503), pas ouverte, pas 500."""
    started = time.time()
    try:
        with httpx.Client(timeout=10.0) as client:
            codes = {}
            for path in ("/admin/quarantine-outputs", "/admin/ring-size"):
                response = client.post(f"{PAYMENT}{path}", json={"ring_size": 25})
                codes[path] = response.status_code
            anonymous = client.post(f"{REMEDIATION}/incident", json={})
            codes["/incident"] = anonymous.status_code
    except httpx.HTTPError as exc:
        return Result("token", "FAIL", f"{type(exc).__name__}: {exc}", time.time() - started)

    # 401 (mauvais jeton) ou 503 (aucun jeton configuré) : les deux sont
    # acceptables. Ce qui est interdit, c'est 200 ou 500.
    ok = all(code in (401, 503) for code in codes.values())
    return Result(
        "token",
        "PASS" if ok else "FAIL",
        f"codes sans jeton : {codes}",
        time.time() - started,
    )


def scenario_rag() -> Result:
    """Arrêter l'assistant : ni la boutique ni le paiement ne s'en aperçoivent."""
    started = time.time()
    code, output = compose("stop", "rag-assistant")
    if code != 0:
        return Result("rag", "SKIP", f"stop a échoué : {output[:120]}")
    try:
        wait_http(f"{RAG}/healthz", ok=False, timeout=60)
        ok, detail = payment_works()
        if ok:
            return Result("rag", "PASS", f"paiement intact sans RAG ({detail})", time.time() - started)
        return Result("rag", "FAIL", detail, time.time() - started)
    finally:
        compose("start", "rag-assistant")
        wait_http(f"{RAG}/healthz", ok=True, timeout=120)


SCENARIOS = {
    "ml": scenario_ml,
    "remediation": scenario_remediation,
    "crypto": scenario_crypto,
    "shop": scenario_shop,
    "token": scenario_token,
    "rag": scenario_rag,
}

# Scénarios qui n'arrêtent aucun conteneur : ils ne demandent pas Docker.
WITHOUT_DOCKER = {"token"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+", choices=list(SCENARIOS))
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        for name, function in SCENARIOS.items():
            print(f"{name:14s} {(function.__doc__ or '').strip().splitlines()[0]}")
        return 0

    names = args.only or list(SCENARIOS)
    if not docker_available():
        runnable = [name for name in names if name in WITHOUT_DOCKER]
        skipped = [name for name in names if name not in WITHOUT_DOCKER]
        print("Docker indisponible : seuls les scénarios sans conteneur sont exécutés.")
        if skipped:
            print(f"Déclarés NON TESTÉS : {', '.join(skipped)}")
        names = runnable
        if not names:
            print("Aucun scénario exécutable sur ce poste.")
            return 2
    results: list[Result] = []
    for name in names:
        print(f"\n=== {name} ===")
        try:
            result = SCENARIOS[name]()
        except Exception as exc:  # noqa: BLE001 - un scénario ne doit pas tuer le rapport
            result = Result(name, "FAIL", f"{type(exc).__name__}: {exc}")
        print(f"  [{result.status}] {result.detail} ({result.seconds:.1f} s)")
        results.append(result)

    print("\n=== Récapitulatif ===")
    for result in results:
        print(f"  {result.status:4s}  {result.name:14s} {result.detail}")
    print(json.dumps({r.name: r.status for r in results}, indent=2))

    failed = [r for r in results if r.status == "FAIL"]
    if failed:
        print(f"\n{len(failed)} scénario(s) en échec.")
        return 1
    print("\nTous les scénarios exécutés sont conformes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
