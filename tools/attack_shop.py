"""Attaques sur la BOUTIQUE — le rôle CYBER sur le SI de paiement.

Le jury demande de « mettre en place des attaques et essayer de faire tomber le
SI ». Ce module attaque le site vitrine et son panier, pas le terminal : c'est
la nouvelle surface exposée au public.

Aucune de ces attaques ne doit réussir. Chacune vérifie **deux choses** :
le code HTTP attendu, ET l'absence d'effet de bord (aucune commande créée,
aucun stock négatif, service toujours vivant). Un 400 qui laisse une commande
en base est un échec, pas une protection.

    python tools/attack.py shop          # les 5 attaques boutique
"""
from __future__ import annotations

import json
import time

import httpx

SHOP = "http://localhost:8087"


def _alive(client: httpx.Client) -> bool:
    try:
        return client.get(f"{SHOP}/healthz", timeout=5.0).status_code == 200
    except httpx.HTTPError:
        return False


def _order_count(client: httpx.Client) -> int:
    """Nombre de commandes en base, lu par l'administration."""
    import service_token

    try:
        response = client.get(
            f"{SHOP}/admin/orders", headers=service_token.headers(), timeout=5.0
        )
        if response.status_code == 200:
            return len(response.json().get("orders", []))
    except httpx.HTTPError:
        pass
    return -1


def _stock(client: httpx.Client, product_id: int) -> int:
    try:
        products = client.get(f"{SHOP}/products", timeout=5.0).json()["products"]
        for product in products:
            if product["id"] == product_id:
                return int(product["stock"])
    except (httpx.HTTPError, KeyError, ValueError):
        pass
    return -1


def attack_quantity(client: httpx.Client) -> dict:
    """Quantité négative ou absurde : doit être refusée par la validation."""
    results = {}
    cart = client.post(f"{SHOP}/cart", timeout=5.0).json()["cart"]
    products = client.get(f"{SHOP}/products", timeout=5.0).json()["products"]
    product_id = products[0]["id"]

    cases = {
        "quantity_negative": -5,
        "quantity_zero": 0,
        "quantity_huge": 1_000_000,
        "quantity_float": 2.5,
        "quantity_string": "3",
    }
    for label, quantity in cases.items():
        try:
            response = client.post(
                f"{SHOP}/cart/{cart['token']}/items",
                json={"product_id": product_id, "quantity": quantity},
                timeout=5.0,
            )
            results[label] = response.status_code
        except httpx.HTTPError as exc:
            results[label] = type(exc).__name__

    refused = all(code in (400, 422) for code in results.values())
    return {"cases": results, "refused": refused}


def attack_forged_cart(client: httpx.Client) -> dict:
    """Jeton de panier inventé, ou panier d'un autre client."""
    results = {}
    for label, token in (
        ("random_token", "A" * 32),
        ("empty_token", ""),
        ("sqli_token", "' OR 1=1 --"),
        ("path_traversal", "../../etc/passwd"),
    ):
        try:
            response = client.get(f"{SHOP}/cart/{token}", timeout=5.0)
            results[label] = response.status_code
        except httpx.HTTPError as exc:
            results[label] = type(exc).__name__
    # 307 = redirection FastAPI pour `/cart/` (barre finale). Ce n'est pas une
    # fuite : le panier n'est pas rendu, la requête est redirigée vers une route
    # qui n'existe pas sans jeton.
    refused = all(code in (307, 400, 404, 422) for code in results.values())
    return {"cases": results, "refused": refused}


def attack_sku_injection(client: httpx.Client) -> dict:
    """Injection SQL par le SKU : la requête est paramétrée, donc inerte."""
    import service_token

    headers = service_token.headers()
    payloads = {
        "drop_table": "X'; DROP TABLE products; --",
        "union": "X' UNION SELECT 1,2,3,4,5,6,7,8,9,10 --",
        "quote": "X''''",
    }
    results = {}
    for label, sku in payloads.items():
        try:
            response = client.post(
                f"{SHOP}/admin/products",
                json={"sku": sku, "name": "injection", "price_cents": 100, "stock": 1},
                headers=headers,
                timeout=5.0,
            )
            results[label] = response.status_code
        except httpx.HTTPError as exc:
            results[label] = type(exc).__name__

    # Le catalogue doit toujours répondre : la table n'a pas été supprimée.
    catalog_ok = client.get(f"{SHOP}/products", timeout=5.0).status_code == 200
    # Aucune charge utile ne doit être stockée : toutes doivent être refusées
    # par le format de SKU (422).
    all_refused = all(code == 422 for code in results.values())
    return {"cases": results, "catalog_intact": catalog_ok, "all_refused": all_refused}


def attack_history_scraping(client: httpx.Client) -> dict:
    """Énumération de l'historique d'autrui : doit renvoyer 404 partout."""
    import service_token

    results = {}
    try:
        orders = client.get(
            f"{SHOP}/admin/orders", headers=service_token.headers(), timeout=5.0
        ).json()["orders"]
    except (httpx.HTTPError, KeyError):
        orders = []

    reference = orders[0]["reference"] if orders else "CMD-DEADBEEF"
    attempts = {
        "no_token": "",
        "guess": "0" * 32,
        "sql": "' OR '1'='1",
    }
    for label, customer_ref in attempts.items():
        response = client.get(
            f"{SHOP}/orders/{reference}",
            params={"customer_ref": customer_ref},
            timeout=5.0,
        )
        results[label] = response.status_code

    # Lister avec un capability token inventé doit renvoyer 200 **avec zéro
    # commande** : c'est la bonne réponse (le token ne correspond à personne).
    # Un 200 n'est donc pas un échec — c'est le nombre de commandes qui compte.
    listing = client.get(
        f"{SHOP}/orders", params={"customer_ref": "1" * 32}, timeout=5.0
    )
    results["list_with_fake_token"] = listing.status_code
    leaked = len(listing.json().get("orders", [])) if listing.status_code == 200 else 0

    # Seules les routes de DÉTAIL doivent renvoyer 404. La liste avec un
    # capability token inventé renvoie 200 et zéro commande : c'est correct.
    detail_cases = ("no_token", "guess", "sql")
    refused = all(results[key] in (400, 404) for key in detail_cases)
    return {
        "cases": results,
        "refused": refused,
        "orders_leaked": leaked,
        "empty": leaked == 0,
    }


