"""SHIM DE RECETTE — ne fait PAS partie du livrable.

Reproduit l'interface HTTP de services/monero-crypto-core (Rust) pour valider
la chaîne payment-api -> ml-detector -> remediation-engine sur un poste sans
Docker ni cargo. **La cryptographie est FACTICE** : ce fichier ne doit jamais
être utilisé en démonstration. Le noyau Rust réel (bLSAG + Pedersen + View Key)
est la seule référence, et il est testé par `test_crypto_flow.py`.

Ce qui est fidèle ici : les ENDPOINTS, les tailles de champs et l'arithmétique
de somme des engagements (sur des entiers, pas des points). Ce qui ne l'est
pas : la sécurité. Une key image est un hash, pas x·H_p(P).
"""
import hashlib
import secrets

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="crypto-core-shim (TEST ONLY)")
SPENT: set[str] = set()
MOD = 1 << 256


def _h(*parts: bytes) -> str:
    digest = hashlib.sha256(b"|".join(parts)).hexdigest()
    return digest


def _pub(priv_hex: str) -> str:
    return _h(b"pub", bytes.fromhex(priv_hex))


def _one_time_public(tx_public: str, spend_public: str) -> str:
    return _h(b"P", tx_public.encode(), spend_public.encode())


def _mask(tx_public: str) -> int:
    return int(_h(b"amount", tx_public.encode())[:16], 16)


def _encrypt(tx_public: str, amount: int) -> str:
    return format(amount ^ _mask(tx_public), "016x")


def _decrypt(tx_public: str, encrypted_hex: str) -> int:
    return int(encrypted_hex, 16) ^ _mask(tx_public)


# ------------------------------------------------------------------- modèles
class KeysRequest(BaseModel):
    private_view_key_hex: str
    private_spend_key_hex: str


class StealthRequest(BaseModel):
    public_spend_key_hex: str
    public_view_key_hex: str


class MintRequest(BaseModel):
    count: int
    public_spend_key_hex: str
    public_view_key_hex: str


class DeriveRequest(BaseModel):
    tx_public_key_hex: str
    private_view_key_hex: str
    private_spend_key_hex: str


class CheckOutputRequest(BaseModel):
    tx_public_key_hex: str
    private_view_key_hex: str
    public_spend_key_hex: str
    one_time_public_key_hex: str


class AuditViewRequest(BaseModel):
    tx_public_key_hex: str
    private_view_key_hex: str
    encrypted_amount_hex: str


class CommitRequest(BaseModel):
    amount: int
    blind_hex: str


class CommitSplitRequest(BaseModel):
    amount: int
    blind_hex: str
    parts: int


class BalanceRequest(BaseModel):
    inputs: list[str]
    outputs: list[str]


class RingSignRequest(BaseModel):
    ring: list[str]
    signer_index: int
    one_time_private_key_hex: str
    message: str
    amount: int
    blind_hex: str


class RingVerifyRequest(BaseModel):
    ring: list[str]
    message: str
    ring_signature_hex: str
    key_image_hex: str
    pedersen_commitment_hex: str


# ------------------------------------------------------------------ endpoints
@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "crypto-core-shim", "shim": True, "curve": "sha256"}


@app.post("/crypto/keys")
def keys(req: KeysRequest):
    return {
        "public_view_key_hex": _pub(req.private_view_key_hex),
        "public_spend_key_hex": _pub(req.private_spend_key_hex),
    }


@app.post("/crypto/stealth-address")
def stealth(req: StealthRequest):
    r = secrets.token_hex(32)
    return {
        "stealth_address_hex": _one_time_public(r, req.public_spend_key_hex),
        "tx_public_key_hex": r,
    }


@app.post("/crypto/mint-outputs")
def mint(req: MintRequest):
    outputs = []
    for _ in range(max(0, min(req.count, 2000))):
        r = secrets.token_hex(32)
        amount = 1 + int(_h(b"a", r.encode())[:8], 16) % 500
        outputs.append(
            {
                "one_time_public_key_hex": _one_time_public(r, req.public_spend_key_hex),
                "tx_public_key_hex": r,
                "encrypted_amount_hex": _encrypt(r, amount),
            }
        )
    return {"outputs": outputs, "amounts_visible": False}


@app.post("/crypto/derive-one-time-key")
def derive(req: DeriveRequest):
    spend_public = _pub(req.private_spend_key_hex)
    return {
        "one_time_private_key_hex": _h(
            b"x", req.tx_public_key_hex.encode(), req.private_view_key_hex.encode()
        ),
        "one_time_public_key_hex": _one_time_public(req.tx_public_key_hex, spend_public),
    }


@app.post("/crypto/check-output")
def check_output(req: CheckOutputRequest):
    expected = _one_time_public(req.tx_public_key_hex, req.public_spend_key_hex)
    return {"belongs_to_view_key": expected == req.one_time_public_key_hex}


@app.post("/crypto/audit-view")
def audit_view(req: AuditViewRequest):
    return {"decrypted_amount": _decrypt(req.tx_public_key_hex, req.encrypted_amount_hex)}


def _commit_int(amount: int, blind_hex: str) -> int:
    return (amount + int(blind_hex, 16)) % MOD


@app.post("/crypto/commit")
def commit(req: CommitRequest):
    return {"commitment_hex": format(_commit_int(req.amount, req.blind_hex), "064x")}


@app.post("/crypto/commit-split")
def commit_split(req: CommitSplitRequest):
    parts = max(1, min(req.parts, 16))
    base = req.amount // parts
    amounts = [base] * parts
    amounts[-1] += req.amount - base * parts
    blind = int(req.blind_hex, 16) % MOD
    blinds = [secrets.randbelow(MOD) for _ in range(parts - 1)]
    blinds.append((blind - sum(blinds)) % MOD)
    return {
        "parts": [
            {
                "amount": amount,
                "blind_hex": format(blind_part, "064x"),
                "commitment_hex": format(_commit_int(amount, format(blind_part, "064x")), "064x"),
            }
            for amount, blind_part in zip(amounts, blinds)
        ]
    }


@app.post("/crypto/commitment-balance")
def commitment_balance(req: BalanceRequest):
    total = sum(int(p, 16) for p in req.inputs) - sum(int(p, 16) for p in req.outputs)
    return {"balanced": total % MOD == 0, "difference_hex": format(total % MOD, "064x")}


@app.post("/crypto/ring-sign")
def ring_sign(req: RingSignRequest):
    key_image = _h(b"ki", req.one_time_private_key_hex.encode())
    signature = _h(
        b"sig",
        req.message.encode(),
        req.one_time_private_key_hex.encode(),
        ",".join(req.ring).encode(),
    )
    return {
        "ring_signature_hex": signature,
        "key_image_hex": key_image,
        "pedersen_commitment_hex": format(_commit_int(req.amount, req.blind_hex), "064x"),
        "ring_size": len(req.ring),
        "scheme": "shim (non cryptographique)",
    }


@app.post("/crypto/ring-verify")
def ring_verify(req: RingVerifyRequest):
    valid = len(req.ring_signature_hex) == 64 and len(req.key_image_hex) == 64
    return {
        "valid": valid,
        "verify_cpu_microseconds": 900,
        "ring_size": len(req.ring),
        "reason": None if valid else "signature malformée (shim)",
    }
