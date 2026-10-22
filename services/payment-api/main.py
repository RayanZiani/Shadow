"""payment-api — le SI de paiement fictif, cœur du pilier DEV.

Appelle monero-crypto-core (Rust) pour TOUTE la cryptographie et pousse une
télémétrie d'OBSERVATIONS vers ml-detector à chaque transaction. Le détecteur
calcule lui-même les features : il n'existe qu'une seule définition de chaque
feature (services/ml-detector/features.py).

Modèle de menace supporté (c'est ce qui rend la démo réelle) :

  * `GET /outputs` expose le registre public des outputs (comme une blockchain) :
    clé publique one-time + montant CHIFFRÉ. Les montants ne sont pas lisibles
    sans la View Key.
  * `POST /tx/submit` accepte un anneau construit par le client. Un wallet
    honnête tire ses leurres via `GET /decoys` (distribution gamma, biais
    récents) ; un attaquant qui tire uniformément depuis `/outputs` produit une
    distribution plate, ce qui est exactement le signal appris par le ML.
  * Un rejeu de key image est rejeté (409) et compté dans la télémétrie.

Ce service ne prend AUCUNE décision de sécurité : il constate, il rejette ce
qui est invalide, il publie. La remédiation est déclenchée par ml-detector.

Rôle du portefeuille de démonstration
-------------------------------------
En production, les clés sont chez le client. Ici, `payment-api` joue le rôle du
wallet : il détient une View Key et une Spend Key (générées au démarrage, ou
fournies par `DEMO_VIEW_KEY_HEX` / `DEMO_SPEND_KEY_HEX` pour rejouer une
démonstration à l'identique). C'est ce qui permet de démontrer :

  * la signature d'anneau réelle (le client prouve qu'il possède un output sans
    dire lequel) ;
  * l'audit view-only : `GET /audit/last` déchiffre un montant avec la SEULE
    View Key, donc sans capacité de dépense.

Aucune clé privée n'est jamais renvoyée par un endpoint public.
"""
import asyncio
import hmac
import os
import random
import secrets
import logging
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

CRYPTO_CORE_URL = os.environ.get("CRYPTO_CORE_URL", "http://monero-crypto-core:8081")

from svc_auth import require_service_token, service_token_configured  # noqa: E402
from registry import RegistryStore  # noqa: E402

# Journal d'écriture PostgreSQL : la mémoire reste la source de lecture
# (latence du chemin critique), la base protège contre le redémarrage.
REGISTRY = RegistryStore()


def _service_headers() -> dict:
    """En-tête d'authentification service-à-service (vide si non configuré)."""
    token = os.environ.get("SERVICE_TOKEN", "").strip()
    return {"X-Service-Token": token} if token else {}
ML_DETECTOR_URL = os.environ.get("ML_DETECTOR_URL", "http://ml-detector:8083")
REMEDIATION_STATE_URL = os.environ.get(
    "REMEDIATION_STATE_URL", "http://remediation-engine:8084/state/report"
)
SELF_URL = os.environ.get("SELF_URL", "http://localhost:8080")
RING_SIZE = int(os.environ.get("RING_SIZE_DEFAULT", "11"))

# Le PIN du terminal est un SECRET DE DÉPLOIEMENT, jamais une constante du code.
# La valeur 1234 qui figurait ici était une faille volontaire : le jury l'aurait
# trouvée, et les nouvelles règles l'interdisent explicitement.
# `tools/init_env.py` génère `TERMINAL_PIN` dans `.env`. À défaut, un PIN
# aléatoire est tiré au démarrage et publié UNE FOIS dans les logs — jamais dans
# `/healthz`, qui est public.
PIN_CODE = os.environ.get("TERMINAL_PIN", "").strip()
PIN_SOURCE = "env"
if not PIN_CODE:
    PIN_CODE = f"{secrets.randbelow(9000) + 1000}"
    PIN_SOURCE = "generated"
    logging.getLogger("payment-api").warning(
        "TERMINAL_PIN non configuré : PIN aléatoire généré (%s). "
        "Renseigner TERMINAL_PIN dans .env pour un PIN stable.",
        PIN_CODE,
    )
# Nombre d'outputs émis au démarrage et seuil de réapprovisionnement.
INITIAL_OUTPUTS = int(os.environ.get("INITIAL_OUTPUTS", "500"))
MIN_UNSPENT = int(os.environ.get("MIN_UNSPENT_OUTPUTS", "250"))

# La sonde émet un output interne toutes les 4 s (~21 600/jour). On n'en garde
# que les N derniers : au-delà, le dictionnaire grossissait sans fin et
# `/metrics` ralentissait avec lui.
PROBE_OUTPUT_RETENTION = int(os.environ.get("PROBE_OUTPUT_RETENTION", "64"))


def _env_float(name: str, default: float = 0.0) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


# Limite de débit de `/tx/pay`, en requêtes/seconde et par IP.
# 0 ou absent = DÉSACTIVÉ : les mesures de charge (`tools/load_test.py`) ne
# doivent pas être faussées par un limiteur activé par défaut.
TX_PAY_RATE_LIMIT = _env_float("TX_PAY_RATE_LIMIT", 0.0)
TX_PAY_RATE_WINDOW = 1.0

# --- Portefeuille de démonstration -----------------------------------------
# `None` => généré au démarrage (aléatoire). Fixer les deux variables rejoue
# exactement la même démonstration, ce qui est utile pour le Clip Chrono.
WALLET = {
    "view_private": os.environ.get("DEMO_VIEW_KEY_HEX"),
    "spend_private": os.environ.get("DEMO_SPEND_KEY_HEX"),
    "view_public": None,
    "spend_public": None,
}
CRYPTO_STATUS = {"ready": False, "last_error": None}

# --- Registre des outputs ---------------------------------------------------
# En mémoire pour la démo ; à remplacer par PostgreSQL. Les âges suivent une
# loi log-normale : beaucoup d'outputs récents, une longue traîne d'anciens,
# comme un vrai registre.
# Chaque entrée : id, age_days, one_time_public_key_hex, tx_public_key_hex,
# encrypted_amount_hex, internal (sonde uniquement).
random.seed(1337)
OUTPUTS: dict[str, dict] = {}
# Protège OUTPUTS : les routes synchrones s'exécutent dans le threadpool
# pendant que `_mint` (boucle d'événements) le mute. Toute lecture multiple
# passe par `_snapshot_outputs()`, toute mutation par ce verrou.
_outputs_lock = threading.Lock()
_registry_seq = 0

spent_key_images: set[str] = set()

# Réservations en cours : une key image est marquée ici AVANT le premier appel
# au noyau, et libérée si la vérification échoue. Sans cela, deux requêtes
# concurrentes passaient toutes les deux le test de double dépense.
_key_image_lock = asyncio.Lock()
_pending_key_images: set[str] = set()
# Outputs réellement dépensés. On suit l'IDENTIFIANT en plus de la key image :
# la key image est produite par le noyau cryptographique et sa dérivation peut
# changer (placeholder -> bLSAG réel). Filtrer `/outputs?unspent=true` sur une
# dérivation recopiée ici donnait un pool faux dès que le noyau Rust réel était
# utilisé : le client retombait en boucle sur des outputs déjà dépensés et
# recevait des 409 en série. Constaté en recette sur `docker compose`.
spent_output_ids: set[str] = set()
auto_probe_status = {"last_run": None, "ok": True}
LAST_TX: dict | None = None