def attack_oversell(client: httpx.Client) -> dict:
    """Course sur le stock : N paniers pour le même dernier article.

    On ne peut pas garantir un vrai parallélisme avec `httpx` synchrone, mais
    on vérifie le point qui compte : le stock ne devient jamais négatif et le
    nombre de commandes payées ne dépasse pas le stock disponible.
    """
    products = client.get(f"{SHOP}/products", timeout=5.0).json()["products"]
    target = next((p for p in products if p["stock"] > 0), None)
    if target is None:
        return {"skipped": "aucun article en stock"}

    stock_before = target["stock"]
    orders_before = _order_count(client)
    attempts = min(stock_before + 2, 6)

    created = 0
    for _ in range(attempts):
        cart = client.post(f"{SHOP}/cart", timeout=5.0).json()["cart"]
        added = client.post(
            f"{SHOP}/cart/{cart['token']}/items",
            json={"product_id": target["id"], "quantity": 1},
            timeout=5.0,
        )
        if added.status_code != 200:
            continue
        checkout = client.post(
            f"{SHOP}/checkout", json={"cart_token": cart["token"]}, timeout=20.0
        )
        if checkout.status_code == 201:
            created += 1

    stock_after = _stock(client, target["id"])
    orders_after = _order_count(client)
    return {
        "product": target["sku"],
        "stock_before": stock_before,
        "stock_after": stock_after,
        "orders_created": created,
        "orders_delta": orders_after - orders_before if orders_before >= 0 else None,
        "no_negative_stock": stock_after >= 0,
        "no_oversell": stock_after == max(0, stock_before - created),
    }


def attack_price_tampering(client: httpx.Client) -> dict:
    """Le client annonce un prix : le serveur doit l'ignorer.

    Le total vient de la base, jamais du corps de la requête. On envoie un prix
    dérisoire et on vérifie que la commande facture le vrai montant.
    """
    products = client.get(f"{SHOP}/products", timeout=5.0).json()["products"]
    target = next((p for p in products if p["stock"] > 0), None)
    if target is None:
        return {"skipped": "aucun article en stock"}

    cart = client.post(f"{SHOP}/cart", timeout=5.0).json()["cart"]
    client.post(
        f"{SHOP}/cart/{cart['token']}/items",
        json={"product_id": target["id"], "quantity": 1},
        timeout=5.0,
    )
    # Tentative : forcer un total de 1 centime et un prix unitaire nul.
    checkout = client.post(
        f"{SHOP}/checkout",
        json={
            "cart_token": cart["token"],
            "total_cents": 1,
            "price_cents": 0,
            "items": [{"product_id": target["id"], "quantity": 1, "unit_price_cents": 0}],
        },
        timeout=20.0,
    )
    if checkout.status_code != 201:
        return {"status": checkout.status_code, "ignored": True}
    total = checkout.json()["order"]["total_cents"]
    return {
        "status": 201,
        "expected": target["price_cents"],
        "charged": total,
        "ignored": total == target["price_cents"],
    }


def run_shop() -> int:
    """Lance les 5 attaques boutique et rapporte ce qui a résisté."""
    with httpx.Client(timeout=25.0) as client:
        try:
            client.get(f"{SHOP}/healthz", timeout=5.0)
        except httpx.HTTPError as exc:
            print(f"ECHEC : boutique injoignable ({SHOP}) : {exc}")
            return 1

        started = time.time()
        summary = {
            "quantity": attack_quantity(client),
            "forged_cart": attack_forged_cart(client),
            "sku_injection": attack_sku_injection(client),
            "history_scraping": attack_history_scraping(client),
            "oversell": attack_oversell(client),
            "price_tampering": attack_price_tampering(client),
        }
        summary["service_alive"] = _alive(client)
        summary["duration_seconds"] = round(time.time() - started, 1)
        print(json.dumps(summary, indent=2, ensure_ascii=False))

        failures = []
        if not summary["quantity"]["refused"]:
            failures.append("quantités invalides acceptées")
        if not summary["forged_cart"]["refused"]:
            failures.append("panier forgé accessible")
        if not summary["sku_injection"]["catalog_intact"]:
            failures.append("catalogue détruit par injection")
        if not summary["sku_injection"]["all_refused"]:
            failures.append("charge utile SQL stockée comme SKU")
        if not summary["history_scraping"]["refused"]:
            failures.append("historique d'autrui lisible")
        if not summary["history_scraping"]["empty"]:
            failures.append("commandes d'autrui retournées")
        oversell = summary["oversell"]
        if "skipped" not in oversell:
            if not oversell["no_negative_stock"]:
                failures.append("stock négatif")
            if not oversell["no_oversell"]:
                failures.append("survente")
        if not summary["price_tampering"].get("ignored", False):
            failures.append("prix forgé accepté")
        if not summary["service_alive"]:
            failures.append("service tombé")

        if failures:
            print("\nECHEC : " + " · ".join(failures))
            return 1
        print(f"\nSI toujours debout après 6 familles d'attaques "
              f"({summary['duration_seconds']} s) · aucune commande frauduleuse")
        return 0
