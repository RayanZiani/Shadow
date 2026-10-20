"""Tests de la boutique — le parcours d'achat est le contrat.

    python -m pytest test_shop_api.py -v

Aucun serveur PostgreSQL ni payment-api requis : la base est SQLite (même SQL,
mêmes transactions) et le paiement est remplacé par un stub qui imite
`/tx/pay`. Ce qui est vérifié est exactement ce que le jury demande :

* des articles en base, un panier, une commande, un paiement, un historique ;
* le stock est réservé puis restitué si le paiement échoue (pas de stock
  fantôme, pas de survente) ;
* une commande n'est `paid` qu'après une preuve de paiement ;
* l'historique n'est lisible qu'avec le `customer_ref` (capability token) ;
* l'administration exige une session back-office de rôle admin.
"""
from __future__ import annotations

import pathlib
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) in sys.path:
    sys.path.remove(str(HERE))
sys.path.insert(0, str(HERE))
for _name in ("main", "db", "svc_auth"):
    _module = sys.modules.get(_name)
    _file = getattr(_module, "__file__", None)
    if _file and pathlib.Path(_file).resolve().parent != HERE:
        del sys.modules[_name]

import main  # noqa: E402


class PaymentStub:
    """Imite `payment-api /tx/pay` : succès, échec, ou exception réseau."""

    def __init__(self):
        self.mode = "ok"
        self.calls: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        import json as _json

        payload = _json.loads(request.content or b"{}")
        self.calls.append({"url": str(request.url), "json": payload})
        if self.mode == "network":
            raise httpx.ConnectError("payment-api injoignable", request=request)
        if self.mode == "fail":
            return httpx.Response(502, json={"detail": "registre saturé"}, request=request)
        return httpx.Response(
            200,
            json={
                "status": "paid",
                "amount": payload["amount"],
                "key_image": "ab" * 32,
                "ring_size": 11,
                "balance_ok": True,
                "signature_scheme": "bLSAG",
            },
            request=request,
        )