# Outputs internes de la sonde encore présents (bornés à PROBE_OUTPUT_RETENTION).
_probe_outputs: deque[str] = deque()

# Idempotence de `/tx/pay` : référence -> résultat déjà émis. La mémoire reste
# la source de lecture ; PostgreSQL (table `registry_payments`) protège du redémarrage.
PAYMENTS: dict[str, dict] = {}
# Borne mémoire : au-delà, les plus anciennes références sont oubliées et un
# rejeu est résolu par le journal PostgreSQL (`load_payment`).
PAYMENTS_MEMORY_MAX = int(os.environ.get("PAYMENTS_MEMORY_MAX", "10000"))
_payment_order: deque[str] = deque()
_payment_locks: dict[str, asyncio.Lock] = {}
_payment_locks_guard = asyncio.Lock()

# Limiteur de débit de `/tx/pay` : fenêtre glissante d'une seconde par IP.
_tx_pay_hits: dict[str, deque[float]] = {}
_tx_pay_hits_lock = threading.Lock()


def _snapshot_outputs() -> list[dict]:
    """Copie atomique des outputs — SEULE façon d'itérer le registre.

    `/metrics`, `/decoys` et `/outputs` s'exécutent dans le threadpool pendant
    que `_mint` mute le dictionnaire depuis la boucle : itérer directement
    pouvait lever `RuntimeError: dictionary changed size during iteration`
    (scrape Prometheus + sonde toutes les 4 s). On copie sous verrou, puis on
    itère la copie.
    """
    with _outputs_lock:
        return list(OUTPUTS.values())


def _count_unspent() -> int:
    """Outputs publics (hors sonde) encore dépensables."""
    return sum(
        1
        for output in _snapshot_outputs()
        if output["id"] not in spent_output_ids and not output.get("internal")
    )

# --- Compteurs d'observations ----------------------------------------------
pin_attempts: deque[float] = deque(maxlen=500)
pin_failures: deque[float] = deque(maxlen=500)
# `/pin/stats` lit ces deques depuis le threadpool pendant que `verify_pin` les
# mute : sans verrou, l'itération peut lever `deque mutated during iteration`.
_pin_lock = threading.Lock()
PIN_WINDOW_SECONDS = 30.0


