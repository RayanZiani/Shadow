# shop-api — boutique du SI (site vitrine + back)

Réponse aux nouvelles règles du jury : le SI de paiement est un **site vitrine**
branché sur une **base de données**, avec un front qui présente les articles et
un back qui les gère. Aucune solution du commerce (Prestashop ou autre) : tout
est écrit ici.

| | |
|---|---|
| Port | **8087** |
| Base | **PostgreSQL** (`db-postgres`, base `shadow_settle`) |
| Dépend de | `payment-api` (paiement), `backoffice` (sessions admin) |
| Tests | 21, sur SQLite en mémoire (même SQL, mêmes transactions) |

---

## 1. Le modèle de données

```
products ──< cart_items >── carts
    │
    └──< order_items >── orders ──< payments ──< payment_events
```

| Table | Rôle |
|---|---|
| `products` | SKU, nom, description, prix (centimes), stock, actif |
| `carts` / `cart_items` | panier persistant, quantité bornée 1..20 |
| `orders` / `order_items` | commande + **photo du prix** au moment de l'achat |
| `payments` | montant, statut (`pending`/`paid`/`failed`), key image, taille d'anneau |
| `payment_events` | journal du paiement : création, succès, échec avec motif |

`order_items` recopie le nom et le prix : si l'administrateur change le tarif
demain, l'historique reste juste. C'est une contrainte comptable, pas un détail.

---

## 2. Le parcours d'achat

```
GET  /products                     catalogue (articles actifs)
POST /cart                         nouveau panier
POST /cart/{token}/items           ajouter (stock vérifié)
POST /checkout                     réserver le stock → payer → confirmer
GET  /orders?customer_ref=…        historique du client
GET  /orders/{ref}?customer_ref=…  détail + preuve de paiement
```

### Ce qui se passe dans `/checkout`

1. **Transaction** : `UPDATE products SET stock = stock - ? WHERE stock >= ?`
   pour chaque ligne. Le nombre de lignes affectées est vérifié : deux
   acheteurs simultanés sur le dernier article donnent une commande et un
   `409`, jamais un stock négatif. Puis création de la commande, des lignes et
   du paiement `pending`.
2. **Hors transaction** : appel de `payment-api /tx/pay` avec le montant exact.
   Le noyau émet un output de ce montant et le dépense par une signature
   d'anneau bLSAG. La réponse porte la **key image**.
3. **Succès** : paiement `paid`, commande `paid`, panier vidé.
   **Échec** : stock **restitué**, commande `failed`, motif dans
   `payment_events`, panier conservé pour réessayer.

Un paiement refusé n'est donc jamais un stock perdu, et une commande n'est
jamais `paid` sans preuve cryptographique.

---

## 3. Sécurité

| Risque | Traitement |
|---|---|
| Survente | réservation transactionnelle + `WHERE stock >= ?` |
| Prix forgé par le client | le total est calculé **côté serveur** depuis la base |
| Panier d'autrui | jeton de panier 192 bits, non énumérable |
| Historique d'autrui | `customer_ref` 256 bits, comparé à temps constant, `404` et non `403` |
| Administration | session back-office validée auprès du back-office, rôle `admin` exigé |
| Jeton de service | accepté uniquement pour les outils/tests, jamais envoyé au navigateur |
| Corps de requête | plafond 64 Ko (413) |
| Quantités / prix | bornes Pydantic (1..20, prix > 0) |
| En-têtes | `nosniff`, `X-Frame-Options: DENY`, `no-referrer`, `no-store` sur `/admin` |

---

## 4. Base : PostgreSQL en production, SQLite en test

Le SQL est écrit une fois (placeholders `?`, traduits en `%s` pour psycopg) et
les deux schémas ne diffèrent que par la génération des identifiants. Les
transactions sont réelles dans les deux cas (`BEGIN IMMEDIATE` / `conn.transaction()`).

Pourquoi ne pas imposer PostgreSQL aux tests : un test qui exige un serveur
démarré n'est pas exécutable partout, et un test qu'on n'exécute pas ne protège
rien. Le jour de la démonstration, c'est bien PostgreSQL qui sert.

```bash
# local, sans Docker
DATABASE_URL=sqlite:///./data/shop.db uvicorn main:create_app --factory --port 8087
```

---

## 5. Administration

| Route | Effet |
|---|---|
| `GET /admin/products` | tous les articles (y compris retirés) |
| `POST /admin/products` | créer (SKU unique) |
| `PATCH /admin/products/{id}` | nom, description, prix, stock, actif, image |
| `DELETE /admin/products/{id}` | **désactiver** (jamais supprimer : l'historique doit rester lisible) |
| `GET /admin/orders` | 100 dernières commandes avec leurs lignes |

L'écran correspondant est `front-dashboard/src/shop/AdminShop.jsx`, accessible
depuis « Habilitations → Boutique ».

---

## 6. Limites assumées

| Limite | Pourquoi | Où c'est dit |
|---|---|---|
| pas de compte client | le `customer_ref` est un capability token, suffisant pour un SI fictif | ici |
| pas de remboursement | hors périmètre de la démonstration ; le stock est restitué sur échec | ici |
| images produit non servies | `image_url` existe, aucun stockage de fichiers | `TODO.md` §0.5.2 |
| pas de TVA / facturation | SI fictif, montants en centimes | ici |