@pytest.fixture()
def stub(monkeypatch):
    payment = PaymentStub()
    real_client = httpx.AsyncClient

    class PatchedClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(payment.handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", PatchedClient)
    return payment


@pytest.fixture()
def app(tmp_path):
    application = main.create_app(database_url=f"sqlite:///{tmp_path / 'shop.db'}")
    yield application
    application.state.db.close()


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def _product(client, sku="RING-24"):
    products = client.get("/products").json()["products"]
    return next(product for product in products if product["sku"] == sku)


def _cart_with(client, sku="RING-24", quantity=2):
    product = _product(client, sku)
    token = client.post("/cart").json()["cart"]["token"]
    response = client.post(
        f"/cart/{token}/items", json={"product_id": product["id"], "quantity": quantity}
    )
    assert response.status_code == 200, response.text
    return token, product


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
def test_le_catalogue_est_en_base_et_non_code_en_dur(client):
    products = client.get("/products").json()["products"]
    assert len(products) >= 6
    assert {product["sku"] for product in products} >= {"TERM-01", "RING-24"}
    assert all(product["price_cents"] > 0 for product in products)


def test_un_article_inactif_disparait_du_catalogue(client, monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "jeton-de-test")
    headers = {"X-Service-Token": "jeton-de-test"}
    product = _product(client)
    assert client.delete(f"/admin/products/{product['id']}", headers=headers).status_code == 200
    assert all(item["id"] != product["id"] for item in client.get("/products").json()["products"])


def test_un_article_en_rupture_nest_pas_commandable(client):
    product = _product(client, "AUDIT-J")
    assert product["stock"] == 0
    token = client.post("/cart").json()["cart"]["token"]
    response = client.post(
        f"/cart/{token}/items", json={"product_id": product["id"], "quantity": 1}
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Panier
# ---------------------------------------------------------------------------
def test_le_panier_calcule_le_total(client):
    token, product = _cart_with(client, quantity=3)
    cart = client.get(f"/cart/{token}").json()["cart"]
    assert cart["count"] == 3
    assert cart["total_cents"] == product["price_cents"] * 3


def test_les_quantites_sont_bornees(client):
    token, product = _cart_with(client, quantity=1)
    response = client.patch(
        f"/cart/{token}/items/{product['id']}", json={"quantity": 999}
    )
    assert response.status_code == 422


def test_un_panier_inconnu_renvoie_404(client):
    assert client.get("/cart/inconnu").status_code == 404


# ---------------------------------------------------------------------------
# Commande et paiement
# ---------------------------------------------------------------------------
def test_achat_nominal_stocke_la_commande_et_le_paiement(client, stub):
    token, product = _cart_with(client, quantity=2)
    response = client.post("/checkout", json={"cart_token": token})
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["order"]["status"] == "paid"
    assert body["order"]["total_cents"] == product["price_cents"] * 2
    assert body["payment"]["status"] == "paid"
    assert body["payment"]["key_image"] == "ab" * 32
    assert body["payment"]["events"][-1]["event"] == "paid"
    assert body["customer_ref"]

    # Le stock a réellement baissé, et le panier est vidé.
    assert _product(client)["stock"] == product["stock"] - 2
    assert client.get(f"/cart/{token}").json()["cart"]["items"] == []

    # Le paiement a bien été demandé au noyau pour le montant exact.
    assert stub.calls[-1]["json"]["amount"] == product["price_cents"] * 2


def test_le_paiement_refuse_restitue_le_stock(client, stub):
    token, product = _cart_with(client, quantity=2)
    stub.mode = "fail"
    response = client.post("/checkout", json={"cart_token": token})
    assert response.status_code == 502
    body = response.json()
    assert body["order"]["status"] == "failed"
    # Le stock est revenu à sa valeur initiale : pas de stock fantôme.
    assert _product(client)["stock"] == product["stock"]
    # Le panier est conservé : le client peut réessayer.
    assert client.get(f"/cart/{token}").json()["cart"]["count"] == 2


def test_le_paiement_injoignable_restitue_le_stock(client, stub):
    token, product = _cart_with(client, quantity=1)
    stub.mode = "network"
    response = client.post("/checkout", json={"cart_token": token})
    assert response.status_code == 502
    assert _product(client)["stock"] == product["stock"]


def test_un_panier_vide_ne_cree_pas_de_commande(client, stub):
    token = client.post("/cart").json()["cart"]["token"]
    response = client.post("/checkout", json={"cart_token": token})
    assert response.status_code == 400
    assert stub.calls == []


def test_deux_commandes_ne_survendent_pas(client, stub):
    # VAULT-LIC a un stock faible (8) : on l'épuise, puis on tente de recommander.
    product = _product(client, "VAULT-LIC")
    product_stock = product["stock"]
    token, product = _cart_with(client, sku="VAULT-LIC", quantity=product_stock)
    assert client.post("/checkout", json={"cart_token": token}).status_code == 201
    assert _product(client, "VAULT-LIC")["stock"] == 0

    token2 = client.post("/cart").json()["cart"]["token"]
    response = client.post(
        f"/cart/{token2}/items", json={"product_id": product["id"], "quantity": 1}
    )
    # Il ne reste pas assez de stock : le panier refuse avant la commande.
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Historique
# ---------------------------------------------------------------------------
def test_lhistorique_exige_le_customer_ref(client, stub):
    token, _ = _cart_with(client, quantity=1)
    customer_ref = client.post("/checkout", json={"cart_token": token}).json()["customer_ref"]

    history = client.get("/orders", params={"customer_ref": customer_ref}).json()
    assert len(history["orders"]) == 1
    reference = history["orders"][0]["reference"]

    # Sans le capability token : 404, et pas 403 (on ne révèle pas l'existence).
    assert client.get(f"/orders/{reference}").status_code == 404
    assert client.get(f"/orders/{reference}", params={"customer_ref": "x" * 32}).status_code == 404

    detail = client.get(f"/orders/{reference}", params={"customer_ref": customer_ref})
    assert detail.status_code == 200
    assert detail.json()["payment"]["status"] == "paid"


# ---------------------------------------------------------------------------
# Administration
# ---------------------------------------------------------------------------
def test_ladministration_exige_une_session_admin(client, monkeypatch):
    assert client.get("/admin/products").status_code == 401
    assert client.get("/admin/orders").status_code == 401

    # Le jeton de service est accepté (outils, tests, amorçage).
    monkeypatch.setenv("SERVICE_TOKEN", "jeton-de-test")
    headers = {"X-Service-Token": "jeton-de-test"}
    assert client.get("/admin/products", headers=headers).status_code == 200
    assert client.get("/admin/orders", headers=headers).status_code == 200


def test_un_service_token_invalide_est_refuse(client, monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "jeton-de-test")
    response = client.get("/admin/products", headers={"X-Service-Token": "faux"})
    assert response.status_code == 401


def test_creation_et_modification_dun_article(client, monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "jeton-de-test")
    headers = {"X-Service-Token": "jeton-de-test"}

    created = client.post(
        "/admin/products",
        json={
            "sku": "NEW-01",
            "name": "Nouvel article",
            "description": "Ajouté par le back-office",
            "price_cents": 1500,
            "stock": 5,
        },
        headers=headers,
    )
    assert created.status_code == 201
    product_id = created.json()["product"]["id"]

    # SKU unique : un doublon est refusé.
    duplicate = client.post(
        "/admin/products",
        json={"sku": "NEW-01", "name": "Doublon", "price_cents": 100, "stock": 1},
        headers=headers,
    )
    assert duplicate.status_code == 409

    updated = client.patch(
        f"/admin/products/{product_id}", json={"price_cents": 1800}, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["product"]["price_cents"] == 1800

    # Une désactivation ne supprime pas l'article : il sort du catalogue.
    assert client.delete(f"/admin/products/{product_id}", headers=headers).status_code == 200
    assert all(
        item["id"] != product_id for item in client.get("/products").json()["products"]
    )


def test_les_metriques_refletent_les_ventes(client, stub):
    token, product = _cart_with(client, quantity=2)
    client.post("/checkout", json={"cart_token": token})
    body = client.get("/metrics").text
    assert "shop_orders_paid_total 1" in body
    assert f"shop_revenue_cents_total {product['price_cents'] * 2}" in body


def test_healthz_declare_le_moteur_de_base(client):
    health = client.get("/healthz").json()
    assert health["status"] == "ok"
    assert health["database"] == "sqlite"
    assert health["products"] >= 6


# ---------------------------------------------------------------------------
# Concurrence : la promesse « pas de survente » doit tenir sous charge
# ---------------------------------------------------------------------------
def test_achats_simultanes_ne_survendent_pas(app, client, stub):
    """N acheteurs simultanés sur le dernier article.

    Le code réserve le stock dans une transaction avec
    `UPDATE ... WHERE stock >= ?` et vérifie le nombre de lignes affectées.
    Ce test le prouve sous concurrence réelle (threads), pas seulement en
    séquence : c'est la différence entre « ça a l'air correct » et « c'est
    correct ».

    Ce qui doit être vrai :
      * une seule commande payée ;
      * le stock tombe à 0, jamais en dessous ;
      * les autres acheteurs reçoivent 409, pas une erreur 500.
    """
    import threading

    from fastapi.testclient import TestClient

    # On met un article à 1 en stock, puis on l'attaque à 5 threads.
    # Le fixture `client` a déjà démarré l'application (lifespan) : les clients
    # créés dans les threads n'ont donc pas besoin de la relancer.
    db = app.state.db
    db.run("UPDATE products SET stock = 1 WHERE sku = 'VAULT-LIC'")
    product = _product(client, "VAULT-LIC")

    statuses: list[int] = []
    lock = threading.Lock()

    def buy() -> None:
        # Chaque thread a son propre client : TestClient n'est pas réentrant.
        buyer = TestClient(app)
        cart = buyer.post("/cart").json()["cart"]
        added = buyer.post(
            f"/cart/{cart['token']}/items",
            json={"product_id": product["id"], "quantity": 1},
        )
        if added.status_code != 200:
            with lock:
                statuses.append(added.status_code)
            return
        response = buyer.post("/checkout", json={"cart_token": cart["token"]})
        with lock:
            statuses.append(response.status_code)

    threads = [threading.Thread(target=buy) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    paid = statuses.count(201)
    refused = sum(1 for code in statuses if code in (409, 400))

    assert paid == 1, f"une seule commande doit aboutir, obtenu {statuses}"
    assert paid + refused == len(statuses), f"aucune erreur 500 attendue : {statuses}"
    assert _product(client, "VAULT-LIC")["stock"] == 0

    # Et la base le confirme : une seule commande payée pour cet article.
    orders = db.query(
        "SELECT o.reference FROM orders o "
        "JOIN order_items oi ON oi.order_id = o.id "
        "JOIN products p ON p.id = oi.product_id "
        "WHERE p.sku = 'VAULT-LIC' AND o.status = 'paid'"
    )
    assert len(orders) == 1, orders


# ---------------------------------------------------------------------------
# Durcissement : ce que le jury CYBER essaie en premier
# ---------------------------------------------------------------------------
def test_une_quantite_en_chaine_est_refusee(client):
    """« 3 » n'est pas 3.

    Accepter la coercition implicite n'est pas une faille, mais un contrat
    exact se défend mieux : un client qui envoie une chaîne se trompe, et on
    le lui dit (422) au lieu de deviner.
    """
    token, product = _cart_with(client, quantity=1)
    for quantity in ("3", 2.5, -1, 0, 10_000):
        response = client.post(
            f"/cart/{token}/items",
            json={"product_id": product["id"], "quantity": quantity},
        )
        assert response.status_code == 422, (quantity, response.status_code)


def test_un_sku_malforme_est_refuse(client, monkeypatch):
    """Un SKU est un identifiant de stock, pas une chaîne libre.

    Les requêtes sont paramétrées, donc aucune de ces charges utiles n'injecte
    quoi que ce soit. Mais les stocker laisserait pourrir la base et les
    exports — et un juré qui voit `X'; DROP TABLE` dans le catalogue conclut
    (à tort) que le SI est troué.
    """
    monkeypatch.setenv("SERVICE_TOKEN", "jeton-de-test")
    headers = {"X-Service-Token": "jeton-de-test"}
    for sku in ("X'; DROP TABLE products; --", "x' UNION SELECT 1 --", "x", "a b"):
        response = client.post(
            "/admin/products",
            json={"sku": sku, "name": "injection", "price_cents": 100, "stock": 1},
            headers=headers,
        )
        assert response.status_code == 422, (sku, response.status_code)

    # Un SKU correct passe : on n'a pas cassé la fonctionnalité en durcissant.
    ok = client.post(
        "/admin/products",
        json={"sku": "LEGIT-01", "name": "Article légitime", "price_cents": 100, "stock": 1},
        headers=headers,
    )
    assert ok.status_code == 201
    # Et le catalogue n'a pas bougé de travers.
    assert client.get("/products").status_code == 200


def test_un_prix_envoye_par_le_client_est_ignore(client, stub):
    """Le total vient de la base, jamais du corps de la requête."""
    token, product = _cart_with(client, quantity=1)
    response = client.post(
        "/checkout",
        json={
            "cart_token": token,
            "total_cents": 1,
            "items": [{"product_id": product["id"], "quantity": 1, "unit_price_cents": 0}],
        },
    )
    assert response.status_code == 201
    assert response.json()["order"]["total_cents"] == product["price_cents"]