# ------------------------------------------------------------------ crypto
async def _crypto(path: str, payload: dict, timeout: float = 5.0) -> dict:
    """Appel au noyau Rust. Toute panne est explicite : jamais de silence."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{CRYPTO_CORE_URL}{path}", json=payload)
    except httpx.HTTPError as exc:
        # Le noyau ne répond pas : on le SAIT maintenant. Sans cette ligne,
        # `/healthz` continuait d'annoncer `crypto_core: ready` jusqu'au
        # prochain passage de la sonde (4 s), et `tools/chaos.py` lisait un
        # état périmé juste après l'arrêt du noyau.
        CRYPTO_STATUS["ready"] = False
        CRYPTO_STATUS["last_error"] = f"{type(exc).__name__}: {exc}"
        raise HTTPException(status_code=503, detail=f"noyau crypto injoignable ({path})") from exc
    # Le noyau a répondu (même par une erreur) : il est joignable.
    CRYPTO_STATUS["ready"] = True
    if response.status_code >= 400:
        CRYPTO_STATUS["last_error"] = f"{path} -> {response.status_code}: {response.text[:200]}"
        # Une 4xx du noyau est une faute du CLIENT (anneau malformé, clé
        # invalide) : la propager en 400, pas en 502. Une 5xx est une panne du
        # noyau, donc une passerelle en erreur.
        status = 400 if response.status_code < 500 else 502
        raise HTTPException(status_code=status, detail=f"noyau crypto: {response.text[:200]}")
    CRYPTO_STATUS["last_error"] = None
    return response.json()


async def _bootstrap_wallet() -> None:
    """Génère (ou recharge) la paire de clés et publie les clés publiques.

    Ordre de résolution, du plus stable au plus volatil :

    1. `DEMO_VIEW_KEY_HEX` / `DEMO_SPEND_KEY_HEX` (rejeu exact de la démo) ;
    2. le portefeuille persisté dans PostgreSQL (survit au redémarrage) ;
    3. des clés aléatoires (premier démarrage, ou registre en mémoire).

    Le point 2 est ce qui manquait : sans lui, un redémarrage tirait de
    nouvelles clés alors que les outputs restaurés appartenaient aux
    anciennes, et `/tx/submit` répondait 409 une fois sur deux.
    """
    if not WALLET["view_private"] or not WALLET["spend_private"]:
        stored = REGISTRY.load_wallet()
        if stored is not None:
            WALLET["view_private"], WALLET["spend_private"] = stored
            logging.getLogger("payment-api").info(
                "portefeuille rechargé depuis PostgreSQL (les outputs "
                "restaurés restent dépensables)"
            )

    WALLET["view_private"] = WALLET["view_private"] or secrets.token_hex(32)
    WALLET["spend_private"] = WALLET["spend_private"] or secrets.token_hex(32)
    REGISTRY.save_wallet(
        WALLET["view_private"], WALLET["spend_private"], time.time()
    )
    keys = await _crypto(
        "/crypto/keys",
        {
            "private_view_key_hex": WALLET["view_private"],
            "private_spend_key_hex": WALLET["spend_private"],
        },
    )
    WALLET["view_public"] = keys["public_view_key_hex"]
    WALLET["spend_public"] = keys["public_spend_key_hex"]


async def _mint(count: int, internal: bool = False, amount: int | None = None) -> list[str]:
    """Émet `count` outputs chiffrés. Le registre ne voit QUE le chiffré.

    `amount` impose le montant (paiement d'une commande) ; sans lui, le noyau
    tire un montant varié, ce qui garde un registre réaliste.
    """
    global _registry_seq
    if not WALLET["view_public"]:
        return []
    payload = {
        "count": count,
        "public_spend_key_hex": WALLET["spend_public"],
        "public_view_key_hex": WALLET["view_public"],
    }
    if amount is not None:
        payload["amount"] = amount
    minted = await _crypto("/crypto/mint-outputs", payload)
    created: list[str] = []
    new_outputs: dict[str, dict] = {}
    for item in minted["outputs"]:
        _registry_seq += 1
        output = {
            "id": f"output_{_registry_seq}",
            "age_days": 0.01 if internal else round(random.lognormvariate(3.2, 1.1), 3),
            **item,
        }
        if internal:
            output["internal"] = True
        output["created_at"] = time.time()
        new_outputs[output["id"]] = output
        created.append(output["id"])
    # Mutation sous verrou : un snapshot concurrent ne voit jamais un état
    # à moitié écrit.
    with _outputs_lock:
        OUTPUTS.update(new_outputs)
    # Écriture groupée : un seul aller-retour pour N outputs. Si la base est
    # tombée, `save_outputs` dégrade et `/healthz` le dit — on ne perd jamais
    # un paiement à cause du journal.
    REGISTRY.save_outputs([new_outputs[output_id] for output_id in created])
    return created


async def _replenish_pool() -> None:
    """Réapprovisionne le registre, avec la MÊME loi d'âge.

    Sans cela, deux passages de `e2e_demo.py` épuisaient les 500 outputs et les
    clients recevaient des 409 en série — ce qui ressemble à une attaque
    permanente. Constaté en recette.
    """
    unspent = _count_unspent()
    while unspent < MIN_UNSPENT:
        created = await _mint(200)
        if not created:
            # Portefeuille non prêt : sans cette garde, la boucle serait infinie.
            return
        unspent += len(created)


# Un seul réapprovisionnement à la fois : deux `/decoys` concurrents doublaient
# sinon l'émission (et la charge sur le noyau).
_pool_lock = asyncio.Lock()


async def _ensure_pool() -> None:
    """Réapprovisionne SEULEMENT si le pool utile est sous le seuil.

    `quarantine_outputs` retire 25 outputs par incident. `_replenish_pool`
    n'était appelé que par `/outputs?unspent=true` : après une vingtaine
    d'incidents, `/decoys` ne trouvait plus de leurres et `/tx/pay` répondait
    502 en boucle. On répare donc depuis les chemins qui en ont besoin.
    """
    if _count_unspent() >= MIN_UNSPENT:
        return
    async with _pool_lock:
        # Re-test sous verrou : un autre appel a pu réapprovisionner entre-temps.
        if _count_unspent() >= MIN_UNSPENT:
            return
        await _replenish_pool()


def _restore_registry() -> None:
    """Recharge le registre depuis PostgreSQL. Appelé une fois au démarrage.

    Sans cela, `docker compose restart payment-api` repartait d'un registre
    vide : la transaction de preuve disparaissait et les clients recevaient des
    409 en série. C'était le risque de démonstration n°1.
    """
    global _registry_seq
    outputs, key_images, spent_ids, highest = REGISTRY.load()
    # Chaque partie est restaurée indépendamment : une base dont tous les
    # outputs ont été mis en quarantaine doit quand même conserver ses key
    # images, sinon la protection anti-double-dépense repart en arrière.
    with _outputs_lock:
        OUTPUTS.update(outputs)
    spent_key_images.update(key_images)
    spent_output_ids.update(spent_ids)
    _registry_seq = max(_registry_seq, highest)
    # Les outputs internes restaurés comptent dans la borne de la sonde :
    # sinon chaque redémarrage en rajoutait jusqu'à PROBE_OUTPUT_RETENTION.
    # Les excédents laissés par les versions précédentes sont purgés tout de
    # suite (mémoire + journal), sans attendre 21 600 cycles de sonde.
    internes = [output["id"] for output in outputs.values() if output.get("internal")]
    internes.sort(key=_probe_order)
    keep = max(PROBE_OUTPUT_RETENTION, 1)
    _probe_outputs.clear()
    _probe_outputs.extend(internes[-keep:])
    _forget_probe_outputs(internes[:-keep])
    if not outputs and not key_images:
        return
    logging.getLogger("payment-api").info(
        "registre rechargé : %d outputs, %d key images, %d dépensés",
        len(outputs),
        len(key_images),
        len(spent_ids),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Recharger ce qui existe déjà (redémarrage) — AVANT d'émettre, sinon
    #    `_registry_seq` repart de zéro et écrase les identifiants existants.
    # 1. Restaurer les outputs ET les key images.
    _restore_registry()
    try:
        # 2. Recharger le portefeuille persistant (ou en créer un). Cet ordre
        #    est critique : les outputs restaurés doivent être dépensables par
        #    les clés qu'on va utiliser.
        await _bootstrap_wallet()
        await _mint(INITIAL_OUTPUTS)
        CRYPTO_STATUS["ready"] = True
    except HTTPException as exc:
        # On ne masque pas : /healthz le dira, et e2e_demo.py s'arrêtera.
        CRYPTO_STATUS["ready"] = False
        CRYPTO_STATUS["last_error"] = str(exc.detail)
    task = asyncio.create_task(_auto_probe_loop())
    yield
    task.cancel()


app = FastAPI(title="payment-api", lifespan=lifespan)

# Le dashboard interroge /tx/auto-probe/status et /pin/stats depuis le port 5173.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000",
    ).split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- découverte
@app.get("/outputs")
async def list_outputs(limit: int = 500, unspent: bool = False):
    """Registre public : un attaquant peut le lire, c'est le principe d'une
    blockchain. C'est ce qui lui permet de construire un anneau « plat ».

    `unspent=true` : ce qu'un wallet honnête utiliserait pour choisir un output
    réel. Sans ce filtre, tout client retombe sur une key image déjà dépensée
    et déclenche l'anti-double-dépense (faux positif d'attaque).
    """
    outputs = _snapshot_outputs()
    if unspent:
        await _ensure_pool()
        outputs = [
            output
            for output in _snapshot_outputs()
            if output["id"] not in spent_output_ids
        ]
    # Les outputs internes (sonde) ne sont pas des leurres candidats : les
    # exposer laisserait un attaquant reconstruire un anneau entièrement
    # composé de nos propres sondes.
    public = [o for o in outputs if not o.get("internal")]
    return {"count": len(public), "outputs": public[:limit]}


@app.get("/wallet/public")
def wallet_public():
    """Clés PUBLIQUES du portefeuille de démonstration (jamais les privées)."""
    return {
        "public_view_key_hex": WALLET["view_public"],
        "public_spend_key_hex": WALLET["spend_public"],
        "note": "clés publiques uniquement ; les clés privées ne quittent pas le service",
    }


def _key_image_of(output_id: str) -> str:
    """Dérivation historique (SHA-256) — conservée pour les tests et le diagnostic.

    La vraie key image est calculée par le noyau Rust (I = x·H_p(P)) et n'est
    plus recopiée ici : c'est ce qui garantit l'anti-double-dépense.
    """
    import hashlib

    return hashlib.sha256(f"ki::{output_id}".encode()).hexdigest()


def _gamma_weights(ages: list[float], shape: float = 2.0, scale: float = 1.6) -> list[float]:
    """Densité gamma sur l'âge : favorise les outputs récents, comme Monero.

    Remplace le `random.sample` uniforme du squelette. C'est un TODO explicite
    du README et une des features que le ML surveille.
    """
    weights = []
    for age in ages:
        # Gamma(k=2) : x * exp(-x/scale), normalisée ensuite.
        x = age / scale
        weights.append(max(x * pow(2.718281828, -x), 1e-12))
    return weights


@app.get("/decoys")
async def get_decoys(count: int | None = None):
    """Tire les leurres selon une gamma sur l'âge (biais récents, comme Monero).

    Les outputs INTERNES (sonde de liveness) sont exclus : ils ont un âge
    artificiel de 0,01 j et, comme la gamma favorise massivement les récents,
    ils finissaient par représenter l'essentiel du pool. Conséquence observée
    en recette : l'entropie et l'asymétrie dérivaient au fil du temps, et le
    modèle entraîné une heure plus tôt ne correspondait plus au trafic.

    Le pool est réapprovisionné ici s'il est passé sous le seuil : c'est le
    chemin qu'un wallet honnête emprunte à chaque transaction, donc celui qui
    doit constater une pénurie de leurres.
    """
    if count is None:
        count = RING_SIZE - 1
    await _ensure_pool()
    candidates = [o for o in _snapshot_outputs() if not o.get("internal")]
    count = max(0, min(count, len(candidates) - 1))
    if count == 0:
        return {"decoys": [], "ages_days": []}

    ids = [o["id"] for o in candidates]
    weights = _gamma_weights([o["age_days"] for o in candidates])
    picked = random.choices(ids, weights=weights, k=min(max(count * 4, count), len(ids)))
    # Dédoublonnage en gardant l'ordre de tirage.
    seen, decoys = set(), []
    for output_id in picked:
        if output_id in seen:
            continue
        seen.add(output_id)
        decoys.append(output_id)
        if len(decoys) == count:
            break
    # Filet de sécurité : si la gamma est trop concentrée pour fournir `count`
    # ids distincts, on complète avec des outputs non encore retenus.
    if len(decoys) < count:
        for output_id in ids:
            if output_id not in seen:
                decoys.append(output_id)
                if len(decoys) == count:
                    break
    # Les âges viennent du snapshot : un output peut avoir été mis en
    # quarantaine entre-temps, on ne relit pas OUTPUTS (KeyError possible).
    ages = {o["id"]: o["age_days"] for o in candidates}
    return {"decoys": decoys, "ages_days": [ages[i] for i in decoys]}


# ------------------------------------------------------------------- paiement
class TxSubmitRequest(BaseModel):
    real_output_id: str
    # Conservé pour la compatibilité des scripts : le montant qui fait foi est
    # celui que le portefeuille déchiffre, pas celui annoncé par le client.
    amount: int = 0
    # Ignoré : le portefeuille de démonstration signe avec SA clé. Un vrai
    # client fournirait la sienne, dérivée de sa View Key.
    spend_private_key_hex: str | None = None
    # Anneau construit par le client. None => le serveur tire selon la gamma.
    # `max_length=64` : la borne est appliquée par Pydantic AVANT le handler,
    # donc avant toute recherche dans le registre. Un corps de 65 leurres est
    # refusé en 422 sans coût (la vérification d'anneau est linéaire en N).
    # Chaque identifiant est aussi borné : un id de 10 Mo coûterait un hachage
    # de 10 Mo même sans lookup.
    decoy_ids: list[Annotated[str, Field(max_length=128)]] | None = Field(
        default=None, max_length=64
    )
    # Marque le trafic de la sonde de liveness (exclu du ML).
    synthetic: bool = False


def _ring_public_keys(real: dict, decoy_ids: list[str]) -> list[str]:
    ring = [real["one_time_public_key_hex"]]
    for decoy_id in decoy_ids:
        decoy = OUTPUTS.get(decoy_id)
        if decoy is None or "one_time_public_key_hex" not in decoy:
            raise HTTPException(
                status_code=400, detail=f"leurres sans clé publique: {decoy_id}"
            )
        ring.append(decoy["one_time_public_key_hex"])
    return ring


@app.post("/tx/submit")
async def submit_tx(req: TxSubmitRequest):
    global LAST_TX
    started = time.perf_counter()
    real = OUTPUTS.get(req.real_output_id)
    if real is None:
        raise HTTPException(status_code=400, detail="output inconnu")
    if "one_time_public_key_hex" not in real:
        raise HTTPException(status_code=400, detail="output sans clé publique (registre dégradé)")

    if req.decoy_ids is not None:
        # Ceinture et bretelles : Pydantic a déjà refusé > 64 en 422. Cette
        # garde couvre un appel interne qui ne passerait pas par la validation.
        # Elle est AVANT la recherche des inconnus : 65 identifiants inconnus
        # ne doivent pas coûter 65 lookups.
        if len(req.decoy_ids) > 64:
            raise HTTPException(
                status_code=400,
                detail=f"anneau trop grand ({len(req.decoy_ids)} > 64)",
            )
        unknown = [i for i in req.decoy_ids if i not in OUTPUTS]
        if unknown:
            raise HTTPException(status_code=400, detail=f"leurres inconnus: {unknown[:3]}")
        decoy_ids = req.decoy_ids
    else:
        decoy_ids = (await get_decoys(RING_SIZE - 1))["decoys"]

    ring = _ring_public_keys(real, decoy_ids)
    if len(ring) < 2:
        # Un anneau d'un seul élément ne prouve rien : le client doit fournir
        # au moins un leurre. Refus explicite plutôt qu'un 502 du noyau.
        raise HTTPException(status_code=400, detail="anneau trop petit (au moins 2 clés)")

    # 1. Le portefeuille dérive la clé privée one-time de l'output dépensé.
    derived = await _crypto(
        "/crypto/derive-one-time-key",
        {
            "tx_public_key_hex": real["tx_public_key_hex"],
            "private_view_key_hex": WALLET["view_private"],
            "private_spend_key_hex": WALLET["spend_private"],
        },
    )
    if derived["one_time_public_key_hex"] != real["one_time_public_key_hex"]:
        # L'output annoncé par le registre n'est pas celui que nos clés ouvrent.
        raise HTTPException(status_code=409, detail="output non dérivable par ce portefeuille")

    # 2. Montant réel : déchiffré avec la View Key, jamais celui du client.
    audited = await _crypto(
        "/crypto/audit-view",
        {
            "tx_public_key_hex": real["tx_public_key_hex"],
            "private_view_key_hex": WALLET["view_private"],
            "encrypted_amount_hex": real["encrypted_amount_hex"],
        },
    )
    amount = int(audited["decrypted_amount"])
    if req.amount not in (0, amount):
        raise HTTPException(status_code=400, detail="montant annoncé incohérent")

    # 3. Engagements de Pedersen : 1 entrée -> 2 sorties dont les aveugles
    #    somment à celui de l'entrée. L'équilibre est vérifié plus bas.
    blind = secrets.token_hex(32)
    commitment_in = (
        await _crypto("/crypto/commit", {"amount": amount, "blind_hex": blind})
    )["commitment_hex"]
    split = await _crypto(
        "/crypto/commit-split", {"amount": amount, "blind_hex": blind, "parts": 2}
    )
    commitments_out = [part["commitment_hex"] for part in split["parts"]]

    # 4. Signature d'anneau liée, sur un message qui ENGAGE les engagements.
    message = f"tx|{req.real_output_id}|{commitment_in}|{'|'.join(commitments_out)}"
    signed = await _crypto(
        "/crypto/ring-sign",
        {
            "ring": ring,
            "signer_index": 0,
            "one_time_private_key_hex": derived["one_time_private_key_hex"],
            "message": message,
            "amount": amount,
            "blind_hex": blind,
        },
    )
    if signed["pedersen_commitment_hex"] != commitment_in:
        raise HTTPException(status_code=500, detail="engagement incohérent avec la signature")

    key_image = signed["key_image_hex"]

    # Réservation ATOMIQUE, avant tout `await` : c'est ce qui ferme la course.
    # Deux requêtes simultanées avec la même key image : une seule réserve.
    async with _key_image_lock:
        if key_image in spent_key_images or key_image in _pending_key_images:
            duplicate = True
        elif req.real_output_id in spent_output_ids:
            # L'output lui-même a déjà été dépensé : même si la key image a
            # changé de dérivation, il n'y a rien à dépenser deux fois.
            duplicate = True
        else:
            duplicate = False
            _pending_key_images.add(key_image)

    if duplicate:
        # Double dépense : on rejette MAIS on publie la télémétrie, sinon
        # l'attaque serait invisible pour le détecteur.
        await _send_telemetry(
            _observation(
                cpu_us=0.0,
                ring_size=len(ring),
                ages=[],
                rejected=True,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        )
        raise HTTPException(status_code=409, detail="double dépense détectée")

    # 5. Vérification cryptographique réelle.
    verified = await _crypto(
        "/crypto/ring-verify",
        {
            "ring": ring,
            "message": message,
            "ring_signature_hex": signed["ring_signature_hex"],
            "key_image_hex": signed["key_image_hex"],
            "pedersen_commitment_hex": signed["pedersen_commitment_hex"],
        },
    )
    if not verified["valid"]:
        # La réservation est libérée : une signature invalide n'est pas une
        # double dépense, le client doit pouvoir corriger et réessayer.
        _pending_key_images.discard(key_image)
        raise HTTPException(status_code=400, detail=f"anneau invalide: {verified.get('reason')}")

    # 6. Équilibre des engagements : ∑C_in - ∑C_out = 0.
    balance = await _crypto(
        "/crypto/commitment-balance",
        {"inputs": [commitment_in], "outputs": commitments_out},
    )
    if not balance["balanced"]:
        _pending_key_images.discard(key_image)
        raise HTTPException(status_code=400, detail="engagements non équilibrés")

    async with _key_image_lock:
        _pending_key_images.discard(key_image)
        spent_key_images.add(key_image)
        spent_output_ids.add(req.real_output_id)
    REGISTRY.save_spent(req.real_output_id, key_image, time.time())

    latency_ms = (time.perf_counter() - started) * 1000
    # Les âges sont relus sur un snapshot : un leurre peut avoir été mis en
    # quarantaine entre la validation et la télémétrie.
    ages_by_id = {o["id"]: o["age_days"] for o in _snapshot_outputs()}
    ages = [ages_by_id.get(i, 0.0) for i in decoy_ids]

    # Télémétrie : ne bloque jamais la réponse utilisateur.
    asyncio.create_task(
        _send_telemetry(
            _observation(
                cpu_us=verified["verify_cpu_microseconds"],
                ring_size=len(ring),
                ages=ages,
                rejected=False,
                latency_ms=latency_ms,
                synthetic=req.synthetic,
            )
        )
    )

    LAST_TX = {
        "output_id": req.real_output_id,
        "tx_public_key_hex": real["tx_public_key_hex"],
        "encrypted_amount_hex": real["encrypted_amount_hex"],
        "key_image_hex": signed["key_image_hex"],
        "ring_size": len(ring),
        "ts": time.time(),
    }

    # Miroir d'état : une tx confirmée est un fait vérifié, rediffusé au
    # dashboard ET au terminal. Ne déclenche jamais de remédiation.
    asyncio.create_task(
        _report_success_tx({"key_image": signed["key_image_hex"], "amount": amount})
    )

    return {
        "status": "confirmed",
        "key_image": signed["key_image_hex"],
        "ring_size": len(ring),
        "amount": amount,
        "verify_cpu_us": verified["verify_cpu_microseconds"],
        "signature_scheme": signed.get("scheme", "bLSAG"),
        "balance_ok": balance["balanced"],
        "latency_ms": round(latency_ms, 2),
    }


# ------------------------------------------------------- paiement d'une commande
class PayRequest(BaseModel):
    amount: int
    # Longueur bornée : une référence de 1 Mo grossirait la mémoire et le
    # journal sans rien apporter.
    reference: str = Field(default="", max_length=128)


def _rate_limit_tx_pay(client_ip: str) -> None:
    """Fenêtre glissante simple : N requêtes/s par IP, puis 429 + Retry-After.

    Le WAF en amont ne protège PAS un accès direct au port publié : la
    docstring historique le prétendait, c'était faux. Désactivé quand
    `TX_PAY_RATE_LIMIT` vaut 0 (défaut), pour ne pas fausser les mesures de
    charge.
    """
    limit = TX_PAY_RATE_LIMIT
    if limit <= 0:
        return
    now = time.monotonic()
    with _tx_pay_hits_lock:
        if len(_tx_pay_hits) > 4096:
            # Borne la mémoire : on purge les IP dont la fenêtre est finie.
            stale = [
                ip
                for ip, hits in _tx_pay_hits.items()
                if not hits or now - hits[-1] >= TX_PAY_RATE_WINDOW
            ]
            for ip in stale:
                del _tx_pay_hits[ip]
        hits = _tx_pay_hits.setdefault(client_ip, deque())
        while hits and now - hits[0] >= TX_PAY_RATE_WINDOW:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, int(TX_PAY_RATE_WINDOW - (now - hits[0])) + 1)
            raise HTTPException(
                status_code=429,
                detail="trop de paiements : réessayer dans un instant",
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)


async def _payment_lock(reference: str) -> asyncio.Lock:
    """Verrou par référence : deux rejeux simultanés ne paient qu'une fois.

    Un verrou global sérialiserait TOUS les paiements (mesures de charge) ;
    un verrou par référence ne bloque que les doublons.
    """
    async with _payment_locks_guard:
        return _payment_locks.setdefault(reference, asyncio.Lock())


def _remember_payment(reference: str, record: dict) -> None:
    """Mémorise le résultat et borne la mémoire (le journal reste la vérité)."""
    nouveau = reference not in PAYMENTS
    PAYMENTS[reference] = record
    if nouveau:
        _payment_order.append(reference)
    while len(_payment_order) > max(PAYMENTS_MEMORY_MAX, 1):
        ancienne = _payment_order.popleft()
        if ancienne == reference:
            # Ne jamais oublier la référence qu'on vient d'insérer.
            _payment_order.append(ancienne)
            break
        PAYMENTS.pop(ancienne, None)
        verrou = _payment_locks.get(ancienne)
        if verrou is not None and not verrou.locked():
            del _payment_locks[ancienne]


def _forget_payments() -> None:
    """Vide le cache mémoire (tests et remise à zéro)."""
    PAYMENTS.clear()
    _payment_order.clear()
    _payment_locks.clear()


def _known_payment(reference: str) -> dict | None:
    """Résultat déjà émis pour cette référence (mémoire, puis PostgreSQL)."""
    record = PAYMENTS.get(reference)
    if record is not None:
        return record
    record = REGISTRY.load_payment(reference)
    if record is not None:
        _remember_payment(reference, record)
    return record


def _payment_response(record: dict, reference: str, replayed: bool) -> dict:
    return {
        "status": "paid",
        "reference": reference,
        "amount": record["amount"],
        "key_image": record["key_image"],
        "ring_size": record["ring_size"],
        "balance_ok": record.get("balance_ok", True),
        "signature_scheme": record.get("signature_scheme", "bLSAG"),
        "created_at": record["created_at"],
        "replayed": replayed,
    }


async def _submit_payment(output_id: str, amount: int) -> dict:
    """Dépense l'output émis via le chemin normal `/tx/submit`."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{SELF_URL}/tx/submit",
            json={"real_output_id": output_id, "amount": amount},
        )
    if response.status_code != 200:
        detail = response.json().get("detail") if response.content else response.status_code
        raise HTTPException(status_code=502, detail=f"paiement refusé par le registre: {detail}")
    return response.json()


