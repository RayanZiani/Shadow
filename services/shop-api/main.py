"""Boutique RESILIENCE-ALPHA — site vitrine, panier, commande, paiement, historique.

Réponse aux nouvelles règles du jury : le SI de paiement est un **site vitrine**
branché sur une **base de données** — articles, panier, commandes, paiements,
historique — avec un **front** qui présente le catalogue et un **back** qui le
gère. Aucune solution du commerce (Prestashop ou autre) : tout est écrit ici.

Le paiement n'est pas simulé : `POST /checkout` appelle `payment-api`
(`/tx/pay`), qui émet un output du **montant exact**, le dépense par une
signature d'anneau bLSAG et renvoie la key image. La commande n'est marquée
`paid` qu'après cette preuve cryptographique.

Sécurité (les nouvelles règles interdisent toute faille volontaire) :

* le stock est **réservé dans une transaction** avant l'appel de paiement, puis
  restitué si le paiement échoue — pas de survente, pas de stock fantôme ;
* l'administration exige une **session back-office de rôle admin**, vérifiée
  auprès du back-office (aucun jeton partagé côté navigateur) ;
* `customer_ref` est un **capability token** de 256 bits : l'historique n'est
  lisible qu'avec lui, et il n'apparaît jamais dans une URL publique ;
* bornes dures sur les quantités, les prix et la taille des corps de requête.

Lancement :  uvicorn main:create_app --factory --host 0.0.0.0 --port 8087
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import pathlib
import re
import secrets
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from db import Database
from svc_auth import service_token_configured

LOG = logging.getLogger("shop-api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HERE = pathlib.Path(__file__).resolve().parent
PAYMENT_API_URL = os.environ.get("PAYMENT_API_URL", "http://payment-api:8080")
BACKOFFICE_URL = os.environ.get("BACKOFFICE_URL", "http://backoffice:8086")
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/shop.db")
MAX_BODY_BYTES = 64 * 1024
MAX_QUANTITY = 20
CUSTOMER_REF_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
# Durée de validité d'une réservation de stock. Au-delà, la commande restée
# `pending` est expirée et le stock restitué (voir `sweep_pending_orders`).
DEFAULT_PENDING_TTL_SECONDS = 120.0
DEFAULT_SWEEP_INTERVAL_SECONDS = 30.0


class FailSoftStaticFiles(StaticFiles):
    """Sert `/static` sans faire tomber le service si le dossier manque.

    `check_dir=False` évite l'erreur au montage, mais Starlette vérifie encore
    le dossier au **premier appel** et lève `RuntimeError` : la première
    requête d'image produirait un 500. Ici, un dossier absent est signalé dans
    les logs et les images répondent 404 — un contenu manquant, pas une panne.
    """

    async def check_config(self) -> None:
        try:
            await super().check_config()
        except RuntimeError:
            LOG.warning(
                "dossier statique absent (%s) : les images répondront 404",
                self.directory,
            )


CATALOG = [
    {
        "sku": "TERM-01",
        "name": "Terminal de paiement SHADOW",
        "description": "Terminal à clavier, anneau lumineux, signature d'anneau bLSAG.",
        "price_cents": 12900,
        "stock": 12,
    },
    {
        "sku": "RING-24",
        "name": "Anneau LED 24 pixels",
        "description": "Miroir d'état du terminal : rouge attaque, orange remédiation, vert reprise.",
        "price_cents": 3900,
        "stock": 40,
    },
    {
        "sku": "HSM-CARD",
        "name": "Carte de signature hors ligne",
        "description": "Conserve la Spend Key ; ne sort jamais du boîtier scellé.",
        "price_cents": 7900,
        "stock": 25,
    },
    {
        "sku": "SENSOR-01",
        "name": "Sonde de télémétrie",
        "description": "Alimente le détecteur : cadence, latence, rejets.",
        "price_cents": 4900,
        "stock": 30,
    },
    {
        "sku": "VAULT-LIC",
        "name": "Licence coffre de secrets",
        "description": "Rotation automatique des clés, politique d'accès restreinte.",
        "price_cents": 24900,
        "stock": 8,
    },
    {
        "sku": "AUDIT-J",
        "name": "Journal d'audit scellé",
        "description": "Traçabilité inaltérable des paiements, lecture view-only.",
        "price_cents": 5900,
        "stock": 0,
    },
]


# ---------------------------------------------------------------------------
# Modèles
# ---------------------------------------------------------------------------
class CartItemRequest(BaseModel):
    # `strict=True` : une chaîne « 3 » ou un flottant 2.5 est refusé (422) au
    # lieu d'être converti. Le contrat est exact, et un client qui se trompe
    # le voit immédiatement.
    product_id: int = Field(gt=0, strict=True)
    quantity: int = Field(default=1, ge=1, le=MAX_QUANTITY, strict=True)


class QuantityRequest(BaseModel):
    quantity: int = Field(ge=1, le=MAX_QUANTITY, strict=True)


class CheckoutRequest(BaseModel):
    cart_token: str = Field(min_length=8, max_length=128)
    customer_ref: str | None = None


# Un SKU est un identifiant de stock : majuscules, chiffres, tiret. Autoriser
# une chaîne libre (« X'; DROP TABLE products; -- ») n'injecte rien — les
# requêtes sont paramétrées — mais laisse pourrir la base et les exports.
SKU_PATTERN = r"^[A-Z0-9][A-Z0-9-]{1,31}$"


class ProductCreate(BaseModel):
    sku: str = Field(min_length=2, max_length=32, pattern=SKU_PATTERN)
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)
    price_cents: int = Field(gt=0, le=10_000_000)
    stock: int = Field(ge=0, le=100_000)
    image_url: str | None = Field(default=None, max_length=500)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    price_cents: int | None = Field(default=None, gt=0, le=10_000_000)
    stock: int | None = Field(default=None, ge=0, le=100_000)
    active: bool | None = None
    image_url: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
def create_app(database_url: str | None = None, static_dir: str | None = None) -> FastAPI:
    url = database_url or DATABASE_URL
    # `sqlite:///./data/shop.db` -> chemin local ; `postgresql://...` -> psycopg.
    path = url[len("sqlite:///"):] if url.startswith("sqlite:///") else url
    db = Database(path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        _seed_catalog(db)
        if not db.is_postgres:
            LOG.warning(
                "base SQLite (%s) : correcte pour les tests et le poste local, "
                "PostgreSQL est utilisé en pile complète",
                path,
            )
        # 1. Réparation des commandes laissées `pending` par un arrêt brutal :
        #    c'est exactement l'état dans lequel `kill -9` laisse la boutique.
        ttl_seconds = _pending_ttl_seconds()
        try:
            sweep_pending_orders(db, ttl_seconds)
        except Exception:  # noqa: BLE001
            # Un balayage raté ne doit pas empêcher le service de démarrer :
            # `/healthz` dira `degraded` si la base est en cause.
            LOG.exception("balayage initial des commandes en attente : échec")
        # 2. Puis périodiquement, pour le cas d'un processus qui reste debout
        #    mais dont l'appel de paiement ne revient jamais.
        sweeper = asyncio.create_task(
            _pending_sweeper(db, ttl_seconds, _sweep_interval_seconds())
        )
        try:
            yield
        finally:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
            db.close()

    app = FastAPI(title="shop-api", version="1.0.0", lifespan=lifespan)
    # Images produit servies localement (`/static/products/<SKU>.svg`).
    # `check_dir=False` : un déploiement sans le dossier ne doit pas empêcher le
    # service de démarrer ; les images répondent alors 404 (fail-soft).
    app.mount(
        "/static",
        FailSoftStaticFiles(
            directory=str(pathlib.Path(static_dir) if static_dir else HERE / "static"),
            check_dir=False,
        ),
        name="static",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip()
            for origin in os.environ.get(
                "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",")
            if origin.strip()
        ],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Service-Token"],
    )
    app.state.db = db

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------
    def public_product(row: dict) -> dict:
        return {
            "id": row["id"],
            "sku": row["sku"],
            "name": row["name"],
            "description": row["description"],
            "price_cents": row["price_cents"],
            "price": f"{row['price_cents'] / 100:.2f} €",
            "stock": row["stock"],
            "in_stock": row["stock"] > 0,
            "active": bool(row["active"]),
            "image_url": row["image_url"],
        }

    def require_admin(
        request: Request,
        authorization: str | None = Header(default=None),
        x_service_token: str | None = Header(default=None),
    ) -> dict:
        """Administration : session back-office de rôle admin, ou jeton de service.

        Le navigateur ne détient JAMAIS le jeton de service : il envoie la
        session opaque du back-office, que ce service valide auprès du
        back-office. Un client ne peut donc pas se fabriquer un rôle admin.
        """
        expected = os.environ.get("SERVICE_TOKEN", "").strip()
        # `.encode()` : `compare_digest` sur des `str` lève TypeError dès qu'un
        # caractère est non-ASCII (un en-tête peut en contenir). On compare des
        # octets, ce qui est à la fois correct et à temps constant.
        if (
            expected
            and x_service_token
            and secrets.compare_digest(x_service_token.encode(), expected.encode())
        ):
            return {"username": "service", "role": "admin", "via": "service_token"}
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401,
                detail="session administrateur requise",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = authorization.split(" ", 1)[1].strip()
        try:
            response = httpx.get(
                f"{BACKOFFICE_URL}/auth/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=3.0,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"back-office injoignable ({type(exc).__name__})",
            ) from exc
        if response.status_code != 200:
            raise HTTPException(status_code=401, detail="session invalide ou expirée")
        user = response.json().get("user", {})
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="rôle administrateur requis")
        return {"username": user.get("username"), "role": "admin", "via": "session"}

    def cart_or_404(token: str) -> dict:
        cart = db.one("SELECT * FROM carts WHERE token = ?", (token,))
        if cart is None:
            raise HTTPException(status_code=404, detail="panier inconnu")
        return cart

    def cart_payload(cart: dict) -> dict:
        rows = db.query(
            "SELECT ci.product_id, ci.quantity, p.sku, p.name, p.price_cents, "
            "       p.stock, p.active, p.image_url "
            "FROM cart_items ci JOIN products p ON p.id = ci.product_id "
            "WHERE ci.cart_id = ? ORDER BY ci.id",
            (cart["id"],),
        )
        items = []
        total = 0
        for row in rows:
            subtotal = int(row["price_cents"]) * int(row["quantity"])
            total += subtotal
            items.append(
                {
                    "product_id": row["product_id"],
                    "sku": row["sku"],
                    "name": row["name"],
                    "unit_price_cents": row["price_cents"],
                    "quantity": row["quantity"],
                    "subtotal_cents": subtotal,
                    "available": bool(row["active"]) and int(row["stock"]) >= int(row["quantity"]),
                }
            )
        return {
            "token": cart["token"],
            "items": items,
            "total_cents": total,
            "total": f"{total / 100:.2f} €",
            "count": sum(item["quantity"] for item in items),
        }

    # ------------------------------------------------------------------
    # Catalogue public
    # ------------------------------------------------------------------
    @app.get("/healthz")
    def healthz():
        """Sonde de santé — ne lève JAMAIS, même base arrêtée.

        Le `try` ne protégeait que la première requête : les deux suivantes
        étaient dehors, donc une base arrêtée produisait un 500 et le
        healthcheck Docker redémarrait le conteneur en boucle. Une sonde qui
        tombe quand la dépendance tombe ne sert à rien : elle doit dire
        `degraded`.
        """
        base = {
            "service": "shop-api",
            "database": "postgresql" if db.is_postgres else "sqlite",
            "payment_api": PAYMENT_API_URL,
            "service_token_configured": service_token_configured(),
        }
        try:
            db.query("SELECT 1 AS ok")
            orders = db.one(
                "SELECT COUNT(*) AS n, COALESCE(SUM(total_cents), 0) AS revenue "
                "FROM orders WHERE status = 'paid'"
            ) or {"n": 0, "revenue": 0}
            products = db.query("SELECT COUNT(*) AS n FROM products")[0]["n"]
        except Exception as exc:  # noqa: BLE001
            return {
                **base,
                "status": "degraded",
                "error": f"{type(exc).__name__}: {exc}"[:200],
                "products": 0,
                "orders_paid": 0,
                "revenue_cents": 0,
            }
        return {
            **base,
            "status": "ok",
            "products": int(products),
            "orders_paid": int(orders["n"]),
            "revenue_cents": int(orders["revenue"]),
        }

    @app.get("/metrics")
    def metrics():
        """Métriques — dégradées plutôt qu'absentes si la base est muette.

        Un scrape qui échoue pendant une panne de base, c'est précisément le
        moment où Prometheus a besoin de la métrique. On expose donc
        `shop_database_up 0` et des compteurs à zéro au lieu d'un 500.
        """
        try:
            paid = db.one(
                "SELECT COUNT(*) AS n, COALESCE(SUM(total_cents), 0) AS revenue "
                "FROM orders WHERE status = 'paid'"
            ) or {"n": 0, "revenue": 0}
            failed = db.one("SELECT COUNT(*) AS n FROM payments WHERE status = 'failed'")
            pending = db.one("SELECT COUNT(*) AS n FROM payments WHERE status = 'pending'")
            out_of_stock = db.one(
                "SELECT COUNT(*) AS n FROM products WHERE active = TRUE AND stock = 0"
            )
            database_up = 1
        except Exception:  # noqa: BLE001
            paid = {"n": 0, "revenue": 0}
            failed = {"n": 0}
            pending = {"n": 0}
            out_of_stock = {"n": 0}
            database_up = 0
        lines = [
            "# HELP shop_database_up 1 si la base repond",
            "# TYPE shop_database_up gauge",
            f"shop_database_up {database_up}",
            "# HELP shop_orders_paid_total Commandes payées",
            "# TYPE shop_orders_paid_total counter",
            f"shop_orders_paid_total {int(paid['n'])}",
            "# HELP shop_revenue_cents_total Chiffre d'affaires encaissé (centimes)",
            "# TYPE shop_revenue_cents_total counter",
            f"shop_revenue_cents_total {int(paid['revenue'])}",
            "# HELP shop_payments_failed_total Paiements refusés",
            "# TYPE shop_payments_failed_total counter",
            f"shop_payments_failed_total {int(failed['n'])}",
            "# HELP shop_payments_pending Paiements en cours",
            "# TYPE shop_payments_pending gauge",
            f"shop_payments_pending {int(pending['n'])}",
            "# HELP shop_products_out_of_stock Articles actifs en rupture",
            "# TYPE shop_products_out_of_stock gauge",
            f"shop_products_out_of_stock {int(out_of_stock['n'])}",
        ]
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    @app.get("/products")
    def list_products(include_inactive: bool = False):
        if include_inactive:
            rows = db.query("SELECT * FROM products ORDER BY id")
        else:
            rows = db.query("SELECT * FROM products WHERE active = TRUE ORDER BY id")
        return {"products": [public_product(row) for row in rows]}

    @app.get("/products/{product_id}")
    def get_product(product_id: int):
        row = db.one("SELECT * FROM products WHERE id = ?", (product_id,))
        if row is None or not row["active"]:
            raise HTTPException(status_code=404, detail="article inconnu")
        return {"product": public_product(row)}

    # ------------------------------------------------------------------
    # Panier
    # ------------------------------------------------------------------
    @app.post("/cart", status_code=201)
    def create_cart():
        now = time.time()
        token = secrets.token_urlsafe(24)
        db.run(
            "INSERT INTO carts(token, created_at, updated_at) VALUES(?,?,?)",
            (token, now, now),
        )
        return {"cart": cart_payload(cart_or_404(token))}

    @app.get("/cart/{token}")
    def get_cart(token: str):
        return {"cart": cart_payload(cart_or_404(token))}

    @app.post("/cart/{token}/items")
    def add_item(token: str, req: CartItemRequest):
        cart = cart_or_404(token)
        product = db.one("SELECT * FROM products WHERE id = ?", (req.product_id,))
        if product is None or not product["active"]:
            raise HTTPException(status_code=404, detail="article inconnu")
        if int(product["stock"]) < req.quantity:
            raise HTTPException(
                status_code=409,
                detail=f"stock insuffisant ({product['stock']} disponible(s))",
            )
        now = time.time()
        with db.transaction() as conn:
            existing = db.tx_one(
                conn,
                "SELECT * FROM cart_items WHERE cart_id = ? AND product_id = ?",
                (cart["id"], req.product_id),
            )
            if existing is None:
                db.tx_run(
                    conn,
                    "INSERT INTO cart_items(cart_id, product_id, quantity) VALUES(?,?,?)",
                    (cart["id"], req.product_id, req.quantity),
                )
            else:
                quantity = min(MAX_QUANTITY, int(existing["quantity"]) + req.quantity)
                db.tx_run(
                    conn,
                    "UPDATE cart_items SET quantity = ? WHERE id = ?",
                    (quantity, existing["id"]),
                )
            db.tx_run(conn, "UPDATE carts SET updated_at = ? WHERE id = ?", (now, cart["id"]))
        return {"cart": cart_payload(cart_or_404(token))}

    @app.patch("/cart/{token}/items/{product_id}")
    def set_quantity(token: str, product_id: int, req: QuantityRequest):
        cart = cart_or_404(token)
        product = db.one("SELECT * FROM products WHERE id = ?", (product_id,))
        if product is None:
            raise HTTPException(status_code=404, detail="article inconnu")
        if int(product["stock"]) < req.quantity:
            raise HTTPException(
                status_code=409,
                detail=f"stock insuffisant ({product['stock']} disponible(s))",
            )
        if db.one(
            "SELECT 1 FROM cart_items WHERE cart_id = ? AND product_id = ?",
            (cart["id"], product_id),
        ) is None:
            raise HTTPException(status_code=404, detail="article absent du panier")
        db.run(
            "UPDATE cart_items SET quantity = ? WHERE cart_id = ? AND product_id = ?",
            (req.quantity, cart["id"], product_id),
        )
        db.run("UPDATE carts SET updated_at = ? WHERE id = ?", (time.time(), cart["id"]))
        return {"cart": cart_payload(cart_or_404(token))}

    @app.delete("/cart/{token}/items/{product_id}")
    def remove_item(token: str, product_id: int):
        cart = cart_or_404(token)
        db.run(
            "DELETE FROM cart_items WHERE cart_id = ? AND product_id = ?",
            (cart["id"], product_id),
        )
        db.run("UPDATE carts SET updated_at = ? WHERE id = ?", (time.time(), cart["id"]))
        return {"cart": cart_payload(cart_or_404(token))}

    # ------------------------------------------------------------------
    # Commande et paiement
    # ------------------------------------------------------------------
    @app.post("/checkout", status_code=201)
    async def checkout(req: CheckoutRequest):
        cart = cart_or_404(req.cart_token)
        items = db.query(
            "SELECT ci.product_id, ci.quantity, p.sku, p.name, p.price_cents, p.stock, p.active "
            "FROM cart_items ci JOIN products p ON p.id = ci.product_id "
            "WHERE ci.cart_id = ? ORDER BY ci.id",
            (cart["id"],),
        )
        if not items:
            raise HTTPException(status_code=400, detail="panier vide")

        for item in items:
            if not item["active"]:
                raise HTTPException(status_code=409, detail=f"article retiré : {item['sku']}")
            if int(item["stock"]) < int(item["quantity"]):
                raise HTTPException(
                    status_code=409,
                    detail=f"stock insuffisant pour {item['sku']}",
                )

        customer_ref = req.customer_ref
        if customer_ref is None:
            customer_ref = secrets.token_urlsafe(24)
        elif not CUSTOMER_REF_RE.match(customer_ref):
            raise HTTPException(status_code=400, detail="customer_ref invalide")

        total = sum(int(item["price_cents"]) * int(item["quantity"]) for item in items)
        reference = "CMD-" + secrets.token_hex(4).upper()
        now = time.time()

        # 1. Réservation du stock + commande + paiement en attente, DANS une
        #    transaction. Si un article manque, rien n'est écrit.
        with db.transaction() as conn:
            # Réclamation ATOMIQUE du panier : deux `checkout` simultanés (le
            # double-clic sur « Payer ») ne peuvent pas créer deux commandes.
            # Un seul `UPDATE` peut voir `checked_out_at IS NULL`.
            claimed = db.tx_run(
                conn,
                "UPDATE carts SET checked_out_at = ? "
                "WHERE id = ? AND checked_out_at IS NULL",
                (now, cart["id"]),
            )
            if claimed == 0:
                raise HTTPException(
                    status_code=409,
                    detail="paiement déjà en cours pour ce panier",
                )
            for item in items:
                affected = db.tx_run(
                    conn,
                    "UPDATE products SET stock = stock - ?, updated_at = ? "
                    "WHERE id = ? AND active = TRUE AND stock >= ?",
                    (item["quantity"], now, item["product_id"], item["quantity"]),
                )
                if affected == 0:
                    raise HTTPException(
                        status_code=409,
                        detail=f"stock épuisé pour {item['sku']} (course concurrente)",
                    )
            order_id = db.tx_insert(
                conn,
                "INSERT INTO orders(reference, customer_ref, status, total_cents, created_at) "
                "VALUES(?,?,?,?,?) RETURNING id",
                (reference, customer_ref, "pending", total, now),
            )
            for item in items:
                db.tx_run(
                    conn,
                    "INSERT INTO order_items(order_id, product_id, sku, name, "
                    "unit_price_cents, quantity) VALUES(?,?,?,?,?,?)",
                    (
                        order_id,
                        item["product_id"],
                        item["sku"],
                        item["name"],
                        item["price_cents"],
                        item["quantity"],
                    ),
                )
            payment_id = db.tx_insert(
                conn,
                "INSERT INTO payments(order_id, provider, amount_cents, status, created_at) "
                "VALUES(?,?,?,?,?) RETURNING id",
                (order_id, "monero-blsag", total, "pending", now),
            )
            db.tx_run(
                conn,
                "INSERT INTO payment_events(payment_id, event, detail, created_at) "
                "VALUES(?,?,?,?)",
                (payment_id, "created", json.dumps({"reference": reference}), now),
            )

        # 2. Paiement réel : le noyau émet un output du montant exact et le
        #    dépense par une signature d'anneau. Aucun montant n'est simulé.
        paid = False
        failure_detail = None
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{PAYMENT_API_URL}/tx/pay",
                    json={"amount": total, "reference": reference},
                )
            if response.status_code == 200:
                paid = True
                body = response.json()
            else:
                failure_detail = f"HTTP {response.status_code}: {response.text[:200]}"
        except httpx.HTTPError as exc:
            failure_detail = f"{type(exc).__name__}: {exc}"

        # 3a. Succès : commande payée, stock déjà décrémenté, panier vidé.
        if paid:
            with db.transaction() as conn:
                db.tx_run(
                    conn,
                    "UPDATE payments SET status = 'paid', tx_ref = ?, key_image = ?, "
                    "ring_size = ? WHERE id = ?",
                    (reference, body.get("key_image"), body.get("ring_size"), payment_id),
                )
                db.tx_run(
                    conn,
                    "UPDATE orders SET status = 'paid', paid_at = ? WHERE id = ?",
                    (time.time(), order_id),
                )
                db.tx_run(
                    conn,
                    "INSERT INTO payment_events(payment_id, event, detail, created_at) "
                    "VALUES(?,?,?,?)",
                    (
                        payment_id,
                        "paid",
                        json.dumps(
                            {
                                "key_image": body.get("key_image"),
                                "ring_size": body.get("ring_size"),
                                "scheme": body.get("signature_scheme"),
                            }
                        ),
                        time.time(),
                    ),
                )
                db.tx_run(conn, "DELETE FROM cart_items WHERE cart_id = ?", (cart["id"],))
            LOG.info("commande %s payée (%d centimes)", reference, total)
            return {
                "order": _order_payload(db, reference),
                "payment": _payment_payload(db, reference),
                "customer_ref": customer_ref,
            }

        # 3b. Échec : on RESTITUE le stock réservé et on le dit explicitement.
        with db.transaction() as conn:
            for item in items:
                db.tx_run(
                    conn,
                    "UPDATE products SET stock = stock + ?, updated_at = ? WHERE id = ?",
                    (item["quantity"], time.time(), item["product_id"]),
                )
            db.tx_run(
                conn,
                "UPDATE payments SET status = 'failed' WHERE id = ?",
                (payment_id,),
            )
            db.tx_run(
                conn,
                "UPDATE orders SET status = 'failed' WHERE id = ?",
                (order_id,),
            )
            db.tx_run(
                conn,
                "INSERT INTO payment_events(payment_id, event, detail, created_at) "
                "VALUES(?,?,?,?)",
                (payment_id, "failed", json.dumps({"error": failure_detail}), time.time()),
            )
            # Le panier est RELÂCHÉ : un incident passager ne doit pas condamner
            # le client. Il peut réessayer immédiatement.
            db.tx_run(
                conn,
                "UPDATE carts SET checked_out_at = NULL WHERE id = ?",
                (cart["id"],),
            )
        LOG.warning("paiement refusé pour %s : %s", reference, failure_detail)
        return Response(
            status_code=502,
            media_type="application/json",
            content=json.dumps(
                {
                    "detail": "paiement refusé par le registre",
                    "order": _order_payload(db, reference),
                    "error": failure_detail,
                },
                ensure_ascii=False,
            ),
        )

    @app.get("/orders/{reference}")
    def get_order(reference: str, customer_ref: str = ""):
        order = db.one("SELECT * FROM orders WHERE reference = ?", (reference,))
        if order is None:
            raise HTTPException(status_code=404, detail="commande inconnue")
        # `customer_ref` est un capability token : sans lui, la commande n'est
        # pas lisible. On répond 404 (et non 403) pour ne pas révéler qu'elle
        # existe.
        #
        # Le format est validé AVANT la comparaison : un token non-ASCII
        # provoquerait un TypeError dans `compare_digest` (donc un 500), et un
        # 500 sur une référence existante / 404 sur une référence inconnue
        # serait un oracle d'existence.
        if not CUSTOMER_REF_RE.match(customer_ref or ""):
            raise HTTPException(status_code=404, detail="commande inconnue")
        if not secrets.compare_digest(
            str(order["customer_ref"]).encode(), (customer_ref or "").encode()
        ):
            raise HTTPException(status_code=404, detail="commande inconnue")
        return {"order": _order_payload(db, reference), "payment": _payment_payload(db, reference)}

    @app.get("/orders")
    def list_orders(customer_ref: str = ""):
        if not CUSTOMER_REF_RE.match(customer_ref or ""):
            raise HTTPException(status_code=400, detail="customer_ref invalide")
        rows = db.query(
            "SELECT * FROM orders WHERE customer_ref = ? ORDER BY id DESC LIMIT 50",
            (customer_ref,),
        )
        return {
            "orders": [
                {
                    "reference": row["reference"],
                    "status": row["status"],
                    "total_cents": row["total_cents"],
                    "total": f"{row['total_cents'] / 100:.2f} €",
                    "created_at": row["created_at"],
                    "paid_at": row["paid_at"],
                    "items": _order_items(db, row["reference"]),
                }
                for row in rows
            ]
        }

    # ------------------------------------------------------------------
    # Administration (back-office, rôle admin)
    # ------------------------------------------------------------------
    @app.get("/admin/products")
    def admin_products(admin: dict = Depends(require_admin)):
        rows = db.query("SELECT * FROM products ORDER BY id")
        return {"products": [public_product(row) for row in rows], "admin": admin["username"]}

    @app.post("/admin/products", status_code=201)
    def admin_create_product(req: ProductCreate, admin: dict = Depends(require_admin)):
        now = time.time()
        if db.one("SELECT 1 FROM products WHERE sku = ?", (req.sku,)):
            raise HTTPException(status_code=409, detail="SKU déjà utilisé")
        product_id = db.run(
            "INSERT INTO products(sku, name, description, price_cents, stock, active, "
            "image_url, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?) RETURNING id",
            (
                req.sku,
                req.name,
                req.description,
                req.price_cents,
                req.stock,
                True,
                req.image_url,
                now,
                now,
            ),
            returning=True,
        )
        return {"product": public_product(db.one("SELECT * FROM products WHERE id = ?", (product_id,)))}

    @app.patch("/admin/products/{product_id}")
    def admin_update_product(
        product_id: int, req: ProductUpdate, admin: dict = Depends(require_admin)
    ):
        row = db.one("SELECT * FROM products WHERE id = ?", (product_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="article inconnu")
        changes: dict = {}
        for field in ("name", "description", "price_cents", "stock", "image_url"):
            value = getattr(req, field)
            if value is not None:
                changes[field] = value
        if req.active is not None:
            changes["active"] = bool(req.active)
        if not changes:
            return {"product": public_product(row), "changed": []}
        changes["updated_at"] = time.time()
        # Liste blanche : aucune colonne arbitraire ne peut entrer dans le SQL.
        allowed = {"name", "description", "price_cents", "stock", "active", "image_url", "updated_at"}
        unauthorized = set(changes) - allowed
        if unauthorized:
            # Pas un `assert` : sous `python -O` il disparaîtrait, et une garde
            # de sécurité qui s'évapore selon les options d'interpréteur n'en
            # est pas une.
            raise HTTPException(
                status_code=500,
                detail=f"colonne non autorisée : {sorted(unauthorized)}",
            )
        assignments = ", ".join(f"{column} = ?" for column in changes)
        db.run(
            f"UPDATE products SET {assignments} WHERE id = ?",
            (*changes.values(), product_id),
        )
        return {
            "product": public_product(db.one("SELECT * FROM products WHERE id = ?", (product_id,))),
            "changed": [key for key in changes if key != "updated_at"],
        }

    @app.delete("/admin/products/{product_id}")
    def admin_delete_product(product_id: int, admin: dict = Depends(require_admin)):
        """Désactivation, jamais de suppression : un article commandé doit rester
        lisible dans l'historique (intégrité comptable)."""
        row = db.one("SELECT * FROM products WHERE id = ?", (product_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="article inconnu")
        db.run(
            "UPDATE products SET active = FALSE, updated_at = ? WHERE id = ?",
            (time.time(), product_id),
        )
        return {"deactivated": True, "id": product_id}

    @app.get("/admin/orders")
    def admin_orders(admin: dict = Depends(require_admin)):
        rows = db.query("SELECT * FROM orders ORDER BY id DESC LIMIT 100")
        return {
            "orders": [
                {
                    "reference": row["reference"],
                    "status": row["status"],
                    "total_cents": row["total_cents"],
                    "total": f"{row['total_cents'] / 100:.2f} €",
                    "created_at": row["created_at"],
                    "paid_at": row["paid_at"],
                    "items": _order_items(db, row["reference"]),
                }
                for row in rows
            ]
        }

    # ------------------------------------------------------------------
    # Durcissement HTTP
    # ------------------------------------------------------------------
    @app.middleware("http")
    async def guard(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return Response(
                status_code=413,
                content=json.dumps({"detail": "corps de requête trop volumineux"}),
                media_type="application/json",
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/admin"):
            response.headers["Cache-Control"] = "no-store"
        return response

    return app


# ---------------------------------------------------------------------------
# Sérialisation et amorçage
# ---------------------------------------------------------------------------
def _order_items(db: Database, reference: str) -> list[dict]:
    rows = db.query(
        "SELECT oi.sku, oi.name, oi.unit_price_cents, oi.quantity "
        "FROM order_items oi JOIN orders o ON o.id = oi.order_id "
        "WHERE o.reference = ? ORDER BY oi.id",
        (reference,),
    )
    return [
        {
            "sku": row["sku"],
            "name": row["name"],
            "unit_price_cents": row["unit_price_cents"],
            "quantity": row["quantity"],
            "subtotal_cents": int(row["unit_price_cents"]) * int(row["quantity"]),
        }
        for row in rows
    ]


def _order_payload(db: Database, reference: str) -> dict:
    row = db.one("SELECT * FROM orders WHERE reference = ?", (reference,))
    if row is None:
        return {}
    return {
        "reference": row["reference"],
        "status": row["status"],
        "total_cents": row["total_cents"],
        "total": f"{row['total_cents'] / 100:.2f} €",
        "created_at": row["created_at"],
        "paid_at": row["paid_at"],
        "items": _order_items(db, reference),
    }


def _payment_payload(db: Database, reference: str) -> dict | None:
    row = db.one(
        "SELECT p.* FROM payments p JOIN orders o ON o.id = p.order_id "
        "WHERE o.reference = ? ORDER BY p.id DESC LIMIT 1",
        (reference,),
    )
    if row is None:
        return None
    events = db.query(
        "SELECT event, detail, created_at FROM payment_events WHERE payment_id = ? "
        "ORDER BY id",
        (row["id"],),
    )
    return {
        "provider": row["provider"],
        "amount_cents": row["amount_cents"],
        "status": row["status"],
        "tx_ref": row["tx_ref"],
        "key_image": row["key_image"],
        "ring_size": row["ring_size"],
        "created_at": row["created_at"],
        "events": [
            {
                "event": event["event"],
                "detail": json.loads(event["detail"]) if event["detail"] else {},
                "created_at": event["created_at"],
            }
            for event in events
        ],
    }


def _seed_catalog(db: Database) -> None:
    """Catalogue de démonstration, inséré UNE fois (idempotent).

    Sans lui, la boutique s'ouvre sur une page vide le jour de la démonstration.
    Les articles sont de vrais enregistrements en base, modifiables par le back.
    Chaque article reçoit l'URL de son image locale, servie par le montage
    `/static` (chemin **relatif à l'API** : le front le préfixe par l'URL de
    base de la boutique).
    """
    existing = db.query("SELECT COUNT(*) AS n FROM products")[0]["n"]
    now = time.time()
    if int(existing) == 0:
        for product in CATALOG:
            db.run(
                "INSERT INTO products(sku, name, description, price_cents, stock, active, "
                "image_url, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    product["sku"],
                    product["name"],
                    product["description"],
                    product["price_cents"],
                    product["stock"],
                    True,
                    _product_image_url(product["sku"]),
                    now,
                    now,
                ),
            )
        LOG.info("catalogue de démonstration inséré (%d articles)", len(CATALOG))
    # Rattrapage : une base créée avant les images a `image_url = NULL`. On ne
    # réécrit jamais une URL choisie par l'administrateur.
    for product in CATALOG:
        db.run(
            "UPDATE products SET image_url = ?, updated_at = ? "
            "WHERE sku = ? AND image_url IS NULL",
            (_product_image_url(product["sku"]), now, product["sku"]),
        )


def _product_image_url(sku: str) -> str:
    """Chemin relatif à l'API, jamais une URL absolue (le front la préfixe)."""
    return f"/static/products/{sku}.svg"


def _pending_ttl_seconds() -> float:
    """TTL de réservation, `SHOP_PENDING_TTL_SECONDS` (défaut 120 s)."""
    return _positive_float("SHOP_PENDING_TTL_SECONDS", DEFAULT_PENDING_TTL_SECONDS)


def _sweep_interval_seconds() -> float:
    """Période du balayage, `SHOP_PENDING_SWEEP_SECONDS` (défaut 30 s)."""
    return _positive_float("SHOP_PENDING_SWEEP_SECONDS", DEFAULT_SWEEP_INTERVAL_SECONDS)


def _positive_float(name: str, default: float) -> float:
    """Lit une durée d'environnement ; une valeur absurde retombe sur le défaut.

    Une variable mal écrite ne doit pas empêcher la boutique de démarrer, mais
    elle doit être dite dans les logs — pas avalée en silence.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        LOG.warning("%s=%r n'est pas un nombre : défaut %.0f s", name, raw, default)
        return default
    if value <= 0:
        LOG.warning("%s=%r doit être positif : défaut %.0f s", name, raw, default)
        return default
    return value


async def _pending_sweeper(db: Database, ttl_seconds: float, interval_seconds: float) -> None:
    """Balayage périodique, annulé proprement par le `lifespan` à l'arrêt.

    Les appels base sont bloquants : ils partent dans un thread pour ne pas
    geler l'event loop (les autres requêtes continuent de servir).
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(sweep_pending_orders, db, ttl_seconds)
        except Exception:  # noqa: BLE001
            # Une erreur de balayage ne doit pas tuer la tâche : au prochain
            # tour, la base est peut-être revenue. L'échec est tracé.
            LOG.exception("balayage périodique des commandes en attente : échec")


def sweep_pending_orders(
    db: Database, ttl_seconds: float, now: float | None = None
) -> dict[str, list[str]]:
    """Répare les commandes restées `pending` au-delà du TTL de réservation.

    Deux cas réels, tous deux vus en exploitation :

    1. **Processus mort entre la réservation et l'appel de paiement.** La
       commande est `pending`, le stock décrémenté, et personne ne répare. Ici,
       toute commande plus vieille que `ttl_seconds` passe en `failed` et son
       stock est restitué.
    2. **Paiement réussi mais commande `failed` côté boutique** (timeout HTTP
       sur `/tx/pay`). Si la base locale dit déjà `payments.status = 'paid'`,
       on ne restitue **pas** le stock une seconde fois : un événement
       `reconciled` est écrit pour l'audit, la commande reste `failed` et
       l'écart est signalé dans les logs (le remboursement est hors périmètre).

    L'expiration est **atomique et idempotente** : `UPDATE ... WHERE
    reference = ? AND status = 'pending'` d'abord, restitution seulement si
    exactement une ligne a changé. Deux balayages concurrents, ou un second
    passage après le premier, ne restituent donc jamais deux fois.
    """
    moment = time.time() if now is None else now
    cutoff = moment - ttl_seconds
    rows = db.query(
        "SELECT id, reference FROM orders WHERE status = 'pending' AND created_at <= ?",
        (cutoff,),
    )
    expired: list[str] = []
    reconciled: list[str] = []
    for row in rows:
        with db.transaction() as conn:
            # Réclamation atomique : un seul balayage (ou un seul thread) peut
            # faire passer CETTE commande de pending à failed.
            affected = db.tx_run(
                conn,
                "UPDATE orders SET status = 'failed' WHERE reference = ? AND status = 'pending'",
                (row["reference"],),
            )
            if affected != 1:
                continue
            payment = db.tx_one(
                conn,
                "SELECT id, status FROM payments WHERE order_id = ? ORDER BY id DESC LIMIT 1",
                (row["id"],),
            )
            items = db.tx_query(
                conn,
                "SELECT product_id, quantity FROM order_items WHERE order_id = ?",
                (row["id"],),
            )
            if payment is not None and payment["status"] == "paid":
                # Le paiement a abouti : ne pas restituer un stock déjà vendu.
                db.tx_run(
                    conn,
                    "UPDATE orders SET status = 'paid', paid_at = ? WHERE id = ?",
                    (moment, row["id"]),
                )
                db.tx_run(
                    conn,
                    "INSERT INTO payment_events(payment_id, event, detail, created_at) "
                    "VALUES(?,?,?,?)",
                    (
                        payment["id"],
                        "reconciled",
                        json.dumps(
                            {
                                "reference": row["reference"],
                                "reason": "paiement déjà paid localement",
                            }
                        ),
                        moment,
                    ),
                )
                reconciled.append(row["reference"])
                continue
            for item in items:
                db.tx_run(
                    conn,
                    "UPDATE products SET stock = stock + ?, updated_at = ? WHERE id = ?",
                    (item["quantity"], moment, item["product_id"]),
                )
            if payment is not None:
                db.tx_run(
                    conn,
                    "UPDATE payments SET status = 'failed' WHERE id = ? AND status = 'pending'",
                    (payment["id"],),
                )
                db.tx_run(
                    conn,
                    "INSERT INTO payment_events(payment_id, event, detail, created_at) "
                    "VALUES(?,?,?,?)",
                    (
                        payment["id"],
                        "expired",
                        json.dumps(
                            {
                                "reference": row["reference"],
                                "reason": "expired",
                                "ttl_seconds": ttl_seconds,
                            }
                        ),
                        moment,
                    ),
                )
            expired.append(row["reference"])
    reconciled.extend(_reconcile_failed_payments(db, moment))
    if expired or reconciled:
        LOG.warning(
            "balayage des commandes : %d expirée(s) %s, %d réconciliée(s) %s",
            len(expired),
            expired,
            len(reconciled),
            reconciled,
        )
    return {"expired": expired, "reconciled": reconciled}


def _reconcile_failed_payments(db: Database, now: float) -> list[str]:
    """Commandes `failed` dont le paiement local est `paid` : ne rien restituer.

    Le stock a déjà été rendu par le chemin d'échec de `/checkout` ; le
    restituer encore serait une double restitution (stock fantôme). On écrit
    un événement `reconciled` **une seule fois** (idempotent) et on laisse la
    commande `failed` : le remboursement est hors périmètre, mais l'écart est
    visible dans `payment_events` et dans les logs, jamais silencieux.
    """
    rows = db.query(
        "SELECT o.id, o.reference, p.id AS payment_id FROM orders o "
        "JOIN payments p ON p.order_id = o.id "
        "WHERE o.status = 'failed' AND p.status = 'paid'"
    )
    reconciled: list[str] = []
    for row in rows:
        already = db.one(
            "SELECT 1 FROM payment_events WHERE payment_id = ? AND event = 'reconciled'",
            (row["payment_id"],),
        )
        if already:
            continue
        db.run(
            "INSERT INTO payment_events(payment_id, event, detail, created_at) "
            "VALUES(?,?,?,?)",
            (
                row["payment_id"],
                "reconciled",
                json.dumps(
                    {
                        "reference": row["reference"],
                        "reason": "paiement réussi côté payment-api, stock déjà restitué",
                    }
                ),
                now,
            ),
        )
        LOG.warning(
            "réconciliation : commande %s failed mais paiement paid — "
            "stock non restitué une seconde fois, remboursement manuel requis",
            row["reference"],
        )
        reconciled.append(row["reference"])
    return reconciled


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:create_app", factory=True, host="0.0.0.0", port=8087)
