"""waf — reverse proxy d'entrée avec déception active.

Rôle dans l'architecture
------------------------
Tout le trafic « client » vers le SI de paiement passe par ce service. Il ne
remplace pas le ML : il fait un filtrage GROSSIER au bord, sur le même signal
physique, et il exécute la bascule vers le honeypot que le playbook Ansible
demande.

  client  ->  waf:8085  ->  payment-api:8080   (trafic légitime)
                      \\->  shadow-api:8090    (client en cours de déception)

Pourquoi ce service existe
--------------------------
`remediation_playbook.yml` appelait `http://waf:8081/admin/redirect` : ce
service n'existait pas, la tâche échouait sous `ignore_errors`, et le honeypot
`shadow-api` n'était alimenté par personne. La déception active était donc
codée mais jamais branchée — le genre d'écart qu'un jury trouve en Q&A.

Règle de détection au bord (volontairement simple)
--------------------------------------------------
Un wallet honnête tire ses leurres selon une gamma biaisée vers les outputs
récents (âge médian ≈ 3,6 j). Un attaquant qui empoisonne l'anneau pioche
uniformément dans le registre (âge médian ≈ 19-31 j). Le WAF calcule l'âge
médian des `decoy_ids` d'une transaction et marque la requête « suspecte »
au-delà de `DECOY_AGE_THRESHOLD` (8 j par défaut : au-dessus du maximum
observé en trafic normal, 5,84 j).

C'est une règle de bord, pas la détection : elle ne déclenche RIEN. Seul
`ml-detector` déclenche la remédiation, et seul le playbook active la
déception. Le WAF ne fait qu'obéir à `/admin/redirect`.
"""
import os
import statistics
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

UPSTREAM = os.environ.get("WAF_UPSTREAM", "http://payment-api:8080")
HONEYPOT = os.environ.get("WAF_HONEYPOT", "http://shadow-api:8090")
DECOY_AGE_THRESHOLD = float(os.environ.get("DECOY_AGE_THRESHOLD", "8.0"))
# Nombre d'âges mémorisés : au-delà, on purge les plus anciennes entrées.
AGE_CACHE_MAX = 20000

app = FastAPI(title="waf (reverse proxy + déception active)")

# Âges des outputs, appris en proxyfiant /outputs : le WAF ne devine pas, il
# lit le registre public comme n'importe quel client.
AGE_CACHE: dict[str, float] = {}

STATE = {
    "deception": False,
    "target": None,
    "reason": None,
    "activated_at": None,
    "suspicious_requests": 0,
    "intercepted": 0,
    "forwarded": 0,
    "last_suspicious_at": None,
}


class RedirectRequest(BaseModel):
    target: str = "shadow-api:8090"
    reason: str = "remediation_playbook"


def _remember_ages(payload: dict) -> None:
    for output in payload.get("outputs", []):
        output_id = output.get("id")
        age = output.get("age_days")
        if output_id is not None and isinstance(age, (int, float)):
            AGE_CACHE[output_id] = float(age)
    if len(AGE_CACHE) > AGE_CACHE_MAX:
        # Purge simple : on garde les entrées les plus récemment ajoutées.
        for key in list(AGE_CACHE)[: len(AGE_CACHE) - AGE_CACHE_MAX]:
            AGE_CACHE.pop(key, None)


def _decoy_median_age(decoy_ids: list[str]) -> float | None:
    ages = [AGE_CACHE[i] for i in decoy_ids if i in AGE_CACHE]
    if len(ages) < 3:
        # Pas assez d'information : on ne conclut pas. Mieux vaut laisser passer
        # une requête que fabriquer un faux positif au bord.
        return None
    return float(statistics.median(ages))


def _is_suspicious(body: dict) -> tuple[bool, float | None]:
    decoys = body.get("decoy_ids")
    if not isinstance(decoys, list) or len(decoys) < 3:
        return False, None
    median = _decoy_median_age([str(i) for i in decoys])
    if median is None:
        return False, None
    return median > DECOY_AGE_THRESHOLD, median