async def _pay_once(amount: int) -> dict:
    await _ensure_pool()
    created = await _mint(1, internal=True, amount=amount)
    if not created:
        raise HTTPException(status_code=503, detail="registre indisponible")
    body = await _submit_payment(created[-1], amount)
    return {
        "amount": body["amount"],
        "key_image": body["key_image"],
        "ring_size": body["ring_size"],
        "balance_ok": body["balance_ok"],
        "signature_scheme": body["signature_scheme"],
        "created_at": time.time(),
    }


@app.post("/tx/pay")
async def pay_order(req: PayRequest, request: Request):
    """Paiement d'une commande pour un montant EXACT, idempotent par `reference`.

    Pourquoi un endpoint dédié : les outputs du registre portent des montants
    variés (1..500) qui rendent l'audit intéressant, mais un article coûte un
    prix précis. On émet donc un output interne du montant exact, puis on le
    dépense par le chemin normal (`/tx/submit`) : engagement de Pedersen,
    signature d'anneau bLSAG, key image anti-double-dépense. Le paiement n'est
    pas simulé — il produit une vraie preuve cryptographique.

    `reference` est la clé d'idempotence : un rejeu (timeout HTTP côté
    boutique, retry réseau) renvoie le MÊME résultat avec `replayed: true` au
    lieu d'émettre un second paiement. Une référence réutilisée avec un autre
    montant est refusée en 409.

    Ce endpoint n'est pas protégé par le jeton de service : c'est le chemin
    client, celui qu'emprunte la boutique. Il est borné (montant, anneau) et
    limité en débit par IP si `TX_PAY_RATE_LIMIT` est configuré (désactivé par
    défaut ; le WAF en amont ne protège pas un accès direct au port).
    """
    _rate_limit_tx_pay(request.client.host if request.client else "inconnu")

    if not 1 <= req.amount <= 1_000_000:
        raise HTTPException(status_code=400, detail="montant hors bornes (1..1 000 000)")

    reference = req.reference.strip()
    if not reference:
        # Sans référence, pas d'idempotence possible : comportement historique.
        return _payment_response(await _pay_once(req.amount), "", replayed=False)

    async with await _payment_lock(reference):
        known = _known_payment(reference)
        if known is not None:
            if known["amount"] != req.amount:
                raise HTTPException(
                    status_code=409,
                    detail="référence déjà utilisée avec un autre montant",
                )
            return _payment_response(known, reference, replayed=True)

        record = await _pay_once(req.amount)
        # Mémoire d'abord (source de lecture), journal ensuite : le rejeu reste
        # instantané, et PostgreSQL protège du redémarrage.
        _remember_payment(reference, record)
        REGISTRY.save_payment(
            reference,
            record["key_image"],
            record["ring_size"],
            record["amount"],
            record["created_at"],
        )
        return _payment_response(record, reference, replayed=False)


