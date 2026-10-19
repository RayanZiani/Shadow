"""shadow-api — le honeypot. Le WAF y redirige l'IP/fingerprint de l'attaquant
une fois que remediation-engine a basculé la déception (Étape 1 du playbook).
L'attaquant continue de taper sur un faux nœud et ne reçoit jamais de 403 :
il voit des Canary Key Images, et chaque appel est loggé pour le SIEM.
"""
import logging
import random
import time

from fastapi import FastAPI, Request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("shadow-api")

app = FastAPI(title="shadow-api")

intercepted_log: list[dict] = []


def _log_interception(request: Request, extra: dict):
    entry = {
        "ts": time.time(),
        "client": request.client.host if request.client else "unknown",
        "path": request.url.path,
        **extra,
    }
    intercepted_log.append(entry)
    logger.warning("ATTAQUANT INTERCEPTÉ DANS LE HONEYPOT: %s", entry)
    return entry


@app.get("/decoys")
async def fake_decoys(request: Request):
    # Canary outputs : si ces IDs réapparaissent dans une vraie transaction
    # soumise à payment-api, c'est la preuve que l'attaquant a mordu.
    canary_ids = [f"canary_{random.randint(1000, 9999)}" for _ in range(10)]
    _log_interception(request, {"served": "canary_decoys"})
    return {"decoys": canary_ids}


@app.post("/tx/submit")
async def fake_submit(request: Request):
    body = await request.json()
    _log_interception(request, {"attempted_tx": body})
    # Toujours "confirmed" pour ne jamais éveiller les soupçons de l'attaquant
    return {"status": "confirmed", "key_image": f"canary_ki_{random.randint(100000,999999)}"}


@app.get("/admin/interceptions")
def list_interceptions():
    """Lu par le dashboard pour afficher le flux d'interception en direct."""
    return {"count": len(intercepted_log), "entries": intercepted_log[-50:]}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
