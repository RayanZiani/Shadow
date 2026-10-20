#!/usr/bin/env python3
"""Chaîne complète de la boutique — répétition générale avant soutenance.

    python tools/e2e_shop.py

Parcours réel, sur les services DÉMARRÉS (aucun montage de test) :

    catalogue -> panier -> paiement bLSAG -> commande payée -> historique
    -> stock décrémenté -> commande visible côté administration

Code de sortie 0 = la boutique fonctionne de bout en bout. 1 = au moins un
contrôle a échoué, avec la raison exacte.

Ce que ce script ne fait PAS : simuler un paiement. Il appelle `/checkout`, qui
appelle `payment-api /tx/pay`, qui émet un output du montant exact et le
dépense par une signature d'anneau. Si le noyau est cassé, le script échoue.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

import service_token

SHOP = "http://localhost:8087"
PAYMENT = "http://localhost:8080"
BACKOFFICE = "http://localhost:8086"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=SHOP)
    args = parser.parse_args()
    base = args.base

    failures: list[str] = []

    def check(condition: bool, label: str, detail: str = "") -> None:
        status = "\033[32mOK\033[0m" if condition else "\033[31mECHEC\033[0m"
        print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
        if not condition:
            failures.append(label)

    with httpx.Client(timeout=30.0) as client:
        # 1. Services joignables -------------------------------------------
        try:
            health = client.get(f"{base}/healthz").json()
        except Exception as exc:  # noqa: BLE001
            log(f"boutique injoignable ({type(exc).__name__}) — docker compose up -d shop-api")
            return 1
        check(health.get("status") == "ok", "boutique saine", health.get("database", "?"))
        check(health.get("database") == "postgresql", "base PostgreSQL", health.get("database", "?"))

        try:
            crypto = client.get(f"{PAYMENT}/healthz").json()
            check(crypto.get("crypto_core") == "ready", "noyau cryptographique prêt")
        except Exception as exc:  # noqa: BLE001
            check(False, "noyau cryptographique joignable", str(exc))

        # 2. Catalogue -----------------------------------------------------
        catalog = client.get(f"{base}/products").json()["products"]
        check(len(catalog) >= 3, "catalogue non vide", f"{len(catalog)} articles")
        buyable = [p for p in catalog if p["stock"] >= 2]
        if not buyable:
            check(False, "un article en stock", "tous en rupture")
            return 1
        product = buyable[0]
        stock_before = product["stock"]
        log(f"article retenu : {product['sku']} ({product['price']}) stock {stock_before}")

        # 3. Panier --------------------------------------------------------
        cart = client.post(f"{base}/cart").json()["cart"]
        added = client.post(
            f"{base}/cart/{cart['token']}/items",
            json={"product_id": product["id"], "quantity": 2},
        )
        check(added.status_code == 200, "ajout au panier", str(added.status_code))
        cart = added.json()["cart"]
        expected_total = product["price_cents"] * 2
        check(
            cart["total_cents"] == expected_total,
            "total calculé par le serveur",
            f"{cart['total_cents']} centimes",
        )

        # 4. Paiement réel -------------------------------------------------
        log("paiement (signature d'anneau bLSAG)…")
        started = time.time()
        checkout = client.post(f"{base}/checkout", json={"cart_token": cart["token"]})
        elapsed = time.time() - started
        check(checkout.status_code == 201, "commande créée et payée", str(checkout.status_code))
        if checkout.status_code != 201:
            print(json.dumps(checkout.json(), indent=2, ensure_ascii=False))
            return 1
        body = checkout.json()
        order = body["order"]
        payment = body["payment"]
        check(order["status"] == "paid", "commande payée", order["status"])
        check(bool(payment.get("key_image")), "key image stockée", str(payment.get("key_image"))[:16] + "…")
        check(payment.get("ring_size", 0) >= 2, "anneau vérifié", f"N={payment.get('ring_size')}")
        check(order["total_cents"] == expected_total, "montant exact", f"{order['total_cents']} centimes")
        log(f"paiement en {elapsed * 1000:.0f} ms")

        # 5. Stock décrémenté ---------------------------------------------
        after = next(
            p for p in client.get(f"{base}/products").json()["products"] if p["id"] == product["id"]
        )
        check(after["stock"] == stock_before - 2, "stock décrémenté", f"{after['stock']} restants")

        # 6. Historique ----------------------------------------------------
        reference = order["reference"]
        customer_ref = body["customer_ref"]
        history = client.get(f"{base}/orders", params={"customer_ref": customer_ref}).json()
        check(
            any(item["reference"] == reference for item in history["orders"]),
            "commande dans l'historique",
            f"{len(history['orders'])} commande(s)",
        )
        anonymous = client.get(f"{base}/orders/{reference}")
        check(anonymous.status_code == 404, "historique fermé sans capability token", str(anonymous.status_code))

        # 7. Vue administrateur --------------------------------------------
        auth = service_token.headers()
        admin = client.get(f"{base}/admin/orders", headers=auth)
        if admin.status_code == 200:
            check(
                any(item["reference"] == reference for item in admin.json()["orders"]),
                "commande visible côté administration",
            )
        else:
            check(False, "administration joignable", str(admin.status_code))

        # 8. Paiement refusé : le stock est restitué -----------------------
        # On ne force pas un échec réel (cela demanderait d'arrêter le noyau).
        # Le comportement est couvert par les tests unitaires ; ici on vérifie
        # seulement qu'un panier vide ne crée rien.
        empty_cart = client.post(f"{base}/cart").json()["cart"]
        empty = client.post(f"{base}/checkout", json={"cart_token": empty_cart["token"]})
        check(empty.status_code == 400, "panier vide refusé", str(empty.status_code))

    print()
    if failures:
        print(f"{len(failures)} contrôle(s) en échec : " + ", ".join(failures))
        return 1
    print(json.dumps({"chain_complete": True, "order": reference}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