def _observation(
    cpu_us: float,
    ring_size: int,
    ages: list[float],
    rejected: bool,
    latency_ms: float,
    pin_failure: bool = False,
    synthetic: bool = False,
) -> dict:
    """Fait brut envoyé au détecteur. Pas de feature calculée ici : une seule
    définition, dans services/ml-detector/features.py."""
    return {
        "timestamp": time.time(),
        "ring_verify_cpu_us": cpu_us,
        "ring_size": ring_size,
        "decoy_ages_days": ages,
        "key_image_rejected": rejected,
        "pin_failure": pin_failure,
        "tx_latency_ms": latency_ms,
        "synthetic": synthetic,
    }


# Compteurs de télémétrie perdue. Un non-2xx (401/503 parce que le jeton de
# service manque, par exemple) n'est PAS une exception `httpx` : sans ce
# contrôle, le détecteur ne recevait plus rien et personne ne le savait.
TELEMETRY = {"sent": 0, "dropped": 0, "last_error": None}


async def _send_telemetry(observation: dict):
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.post(
                f"{ML_DETECTOR_URL}/ingest", json=observation, headers=_service_headers()
            )
        if response.status_code >= 400:
            TELEMETRY["dropped"] += 1
            TELEMETRY["last_error"] = f"HTTP {response.status_code}"
        else:
            TELEMETRY["sent"] += 1
    except httpx.HTTPError as exc:
        # Le ML ne doit jamais faire planter un paiement légitime, mais la
        # perte doit être comptée et visible.
        TELEMETRY["dropped"] += 1
        TELEMETRY["last_error"] = type(exc).__name__


