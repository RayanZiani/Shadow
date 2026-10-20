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
import time
from collections import deque
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

CRYPTO_CORE_URL = os.environ.get("CRYPTO_CORE_URL", "http://monero-crypto-core:8081")
ML_DETECTOR_URL = os.environ.get("ML_DETECTOR_URL", "http://ml-detector:8083")
REMEDIATION_STATE_URL = os.environ.get(
    "REMEDIATION_STATE_URL", "http://remediation-engine:8084/state/report"
)
SELF_URL = os.environ.get("SELF_URL", "http://localhost:8080")
RING_SIZE = int(os.environ.get("RING_SIZE_DEFAULT", "11"))
PIN_CODE = os.environ.get("DEMO_PIN", "1234")
# Nombre d'outputs émis au démarrage et seuil de réapprovisionnement.
INITIAL_OUTPUTS = int(os.environ.get("INITIAL_OUTPUTS", "500"))
MIN_UNSPENT = int(os.environ.get("MIN_UNSPENT_OUTPUTS", "250"))

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
_registry_seq = 0

spent_key_images: set[str] = set()
# Outputs réellement dépensés. On suit l'IDENTIFIANT en plus de la key image :
# la key image est produite par le noyau cryptographique et sa dérivation peut
# changer (placeholder -> bLSAG réel). Filtrer `/outputs?unspent=true` sur une
# dérivation recopiée ici donnait un pool faux dès que le noyau Rust réel était
# utilisé : le client retombait en boucle sur des outputs déjà dépensés et
# recevait des 409 en série. Constaté en recette sur `docker compose`.
spent_output_ids: set[str] = set()
auto_probe_status = {"last_run": None, "ok": True}
LAST_TX: dict | None = None

# --- Compteurs d'observations ----------------------------------------------
pin_attempts: deque[float] = deque(maxlen=500)
pin_failures: deque[float] = deque(maxlen=500)
PIN_WINDOW_SECONDS = 30.0