# ------------------------------------------------------------------ admin
@app.post("/admin/redirect")
def redirect(req: RedirectRequest):
    """Active la déception : les transactions suspectes iront au honeypot.

    Appelé par l'étape 1 du playbook, SANS `ignore_errors` : si le WAF ne
    répond pas, la remédiation doit échouer bruyamment, pas faire semblant.
    """
    STATE.update(
        deception=True,
        target=req.target,
        reason=req.reason,
        activated_at=time.time(),
    )
    return {"status": "deception_active", **STATE}


@app.post("/admin/reset")
def reset():
    """Désactive la déception (avant chaque répétition de la démo)."""
    STATE.update(
        deception=False,
        target=None,
        reason=None,
        activated_at=None,
        suspicious_requests=0,
        intercepted=0,
        forwarded=0,
        last_suspicious_at=None,
    )
    return {"status": "reset", **STATE}


@app.get("/admin/state")
def state():
    return {**STATE, "decoy_age_threshold_days": DECOY_AGE_THRESHOLD, "known_outputs": len(AGE_CACHE)}


@app.get("/metrics")
def metrics():
    """Métriques Prometheus de la déception active."""
    lines = [
        "# HELP shadow_waf_deception_active 1 si la déception est armée",
        "# TYPE shadow_waf_deception_active gauge",
        f"shadow_waf_deception_active {1 if STATE['deception'] else 0}",
        "# HELP shadow_waf_suspicious_requests_total Requêtes portant le signal d'attaque",
        "# TYPE shadow_waf_suspicious_requests_total counter",
        f"shadow_waf_suspicious_requests_total {STATE['suspicious_requests']}",
        "# HELP shadow_waf_intercepted_total Requêtes déroutées vers le honeypot",
        "# TYPE shadow_waf_intercepted_total counter",
        f"shadow_waf_intercepted_total {STATE['intercepted']}",
        "# HELP shadow_waf_forwarded_total Requêtes transmises à payment-api",
        "# TYPE shadow_waf_forwarded_total counter",
        f"shadow_waf_forwarded_total {STATE['forwarded']}",
        "# HELP shadow_waf_known_outputs Outputs dont le WAF connaît l'âge",
        "# TYPE shadow_waf_known_outputs gauge",
        f"shadow_waf_known_outputs {len(AGE_CACHE)}",
    ]
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "service": "waf",
        "upstream": UPSTREAM,
        "honeypot": HONEYPOT,
        "deception": STATE["deception"],
    }


# ------------------------------------------------------------------ proxy
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(path: str, request: Request):
    raw = await request.body()
    body = None
    if raw:
        try:
            import json

            body = json.loads(raw)
        except ValueError:
            body = None

    suspicious, median_age = False, None
    if path == "tx/submit" and isinstance(body, dict):
        suspicious, median_age = _is_suspicious(body)
        if suspicious:
            STATE["suspicious_requests"] += 1
            STATE["last_suspicious_at"] = time.time()

    # Bascule vers le honeypot : uniquement si la déception est active ET que
    # la requête porte le signal. Une transaction légitime continue de passer,
    # même pendant la déception — c'est ce qui rend la démo lisible.
    if STATE["deception"] and suspicious:
        target = HONEYPOT
        STATE["intercepted"] += 1
    else:
        target = UPSTREAM
        STATE["forwarded"] += 1

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in ("host", "content-length", "connection")
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            upstream = await client.request(
                request.method,
                f"{target}/{path}",
                content=raw or None,
                headers=headers,
                params=dict(request.query_params),
            )
    except httpx.HTTPError as exc:
        return JSONResponse(
            status_code=502,
            content={"detail": f"passerelle indisponible ({target}): {type(exc).__name__}"},
        )

    if path == "outputs" and upstream.status_code == 200:
        try:
            _remember_ages(upstream.json())
        except ValueError:
            pass

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
        headers={"X-Shadow-Route": "honeypot" if target == HONEYPOT else "upstream"},
    )