async def _report_success_tx(detail: dict):
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.post(
                REMEDIATION_STATE_URL,
                json={"state": "NORMAL_TX", "detail": detail},
                headers=_service_headers(),
            )
        if response.status_code >= 400:
            TELEMETRY["last_error"] = f"state/report HTTP {response.status_code}"
    except httpx.HTTPError as exc:
        TELEMETRY["last_error"] = type(exc).__name__


# ---------------------------------------------------------------------- PIN
class PinRequest(BaseModel):
    pin: str
    amount: int = 1


# Anti-bruteforce du terminal : 5 échecs consécutifs → 60 s de blocage.
# Un PIN à 4 chiffres n'a que 10 000 combinaisons ; sans blocage, il tombe en
# quelques minutes. C'est une vraie protection, pas une décoration.
PIN_LOCKOUT_THRESHOLD = 5
PIN_LOCKOUT_SECONDS = 60.0
_pin_state: dict = {"failures": 0, "locked_until": 0.0}


@app.post("/pin/verify")
async def verify_pin(req: PinRequest):
    """Saisie au terminal physique (ou pavé USB, cf. hardware/README.md).

    Ne déclenche AUCUNE remédiation et ne contourne pas la vérification
    cryptographique : un PIN correct autorise seulement une tx de démo, qui
    passe ensuite par /tx/submit comme n'importe quel client.
    """
    now = time.time()
    if _pin_state["locked_until"] > now:
        retry = int(_pin_state["locked_until"] - now) + 1
        raise HTTPException(
            status_code=429,
            detail="terminal bloqué après plusieurs codes refusés",
            headers={"Retry-After": str(retry)},
        )

    with _pin_lock:
        pin_attempts.append(now)
    # `PIN_CODE` est un secret de configuration, jamais une constante du code :
    # la valeur 1234 qui figurait ici était une faille volontaire.
    #
    # `.encode()` : sur des `str`, `compare_digest` lève TypeError dès qu'un
    # caractère est non-ASCII. Un PIN `"é"` provoquait un 500 SANS incrémenter
    # le compteur d'échecs ni le verrouillage — un angle mort.
    ok = hmac.compare_digest(req.pin.encode(), PIN_CODE.encode())
    if not ok:
        with _pin_lock:
            pin_failures.append(now)
            _pin_state["failures"] += 1
        if _pin_state["failures"] >= PIN_LOCKOUT_THRESHOLD:
            _pin_state["locked_until"] = now + PIN_LOCKOUT_SECONDS
            _pin_state["failures"] = 0
    else:
        _pin_state["failures"] = 0

    asyncio.create_task(
        _send_telemetry(
            _observation(
                cpu_us=0.0,
                ring_size=0,
                ages=[],
                rejected=False,
                latency_ms=0.0,
                pin_failure=not ok,
            )
        )
    )

    if not ok:
        raise HTTPException(status_code=401, detail="code refusé")
    return {"status": "pin_ok", "amount": req.amount}