# ------------------------------------------------------------------ crypto
async def _crypto(path: str, payload: dict, timeout: float = 5.0) -> dict:
    """Appel au noyau Rust. Toute panne est explicite : jamais de silence."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{CRYPTO_CORE_URL}{path}", json=payload)
    except httpx.HTTPError as exc:
        CRYPTO_STATUS["last_error"] = f"{type(exc).__name__}: {exc}"
        raise HTTPException(status_code=503, detail=f"noyau crypto injoignable ({path})") from exc
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
    """Génère (ou recharge) la paire de clés et publie les clés publiques."""
    WALLET["view_private"] = WALLET["view_private"] or secrets.token_hex(32)
    WALLET["spend_private"] = WALLET["spend_private"] or secrets.token_hex(32)
    keys = await _crypto(
        "/crypto/keys",
        {
            "private_view_key_hex": WALLET["view_private"],
            "private_spend_key_hex": WALLET["spend_private"],
        },
    )
    WALLET["view_public"] = keys["public_view_key_hex"]
    WALLET["spend_public"] = keys["public_spend_key_hex"]


async def _mint(count: int, internal: bool = False) -> list[str]:
    """Émet `count` outputs chiffrés. Le registre ne voit QUE le chiffré."""
    global _registry_seq
    if not WALLET["view_public"]:
        return []
    minted = await _crypto(
        "/crypto/mint-outputs",
        {
            "count": count,
            "public_spend_key_hex": WALLET["spend_public"],
            "public_view_key_hex": WALLET["view_public"],
        },
    )
    created: list[str] = []
    for item in minted["outputs"]:
        _registry_seq += 1
        output = {
            "id": f"output_{_registry_seq}",
            "age_days": 0.01 if internal else round(random.lognormvariate(3.2, 1.1), 3),
            **item,
        }
        if internal:
            output["internal"] = True
        OUTPUTS[output["id"]] = output
        created.append(output["id"])
    return created


async def _replenish_pool() -> None:
    """Réapprovisionne le registre, avec la MÊME loi d'âge.

    Sans cela, deux passages de `e2e_demo.py` épuisaient les 500 outputs et les
    clients recevaient des 409 en série — ce qui ressemble à une attaque
    permanente. Constaté en recette.
    """
    unspent = sum(
        1
        for o in OUTPUTS.values()
        if o["id"] not in spent_output_ids and not o.get("internal")
    )
    while unspent < MIN_UNSPENT:
        await _mint(200)
        unspent += 200


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
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
    outputs = list(OUTPUTS.values())
    if unspent:
        await _replenish_pool()
        outputs = [o for o in OUTPUTS.values() if o["id"] not in spent_output_ids]
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
def get_decoys(count: int | None = None):
    """Tire les leurres selon une gamma sur l'âge (biais récents, comme Monero).

    Les outputs INTERNES (sonde de liveness) sont exclus : ils ont un âge
    artificiel de 0,01 j et, comme la gamma favorise massivement les récents,
    ils finissaient par représenter l'essentiel du pool. Conséquence observée
    en recette : l'entropie et l'asymétrie dérivaient au fil du temps, et le
    modèle entraîné une heure plus tôt ne correspondait plus au trafic.
    """
    if count is None:
        count = RING_SIZE - 1
    candidates = [o for o in OUTPUTS.values() if not o.get("internal")]
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
    return {"decoys": decoys, "ages_days": [OUTPUTS[i]["age_days"] for i in decoys]}


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
    decoy_ids: list[str] | None = None
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
        unknown = [i for i in req.decoy_ids if i not in OUTPUTS]
        if unknown:
            raise HTTPException(status_code=400, detail=f"leurres inconnus: {unknown[:3]}")
        decoy_ids = req.decoy_ids
    else:
        decoy_ids = get_decoys(RING_SIZE - 1)["decoys"]

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

    if signed["key_image_hex"] in spent_key_images:
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
        raise HTTPException(status_code=400, detail=f"anneau invalide: {verified.get('reason')}")

    # 6. Équilibre des engagements : ∑C_in - ∑C_out = 0.
    balance = await _crypto(
        "/crypto/commitment-balance",
        {"inputs": [commitment_in], "outputs": commitments_out},
    )
    if not balance["balanced"]:
        raise HTTPException(status_code=400, detail="engagements non équilibrés")

    spent_key_images.add(signed["key_image_hex"])
    spent_output_ids.add(req.real_output_id)

    latency_ms = (time.perf_counter() - started) * 1000
    ages = [OUTPUTS[i]["age_days"] for i in decoy_ids]

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


async def _send_telemetry(observation: dict):
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(f"{ML_DETECTOR_URL}/ingest", json=observation)
    except httpx.HTTPError:
        pass  # le ML ne doit jamais faire planter un paiement légitime


async def _report_success_tx(detail: dict):
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(
                REMEDIATION_STATE_URL, json={"state": "NORMAL_TX", "detail": detail}
            )
    except httpx.HTTPError:
        pass


# ---------------------------------------------------------------------- PIN
class PinRequest(BaseModel):
    pin: str
    amount: int = 1


@app.post("/pin/verify")
async def verify_pin(req: PinRequest):
    """Saisie au terminal physique (ou pavé USB, cf. hardware/README.md).

    Ne déclenche AUCUNE remédiation et ne contourne pas la vérification
    cryptographique : un PIN correct autorise seulement une tx de démo, qui
    passe ensuite par /tx/submit comme n'importe quel client.
    """
    now = time.time()
    pin_attempts.append(now)
    ok = hmac.compare_digest(req.pin, PIN_CODE)
    if not ok:
        pin_failures.append(now)

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


@app.post("/admin/ring-size")
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
            # L'identifiant vient de l'émission, pas d'un compteur partagé : une
            # ré-alimentation concurrente pourrait sinon faire dépenser la
            # mauvaise sonde.
            created = await _mint(1, internal=True)
            probe_id = created[-1]
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    f"{SELF_URL}/tx/submit",
                    json={"real_output_id": probe_id, "synthetic": True},
                )
            auto_probe_status["ok"] = response.status_code == 200
        except Exception as exc:  # noqa: BLE001 - la sonde ne doit jamais tuer le service
            auto_probe_status["ok"] = False
            auto_probe_status["last_error"] = f"{type(exc).__name__}: {exc}"
        auto_probe_status["last_run"] = time.time()
        await asyncio.sleep(4)


@app.get("/tx/auto-probe/status")
def probe_status():
    return auto_probe_status


@app.post("/admin/quarantine-outputs")
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
        OUTPUTS.values(), key=lambda o: o["age_days"], reverse=True
    )[:25]
    quarantined = {o["id"] for o in suspects}
    for output_id in quarantined:
        OUTPUTS.pop(output_id, None)
    return {
        "status": "quarantined",
        "count": len(quarantined),
        "criterion": "oldest_outputs",
        "remaining_outputs": len(OUTPUTS),
    }


@app.get("/metrics")
def metrics():
    """Métriques Prometheus du paiement et du noyau cryptographique."""
    internal = sum(1 for o in OUTPUTS.values() if o.get("internal"))
    lines = [
        "# HELP shadow_outputs_total Outputs présents dans le registre",
        "# TYPE shadow_outputs_total gauge",
        f"shadow_outputs_total {len(OUTPUTS)}",
        "# HELP shadow_outputs_unspent Outputs encore dépensables",
        "# TYPE shadow_outputs_unspent gauge",
        f"shadow_outputs_unspent {sum(1 for i in OUTPUTS if i not in spent_output_ids)}",
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
    }
