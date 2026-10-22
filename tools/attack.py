"""PC Attaquant — les 4 familles d'attaques de la démonstration.

    python tools/attack.py --list
    python tools/attack.py poison          # leurres empoisonnés (attaque principale)
    python tools/attack.py replay          # rejeu de key image (double dépense)
    python tools/attack.py burst           # rafale automatisée
    python tools/attack.py malformed       # anneau malformé (bruit, pas de crash)
    python tools/attack.py all             # les quatre, dans l'ordre

C'est le script à lancer depuis le **PC Attaquant**, en SSH ou en RDP, pendant
la démonstration. Il ne fait aucune supposition sur l'état du système : il
attaque et rapporte ce qu'il observe.

Ce que chaque attaque exploite réellement :

  poison     tire les leurres UNIFORMÉMENT dans le registre public, au lieu de
             la gamma biaisée récents d'un wallet. C'est le seul signal que le
             ML exploite à 100 % (âge médian des leurres).
  replay     renvoie une transaction déjà acceptée : même output, donc même key
             image. Rejet 409, mais la télémétrie est publiée — sinon l'attaque
             serait invisible pour le détecteur.
  burst      cadence machine (50 ms) au lieu d'une cadence humaine.
  malformed  anneau trop court / clés invalides : doit produire une erreur
             propre, jamais un panic du noyau Rust.

Codes de sortie : 0 si l'attaque s'est déroulée comme prévu, 1 sinon.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from urllib.parse import urlsplit

import httpx

import service_token

# Le PC Attaquant passe par le WAF : c'est le point d'entrée « client ».
#
# Deux postes, deux adresses. Sur le PC Hôte, `localhost` suffit ; depuis le PC
# Attaquant, il faut l'adresse de la machine qui héberge la pile :
#
#     SHADOW_HOST=10.251.4.134 python tools/attack.py poison
#     python tools/attack.py poison --target http://10.251.4.134:8085
#
# `SHADOW_HOST` couvre les trois ports d'un coup (WAF, paiement direct, boutique) ;
# `--target` ne déplace que le WAF. Aucun secret n'est nécessaire pour attaquer :
# seul `reset` (désarmer la déception) exige le jeton de service, et il se lance
# depuis le PC Hôte.
SHADOW_HOST = os.environ.get("SHADOW_HOST", "localhost").strip() or "localhost"
TARGET = os.environ.get("SHADOW_WAF", f"http://{SHADOW_HOST}:8085")
DIRECT = os.environ.get("SHADOW_PAYMENT", f"http://{SHADOW_HOST}:8080")
SHOP = os.environ.get("SHADOW_SHOP", f"http://{SHADOW_HOST}:8087")


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _submit(client: httpx.Client, decoy_ids: list[str], base: str = TARGET) -> dict:
    pool = client.get(f"{base}/outputs", params={"unspent": True}).json()["outputs"]
    if not pool:
        return {"status_code": 0, "detail": "aucun output disponible"}
    response = client.post(
        f"{base}/tx/submit",
        json={
            "real_output_id": random.choice(pool)["id"],
            "decoy_ids": decoy_ids,
        },
    )
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:200]}
    return {
        "status_code": response.status_code,
        "route": response.headers.get("X-Shadow-Route"),
        "body": body,
    }


def attack_poison(client: httpx.Client, rounds: int = 12) -> dict:
    """Leurres tirés uniformément dans le registre public."""
    log("ATTAQUE 1/4 — empoisonnement des leurres (tirage uniforme)")
    accepted = rejected = routed = 0
    for _ in range(rounds):
        outputs = client.get(f"{TARGET}/outputs").json()["outputs"]
        if len(outputs) < 10:
            log("registre trop petit — arrêt")
            break
        # Aucun biais vers les récents : c'est exactement ce que le ML détecte.
        poison = [o["id"] for o in random.sample(outputs, k=10)]
        result = _submit(client, poison)
        if result["status_code"] == 200:
            accepted += 1
        elif result["status_code"] >= 400:
            rejected += 1
        if result.get("route") == "honeypot":
            routed += 1
        time.sleep(random.uniform(0.1, 0.3))
    log(f"  {accepted} acceptées · {rejected} rejetées · {routed} déroutées vers le honeypot")
    return {"accepted": accepted, "rejected": rejected, "routed": routed}


def attack_replay(client: httpx.Client) -> dict:
    """Rejoue une transaction acceptée : même output, donc même key image."""
    log("ATTAQUE 2/4 — rejeu de key image (double dépense)")
    decoys = client.get(f"{TARGET}/decoys", params={"count": 10}).json()["decoys"]
    pool = client.get(f"{TARGET}/outputs", params={"unspent": True}).json()["outputs"]
    if not pool:
        return {"error": "aucun output"}
    real = random.choice(pool)["id"]

    first = client.post(
        f"{TARGET}/tx/submit", json={"real_output_id": real, "decoy_ids": decoys}
    )
    log(f"  transaction initiale : HTTP {first.status_code}")
    if first.status_code != 200:
        return {"error": f"la transaction initiale a échoué ({first.status_code})"}

    # Le rejeu passe par le chemin direct : même avec la déception active, on
    # veut mesurer la réponse de payment-api, pas celle du honeypot.
    replay = client.post(
        f"{DIRECT}/tx/submit", json={"real_output_id": real, "decoy_ids": decoys}
    )
    blocked = replay.status_code == 409
    log(
        f"  rejeu : HTTP {replay.status_code} — "
        f"{'BLOQUÉ (anti-double-dépense)' if blocked else 'NON BLOQUÉ (régression !)'}"
    )
    return {"initial": first.status_code, "replay": replay.status_code, "blocked": blocked}


def attack_burst(client: httpx.Client, count: int = 40) -> dict:
    """Rafale automatisée : cadence machine, ~50 ms."""
    log(f"ATTAQUE 3/4 — rafale automatisée ({count} transactions en ~50 ms d'intervalle)")
    accepted = rejected = 0
    started = time.perf_counter()
    for _ in range(count):
        decoys = client.get(f"{TARGET}/decoys", params={"count": 10}).json()["decoys"]
        result = _submit(client, decoys)
        if result["status_code"] == 200:
            accepted += 1
        elif result["status_code"] >= 400:
            rejected += 1
        time.sleep(0.05)
    elapsed = time.perf_counter() - started
    rate = count / elapsed if elapsed else 0
    log(f"  {accepted} acceptées · {rejected} rejetées · {rate:.1f} tx/s")
    return {"accepted": accepted, "rejected": rejected, "rate_per_second": round(rate, 1)}


def attack_malformed(client: httpx.Client) -> dict:
    """Anneau malformé : le noyau doit refuser proprement, jamais paniquer."""
    log("ATTAQUE 4/4 — anneau malformé (robustesse du noyau)")
    cases = [
        ("anneau vide", {"real_output_id": "output_1", "decoy_ids": []}),
        ("leurres inconnus", {"real_output_id": "output_1", "decoy_ids": ["x"] * 10}),
        ("output inconnu", {"real_output_id": "inexistant", "decoy_ids": ["output_2"]}),
    ]
    results = {}
    for label, payload in cases:
        response = client.post(f"{DIRECT}/tx/submit", json=payload)
        results[label] = response.status_code
        log(f"  {label}: HTTP {response.status_code}")
    # Le service doit toujours répondre après ces erreurs.
    alive = client.get(f"{DIRECT}/healthz").status_code == 200
    log(f"  service toujours vivant : {alive}")
    return {"cases": results, "service_alive": alive}


def reset(client: httpx.Client) -> int:
    """Désarme la déception (à faire entre deux répétitions).

    `/admin/reset` est protégé par `SERVICE_TOKEN` : un attaquant ne doit pas
    pouvoir désarmer la déception pendant l'incident. L'outil lit le jeton dans
    l'environnement ou dans `.env` (voir `tools/service_token.py`).
    """
    response = client.post(f"{TARGET}/admin/reset", headers=service_token.headers())
    if response.status_code != 200:
        log(
            f"ECHEC : /admin/reset -> HTTP {response.status_code}. "
            "Renseigner SERVICE_TOKEN dans .env (python tools/init_env.py)."
        )
        return 1
    body = response.json()
    log(f"déception désarmée · état : {body.get('deception')}")
    return 0


def run(kind: str) -> int:
    with httpx.Client(timeout=20.0) as client:
        try:
            health = client.get(f"{TARGET}/healthz").json()
        except httpx.HTTPError as exc:
            log(f"ECHEC : cible injoignable ({TARGET}) : {exc}")
            return 1
        log(f"cible {TARGET} · déception active : {health.get('deception')}")

        if kind == "reset":
            return reset(client)

        summary: dict = {}
        if kind in ("poison", "all"):
            summary["poison"] = attack_poison(client)
        if kind in ("replay", "all"):
            summary["replay"] = attack_replay(client)
        if kind in ("burst", "all"):
            summary["burst"] = attack_burst(client)
        if kind in ("malformed", "all"):
            summary["malformed"] = attack_malformed(client)

        if kind == "shop":
            # Les attaques boutique attaquent un autre service : elles ont leur
            # propre session et leur propre bilan. On leur passe la main.
            from attack_shop import run_shop, set_base

            # La boutique n'est pas derrière le WAF : même hôte, port 8087.
            # `--target` déplace le WAF, `--shop` (ou SHADOW_SHOP) la boutique.
            set_base(SHOP)
            return run_shop()

        print(json.dumps(summary, indent=2, ensure_ascii=False))

        if kind == "replay" and not summary.get("replay", {}).get("blocked"):
            log("ECHEC : le rejeu n'a pas été bloqué")
            return 1
        if kind == "malformed" and not summary.get("malformed", {}).get("service_alive"):
            log("ECHEC : le service ne répond plus après des entrées invalides")
            return 1
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PC Attaquant — 4 familles d'attaques")
    parser.add_argument(
        "kind",
        nargs="?",
        default="poison",
        choices=["poison", "replay", "burst", "malformed", "shop", "all", "list", "reset"],
    )
    parser.add_argument("--target", default=TARGET, help="URL du WAF")
    parser.add_argument(
        "--shop",
        default=os.environ.get("SHADOW_SHOP", f"http://{SHADOW_HOST}:8087"),
        help="URL de la boutique (attaques 'shop')",
    )
    args = parser.parse_args()

    if args.kind == "list":
        print("poison     leurres empoisonnés (attaque principale, détectée à 100 %)")
        print("replay     rejeu de key image (doit être bloqué en 409)")
        print("burst      rafale automatisée (cadence machine)")
        print("malformed  entrées invalides (le noyau doit répondre 400, pas paniquer)")
        print("all        les quatre, dans l'ordre de la démonstration")
        print("shop       les 6 attaques sur la boutique (panier, prix, stock, historique)")
        print("reset      désarme la déception (entre deux répétitions)")
        raise SystemExit(0)

    TARGET = args.target.rstrip("/")
    SHOP = args.shop.rstrip("/")
    # `--target` ne déplace que le WAF ; le paiement direct et la boutique
    # suivent le même hôte, sauf indication contraire explicite.
    host = urlsplit(TARGET).hostname or "localhost"
    if "--target" in sys.argv and "SHADOW_PAYMENT" not in os.environ:
        DIRECT = f"http://{host}:8080"
    if "--target" in sys.argv and "--shop" not in sys.argv and "SHADOW_SHOP" not in os.environ:
        SHOP = f"http://{host}:8087"
    raise SystemExit(run(args.kind))