@app.get("/pin/stats")
def pin_stats():
    now = time.time()
    # Copie sous verrou : l'itération ne peut pas croiser un `append` du
    # chemin `/pin/verify`.
    with _pin_lock:
        attempts = sum(1 for t in pin_attempts if now - t <= PIN_WINDOW_SECONDS)
        failures = sum(1 for t in pin_failures if now - t <= PIN_WINDOW_SECONDS)
    return {
        "window_seconds": PIN_WINDOW_SECONDS,
        "attempts": attempts,
        "failures": failures,
        "failure_ratio": round(failures / attempts, 4) if attempts else 0.0,
    }


# ------------------------------------------------------------------- audit
@app.get("/audit/last")
async def audit_last():
    """Déchiffre le montant de la dernière transaction avec la SEULE View Key.

    C'est la démonstration de gouvernance : un auditeur qui ne détient pas la
    Spend Key lit les montants mais ne peut pas signer. Le déchiffrement est
    refait à chaque appel (aucun montant en clair n'est stocké).
    """
    if LAST_TX is None:
        return {"available": False, "reason": "aucune transaction confirmée"}
    decrypted = await _crypto(
        "/crypto/audit-view",
        {
            "tx_public_key_hex": LAST_TX["tx_public_key_hex"],
            "private_view_key_hex": WALLET["view_private"],
            "encrypted_amount_hex": LAST_TX["encrypted_amount_hex"],
        },
    )
    return {
        "available": True,
        "output_id": LAST_TX["output_id"],
        "amount": decrypted["decrypted_amount"],
        "key_image": LAST_TX["key_image_hex"],
        "ring_size": LAST_TX["ring_size"],
        "capability": "view-only (aucune capacité de signature)",
        "ts": LAST_TX["ts"],
    }


@app.get("/audit/outputs/{output_id}")
async def audit_output(output_id: str):
    """Scan view-only : cet output est-il au portefeuille, et pour quel montant ?"""
    output = OUTPUTS.get(output_id)
    if output is None:
        raise HTTPException(status_code=404, detail="output inconnu")
    decrypted = await _crypto(
        "/crypto/audit-view",
        {
            "tx_public_key_hex": output["tx_public_key_hex"],
            "private_view_key_hex": WALLET["view_private"],
            "encrypted_amount_hex": output["encrypted_amount_hex"],
        },
    )
    belongs = await _crypto(
        "/crypto/check-output",
        {
            "tx_public_key_hex": output["tx_public_key_hex"],
            "private_view_key_hex": WALLET["view_private"],
            "public_spend_key_hex": WALLET["spend_public"],
            "one_time_public_key_hex": output["one_time_public_key_hex"],
        },
    )
    return {
        "output_id": output_id,
        "amount": decrypted["decrypted_amount"],
        "belongs_to_view_key": belongs["belongs_to_view_key"],
        "spent": output_id in spent_output_ids,
    }


# ------------------------------------------------------------ sonde & admin
class RingSizeRequest(BaseModel):
    ring_size: int


@app.post("/admin/ring-size", dependencies=[Depends(require_service_token)])
def set_ring_size(req: RingSizeRequest):
    """Durcissement demandé par remediation_playbook.yml (étape 2).

    Effet RÉEL : la taille d'anneau par défaut utilisée quand le client ne
    fournit pas de leurres (`/decoys`, `/tx/submit`). Avant, le playbook
    appelait `/admin/ring-size` sur le noyau Rust : cet endpoint n'existait pas,
    la tâche échouait sous `ignore_errors`, et le durcissement n'avait aucun
    effet observable — le playbook « réussissait » sans rien durcir.
    """
    global RING_SIZE
    if not 2 <= req.ring_size <= 64:
        raise HTTPException(status_code=400, detail="ring_size hors bornes (2..64)")
    previous, RING_SIZE = RING_SIZE, req.ring_size
    return {"status": "hardened", "ring_size": RING_SIZE, "previous": previous}


