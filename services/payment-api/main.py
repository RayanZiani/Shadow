"""payment-api — le SI de paiement fictif, cœur du pilier DEV.
Appelle monero-crypto-core (Rust) pour tout ce qui est cryptographique,
et pousse une télémétrie vers ml-detector à chaque transaction.
"""
import asyncio
import os
import random
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

CRYPTO_CORE_URL = os.environ.get("CRYPTO_CORE_URL", "http://monero-crypto-core:8081")
ML_DETECTOR_URL = os.environ.get("ML_DETECTOR_URL", "http://ml-detector:8083")
RING_SIZE = int(os.environ.get("RING_SIZE_DEFAULT", "11"))

# État en mémoire pour le squelette — remplacez par PostgreSQL (voir plan)
spent_key_images: set[str] = set()
decoy_pool: list[str] = [f"output_{i}" for i in range(500)]  # TODO: table outputs réelle
auto_probe_status = {"last_run": None, "ok": True}


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_auto_probe_loop())
    yield
    task.cancel()


app = FastAPI(title="payment-api", lifespan=lifespan)


class TxSubmitRequest(BaseModel):
    real_output_id: str
    amount: int
    spend_private_key_hex: str


@app.get("/decoys")
def get_decoys(count: int = RING_SIZE - 1):
    # TODO (vous) : tirage par distribution gamma sur l'âge des outputs
    # (comme un vrai pool de leurres Monero), pas un sample uniforme.
    return {"decoys": random.sample(decoy_pool, min(count, len(decoy_pool)))}


@app.post("/tx/submit")
async def submit_tx(req: TxSubmitRequest):
    async with httpx.AsyncClient(timeout=5.0) as client:
        decoys = (await client.get(f"http://localhost:8080/decoys")).json()["decoys"]

        sign_resp = await client.post(
            f"{CRYPTO_CORE_URL}/crypto/ring-sign",
            json={
                "real_output_id": req.real_output_id,
                "decoy_ids": decoys,
                "amount": req.amount,
                "spend_private_key_hex": req.spend_private_key_hex,
            },
        )
        sign = sign_resp.json()

        if sign["key_image_hex"] in spent_key_images:
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

        # Télémétrie temps réel vers le pilier IA/DATA — ne bloque jamais la réponse utilisateur
        asyncio.create_task(
            _send_telemetry(
                {
                    "ring_verify_cpu_us": verify["verify_cpu_microseconds"],
                    "ring_size": sign["ring_size"],
                    "timestamp": time.time(),
                }
            )
        )

    return {"status": "confirmed", "key_image": sign["key_image_hex"]}


async def _send_telemetry(vector: dict):
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(f"{ML_DETECTOR_URL}/ingest", json=vector)
    except httpx.HTTPError:
        pass  # le ML ne doit jamais faire planter un paiement légitime


async def _auto_probe_loop():
    """Bot de transaction légitime : preuve continue pour le jury (avant/pendant/après)."""
    while True:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    "http://localhost:8080/tx/submit",
                    json={
                        "real_output_id": "auto_probe_output",
                        "amount": 1,
                        "spend_private_key_hex": "00" * 32,
                    },
                )
            auto_probe_status["ok"] = True
        except Exception:
            auto_probe_status["ok"] = False
        auto_probe_status["last_run"] = time.time()
        await asyncio.sleep(4)


@app.get("/tx/auto-probe/status")
def probe_status():
    return auto_probe_status


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
