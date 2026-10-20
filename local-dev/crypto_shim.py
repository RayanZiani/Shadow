"""SHIM DE RECETTE — ne fait PAS partie du livrable.

Reproduit l'interface HTTP de services/monero-crypto-core (Rust) pour valider
la chaîne payment-api -> ml-detector -> remediation-engine sur un poste sans
Docker ni cargo. La cryptographie est FACTICE : ce fichier ne doit jamais être
utilisé en démonstration, et le noyau Rust réel reste la seule référence.

Limite assumée : la key image est un SHA-256 de l'identifiant d'output, ce qui
suffit à tester l'anti-double-dépense, mais ne prouve rien cryptographiquement.
"""
import hashlib
import time

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="crypto-core-shim (TEST ONLY)")
SPENT = set()


class StealthRequest(BaseModel):
    public_spend_key_hex: str
    public_view_key_hex: str


class RingSignRequest(BaseModel):
    real_output_id: str
    decoy_ids: list[str]
    amount: int
    spend_private_key_hex: str


class RingVerifyRequest(BaseModel):
    ring_signature_hex: str
    key_image_hex: str
    pedersen_commitment_hex: str


@app.post("/crypto/stealth-address")
def stealth(req: StealthRequest):
    h = hashlib.sha256(f"{req.public_spend_key_hex}{req.public_view_key_hex}{time.time()}".encode())
    return {"stealth_address_hex": h.hexdigest(), "tx_public_key_hex": h.hexdigest()[::-1]}


@app.post("/crypto/ring-sign")
def ring_sign(req: RingSignRequest):
    # Key image déterministe par output : c'est ce qui permet de détecter
    # un rejeu (double dépense) dans payment-api.
    ki = hashlib.sha256(f"ki::{req.real_output_id}".encode()).hexdigest()
    sig = hashlib.sha256(
        f"sig::{req.real_output_id}::{','.join(req.decoy_ids)}::{req.amount}".encode()
    ).hexdigest()
    commitment = hashlib.sha256(f"C::{req.amount}".encode()).hexdigest()
    return {
        "ring_signature_hex": sig,
        "key_image_hex": ki,
        "pedersen_commitment_hex": commitment,
        "ring_size": len(req.decoy_ids) + 1,
    }


@app.post("/crypto/ring-verify")
def ring_verify(req: RingVerifyRequest):
    start = time.perf_counter()
    # Coût simulé proche d'une vraie vérification d'anneau (quelques ms).
    for _ in range(2000):
        hashlib.sha256(req.ring_signature_hex.encode()).digest()
    valid = len(req.ring_signature_hex) == 64
    return {
        "valid": valid,
        "verify_cpu_microseconds": int((time.perf_counter() - start) * 1_000_000),
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok", "shim": True}
