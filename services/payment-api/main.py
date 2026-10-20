"""payment-api — le SI de paiement fictif, cœur du pilier DEV.

Appelle monero-crypto-core (Rust) pour toute la cryptographie et pousse une
télémétrie d'OBSERVATIONS vers ml-detector à chaque transaction. Le détecteur
calcule lui-même les features : il n'existe qu'une seule définition de chaque
feature (services/ml-detector/features.py).

Modèle de menace supporté (c'est ce qui rend la démo réelle) :

  * `GET /outputs` expose le registre public des outputs (comme une blockchain).
  * `POST /tx/submit` accepte un anneau construit par le client. Un wallet
    honnête tire ses leurres via `GET /decoys` (distribution gamma, biais
    récents) ; un attaquant qui tire uniformément depuis `/outputs` produit une
    distribution plate, ce qui est exactement le signal appris par le ML.
  * Un rejeu de key image est rejeté (409) et compté dans la télémétrie.

Ce service ne prend AUCUNE décision de sécurité : il constate, il rejette ce
qui est invalide, il publie. La remédiation est déclenchée par ml-detector.
"""
import asyncio
import hmac
import os
import random
import time
from collections import deque
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
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

# --- Registre des outputs ---------------------------------------------------
# En mémoire pour la démo ; à remplacer par PostgreSQL. Les âges suivent une
# loi log-normale : beaucoup d'outputs récents, une longue traîne d'anciens,
# comme un vrai registre.
random.seed(1337)
OUTPUTS: list[dict] = [
    {"id": f"output_{i}", "age_days": round(random.lognormvariate(3.2, 1.1), 3)}
    for i in range(500)
]
OUTPUT_AGE = {o["id"]: o["age_days"] for o in OUTPUTS}
spent_key_images: set[str] = set()
# Outputs réellement dépensés. On suit l'IDENTIFIANT en plus de la key image :
# la key image est produite par le noyau cryptographique et sa dérivation peut
# changer (placeholder -> CLSAG réel). Filtrer `/outputs?unspent=true` sur une
# dérivation recopiée ici donnait un pool faux dès que le noyau Rust réel était
# utilisé (la dérivation locale ne correspondait plus) : le client retombait en
# boucle sur des outputs déjà dépensés et recevait des 409 en série. Constaté en
# recette sur `docker compose` — invisible avec le shim, qui recopiait la même
# formule.
spent_output_ids: set[str] = set()
auto_probe_status = {"last_run": None, "ok": True}

# Le registre doit pouvoir alimenter plusieurs répétitions de la démo : chaque
# transaction consomme un output réel. Avec 500 outputs et ~1 tx/s, le pool était
# épuisé après deux passages de `e2e_demo.py` — les clients recevaient alors des
# 409 en série, ce qui ressemble à une attaque permanente. On réalimente donc le
# registre, comme les nouveaux blocs d'une blockchain, avec la MÊME loi d'âge :
# la distribution vue par le ML ne change pas.
_registry_seq = len(OUTPUTS)


def _replenish_pool(min_unspent: int = 250, batch: int = 200) -> None:
    global _registry_seq
    unspent = sum(1 for o in OUTPUTS if o["id"] not in spent_output_ids and not o.get("internal"))
    while unspent < min_unspent:
        for _ in range(batch):
            _registry_seq += 1
            output = {
                "id": f"output_{_registry_seq}",
                "age_days": round(random.lognormvariate(3.2, 1.1), 3),
            }
            OUTPUTS.append(output)
            OUTPUT_AGE[output["id"]] = output["age_days"]
        unspent += batch

# --- Compteurs d'observations ----------------------------------------------
pin_attempts: deque[float] = deque(maxlen=500)
pin_failures: deque[float] = deque(maxlen=500)
PIN_WINDOW_SECONDS = 30.0


@asynccontextmanager
async def lifespan(app: FastAPI):
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
def list_outputs(limit: int = 500, unspent: bool = False):
    """Registre public : un attaquant peut le lire, c'est le principe d'une
    blockchain. C'est ce qui lui permet de construire un anneau « plat ».

    `unspent=true` : ce qu'un wallet honnête utiliserait pour choisir un output
    réel. Sans ce filtre, tout client retombe sur une key image déjà dépensée
    et déclenche l'anti-double-dépense (faux positif d'attaque).
    """
    outputs = OUTPUTS
    if unspent:
        _replenish_pool()
        outputs = [o for o in OUTPUTS if o["id"] not in spent_output_ids]
    # Les outputs internes (sonde) ne sont pas des leurres candidats : les
    # exposer laisserait un attaquant reconstruire un anneau entièrement
    # composé de nos propres sondes.
    public = [o for o in outputs if not o.get("internal")]
    return {"count": len(public), "outputs": public[:limit]}