async def _submit_probe(probe_id: str) -> int:
    """Dépense la sonde via `/tx/submit` (même chemin que les clients)."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(
            f"{SELF_URL}/tx/submit",
            json={"real_output_id": probe_id, "synthetic": True},
        )
    return response.status_code


def _forget_probe_outputs(output_ids: list[str]) -> None:
    """Retire des outputs internes usés du registre mémoire ET du journal.

    La sonde émet un output toutes les 4 s (~21 600/jour) et ne les retirait
    jamais : le dictionnaire grossissait indéfiniment, `/metrics` avec lui. On
    garde les N derniers (diagnostic), les plus vieux sont dépensés donc sans
    valeur pour la démonstration.
    """
    if not output_ids:
        return
    with _outputs_lock:
        for output_id in output_ids:
            OUTPUTS.pop(output_id, None)
    for output_id in output_ids:
        spent_output_ids.discard(output_id)
    REGISTRY.quarantine(list(output_ids))


def _forget_probe_output(output_id: str) -> None:
    _forget_probe_outputs([output_id])


def _probe_order(output_id: str) -> int:
    """Ordre d'émission d'un identifiant `output_<n>` (0 si non numérique)."""
    try:
        return int(output_id.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return 0


def _prune_probe_outputs(keep: int | None = None) -> list[str]:
    """Ne conserve que les `keep` derniers outputs internes. Retourne les retirés."""
    keep = max(keep if keep is not None else PROBE_OUTPUT_RETENTION, 1)
    internes = [o["id"] for o in _snapshot_outputs() if o.get("internal")]
    internes.sort(key=_probe_order)
    excedent = internes[:-keep]
    _forget_probe_outputs(excedent)
    return excedent


async def _auto_probe_once() -> None:
    """Un cycle de la sonde : émettre, dépenser, oublier le plus vieil output."""
    created = await _mint(1, internal=True)
    if not created:
        auto_probe_status["ok"] = False
        auto_probe_status["last_error"] = "émission impossible (portefeuille non prêt)"
        return
    # L'identifiant vient de l'émission, pas d'un compteur partagé : une
    # ré-alimentation concurrente pourrait sinon faire dépenser la mauvaise
    # sonde.
    probe_id = created[-1]
    # La borne est appliquée AVANT la dépense : si `/tx/submit` échoue en
    # boucle, l'output non dépensé ne s'accumule pas pour autant.
    _probe_outputs.append(probe_id)
    while len(_probe_outputs) > max(PROBE_OUTPUT_RETENTION, 1):
        _forget_probe_output(_probe_outputs.popleft())
    auto_probe_status["ok"] = await _submit_probe(probe_id) == 200


async def _auto_probe_loop():
    """Bot de transaction légitime : preuve continue avant/pendant/après.

    Il génère aussi le trafic de référence dont le ML a besoin pour ne pas
    voir une fenêtre vide. Un échec ici est le signal le plus fiable que le
    service est tombé.

    Chaque sonde consomme un output FRAIS : réutiliser le même id produirait la
    même key image et déclencherait l'anti-double-dépense, ce qui ferait croire
    à une attaque permanente (bug du squelette d'origine).
    """
    while True:
        try:
            await _auto_probe_once()
        except Exception as exc:  # noqa: BLE001 - la sonde ne doit jamais tuer le service
            auto_probe_status["ok"] = False
            auto_probe_status["last_error"] = f"{type(exc).__name__}: {exc}"
        auto_probe_status["last_run"] = time.time()
        await asyncio.sleep(4)


@app.get("/tx/auto-probe/status")
def probe_status():
    return auto_probe_status


@app.post("/admin/quarantine-outputs", dependencies=[Depends(require_service_token)])
def quarantine_outputs():
    """Appelé par remediation_playbook.yml (étape 2).

    Retire du pool de leurres les outputs que le ML a désignés comme suspects :
    les plus ANCIENS. C'est le signal exactement mesuré par le détecteur
    (`decoy_median_age_days`) — un attaquant qui empoisonne l'anneau pioche
    uniformément dans le registre, donc majoritairement dans la traîne ancienne.

    Pourquoi pas un tirage aléatoire (version d'origine) : il ne corrigeait rien
    (les outputs empoisonnés restaient en place) tout en vidant le registre à
    chaque incident. Après une vingtaine de répétitions de la démo, `/decoys`
    ne pouvait plus fournir 10 leurres. Constaté en recette.
    """
    suspects = sorted(
        _snapshot_outputs(), key=lambda o: o["age_days"], reverse=True
    )[:25]
    quarantined = {o["id"] for o in suspects}
    with _outputs_lock:
        for output_id in quarantined:
            OUTPUTS.pop(output_id, None)
    REGISTRY.quarantine(sorted(quarantined))
    return {
        "status": "quarantined",
        "count": len(quarantined),
        "criterion": "oldest_outputs",
        "remaining_outputs": len(_snapshot_outputs()),
    }


@app.get("/metrics")
def metrics():
    """Métriques Prometheus du paiement et du noyau cryptographique."""
    # Un seul snapshot pour toutes les jauges : les compteurs restent cohérents
    # entre eux, et l'itération ne peut pas voir le dict changer de taille.
    snapshot = _snapshot_outputs()
    internal = sum(1 for o in snapshot if o.get("internal"))
    lines = [
        "# HELP shadow_outputs_total Outputs présents dans le registre",
        "# TYPE shadow_outputs_total gauge",
        f"shadow_outputs_total {len(snapshot)}",
        "# HELP shadow_outputs_unspent Outputs encore dépensables",
        "# TYPE shadow_outputs_unspent gauge",
        f"shadow_outputs_unspent {sum(1 for o in snapshot if o['id'] not in spent_output_ids)}",
        "# HELP shadow_outputs_internal Outputs de la sonde de liveness",
        "# TYPE shadow_outputs_internal gauge",
        f"shadow_outputs_internal {internal}",
        "# HELP shadow_key_images_spent Key images consommées (anti-double-dépense)",
        "# TYPE shadow_key_images_spent counter",
        f"shadow_key_images_spent {len(spent_key_images)}",
        "# HELP shadow_ring_size Taille d'anneau par défaut (durcie par le playbook)",
        "# TYPE shadow_ring_size gauge",
        f"shadow_ring_size {RING_SIZE}",
        "# HELP shadow_crypto_core_ready 1 si le noyau cryptographique répond",
        "# TYPE shadow_crypto_core_ready gauge",
        f"shadow_crypto_core_ready {1 if CRYPTO_STATUS['ready'] else 0}",
        "# HELP shadow_pin_failures_ratio Ratio de PIN refusés sur 30 s",
        "# TYPE shadow_pin_failures_ratio gauge",
        f"shadow_pin_failures_ratio {pin_stats()['failure_ratio']}",
        "# HELP shadow_probe_ok 1 si la sonde de transaction légitime passe",
        "# TYPE shadow_probe_ok gauge",
        f"shadow_probe_ok {1 if auto_probe_status.get('ok') else 0}",
        "# HELP shadow_service_token_configured 1 si les endpoints d'administration sont ouverts",
        "# TYPE shadow_service_token_configured gauge",
        f"shadow_service_token_configured {1 if service_token_configured() else 0}",
        "# HELP shadow_registry_durable 1 si le registre d'outputs survit a un redemarrage",
        "# TYPE shadow_registry_durable gauge",
        f"shadow_registry_durable {1 if REGISTRY.available else 0}",
        "# HELP shadow_registry_lost_writes_total Ecritures perdues pendant une indisponibilite PostgreSQL",
        "# TYPE shadow_registry_lost_writes_total counter",
        f"shadow_registry_lost_writes_total {REGISTRY.lost_writes}",
        "# HELP shadow_registry_reconnect_attempts_total Tentatives de reconnexion au registre",
        "# TYPE shadow_registry_reconnect_attempts_total counter",
        f"shadow_registry_reconnect_attempts_total {REGISTRY.reconnect_attempts}",
        "# HELP shadow_telemetry_dropped_total Observations perdues (ML injoignable ou jeton refuse)",
        "# TYPE shadow_telemetry_dropped_total counter",
        f"shadow_telemetry_dropped_total {TELEMETRY['dropped']}",
    ]
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "outputs": len(OUTPUTS),
        "spent_key_images": len(spent_key_images),
        "ring_size": RING_SIZE,
        "crypto_core": "ready" if CRYPTO_STATUS["ready"] else "degraded",
        "crypto_last_error": CRYPTO_STATUS["last_error"],
        # `generated` = PIN aléatoire tiré au démarrage : à fixer dans `.env`
        # pour une démonstration reproductible. La valeur n'est JAMAIS publiée.
        "pin_source": PIN_SOURCE,
        "pin_lockout_threshold": PIN_LOCKOUT_THRESHOLD,
        # Les endpoints /admin/* sont fermés si ce champ est false.
        "service_token_configured": service_token_configured(),
        # `durable: false` = registre en mémoire : un redémarrage perd les
        # outputs, donc la transaction de preuve. Publié, jamais silencieux.
        "registry": REGISTRY.health(),
        # `dropped > 0` = le détecteur ne reçoit plus la télémétrie (jeton
        # absent, service arrêté). Sans ce champ, la panne était invisible.
        "telemetry": TELEMETRY,
    }