def _key_image_of(output_id: str) -> str:
    """Dérivation de la key image — DOIT rester identique à celle du noyau.

    Utilisée uniquement pour les tests et le diagnostic. Le filtrage
    `unspent` ne s'appuie plus dessus (voir `spent_output_ids`).
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
    candidates = [o for o in OUTPUTS if not o.get("internal")]
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
    return {"decoys": decoys, "ages_days": [OUTPUT_AGE[i] for i in decoys]}


# ------------------------------------------------------------------- paiement
class TxSubmitRequest(BaseModel):
    real_output_id: str
    amount: int
    spend_private_key_hex: str
    # Anneau construit par le client. None => le serveur tire selon la gamma.
    decoy_ids: list[str] | None = None
    # Marque le trafic de la sonde de liveness (exclu du ML).
    synthetic: bool = False


@app.post("/tx/submit")
async def submit_tx(req: TxSubmitRequest):
    started = time.perf_counter()
    if req.real_output_id not in OUTPUT_AGE:
        raise HTTPException(status_code=400, detail="output inconnu")

    if req.decoy_ids is not None:
        unknown = [i for i in req.decoy_ids if i not in OUTPUT_AGE]
        if unknown:
            raise HTTPException(status_code=400, detail=f"leurres inconnus: {unknown[:3]}")
        decoy_ids = req.decoy_ids
    else:
        decoy_ids = get_decoys(RING_SIZE - 1)["decoys"]

    rejected = False
    async with httpx.AsyncClient(timeout=5.0) as client:
        sign_resp = await client.post(
            f"{CRYPTO_CORE_URL}/crypto/ring-sign",
            json={
                "real_output_id": req.real_output_id,
                "decoy_ids": decoy_ids,
                "amount": req.amount,
                "spend_private_key_hex": req.spend_private_key_hex,
            },
        )
        sign = sign_resp.json()

        if sign["key_image_hex"] in spent_key_images:
            # Double dépense : on rejette MAIS on publie la télémétrie, sinon
            # l'attaque serait invisible pour le détecteur.
            rejected = True
            await _send_telemetry(
                _observation(
                    cpu_us=0.0,
                    ring_size=len(decoy_ids) + 1,
                    ages=[],
                    rejected=True,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            )
            raise HTTPException(status_code=409, detail="double dépense détectée")

        verify_resp = await client.post(
            f"{CRYPTO_CORE_URL}/crypto/ring-verify",
            json={
                "ring_signature_hex": sign["ring_signature_hex"],
                "key_image_hex": sign["key_image_hex"],
                "pedersen_commitment_hex": sign["pedersen_commitment_hex"],
            },
        )
        verify = verify_resp.json()
        if not verify["valid"]:
            raise HTTPException(status_code=400, detail="anneau invalide")

        spent_key_images.add(sign["key_image_hex"])
        spent_output_ids.add(req.real_output_id)

    latency_ms = (time.perf_counter() - started) * 1000
    ages = [OUTPUT_AGE[i] for i in decoy_ids]

    # Télémétrie : ne bloque jamais la réponse utilisateur.
    asyncio.create_task(
        _send_telemetry(
            _observation(
                cpu_us=verify["verify_cpu_microseconds"],
                ring_size=sign["ring_size"],
                ages=ages,
                rejected=rejected,
                latency_ms=latency_ms,
                synthetic=req.synthetic,
            )
        )
    )

    # Miroir d'état : une tx confirmée est un fait vérifié, rediffusé au
    # dashboard ET au terminal. Ne déclenche jamais de remédiation.
    asyncio.create_task(
        _report_success_tx({"key_image": sign["key_image_hex"], "amount": req.amount})
    )

    return {
        "status": "confirmed",
        "key_image": sign["key_image_hex"],
        "ring_size": sign["ring_size"],
        "verify_cpu_us": verify["verify_cpu_microseconds"],
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
    counter = 0
    while True:
        counter += 1
        probe_id = f"probe_{int(time.time())}_{counter}"
        OUTPUTS.append({"id": probe_id, "age_days": 0.01, "internal": True})
        OUTPUT_AGE[probe_id] = 0.01
        # Les sondes sont à usage unique. On retire les précédentes une fois
        # dépensées : sans ce ménage, le registre gagnait 900 entrées par heure
        # et la sérialisation de /outputs devenait inutilement lourde après une
        # journée de répétitions.
        OUTPUTS[:] = [
            o for o in OUTPUTS if not o.get("internal") or o["id"] not in spent_output_ids
        ]
        live_ids = {o["id"] for o in OUTPUTS}
        for output_id in [i for i in OUTPUT_AGE if i.startswith("probe_")]:
            if output_id not in live_ids:
                OUTPUT_AGE.pop(output_id, None)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    f"{SELF_URL}/tx/submit",
                    json={
                        "real_output_id": probe_id,
                        "amount": 1,
                        "spend_private_key_hex": "00" * 32,
                        "synthetic": True,
                    },
                )
            auto_probe_status["ok"] = response.status_code == 200
        except Exception:
            auto_probe_status["ok"] = False
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
    suspects = sorted(OUTPUTS, key=lambda o: o["age_days"], reverse=True)[:25]
    quarantined = {o["id"] for o in suspects}
    OUTPUTS[:] = [o for o in OUTPUTS if o["id"] not in quarantined]
    OUTPUT_AGE.clear()
    OUTPUT_AGE.update({o["id"]: o["age_days"] for o in OUTPUTS})
    return {
        "status": "quarantined",
        "count": len(quarantined),
        "criterion": "oldest_outputs",
        "remaining_outputs": len(OUTPUTS),
    }


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "outputs": len(OUTPUTS),
        "spent_key_images": len(spent_key_images),
        "ring_size": RING_SIZE,
    }
