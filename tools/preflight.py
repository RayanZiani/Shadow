#!/usr/bin/env python3
"""Pré-vol de soutenance : « le SI est-il démontrable, maintenant ? »

    python tools/preflight.py            # tableau lisible + code de sortie
    python tools/preflight.py --json     # sortie machine (CI, script de veille)
    python tools/preflight.py --timeout 1.5

Une seule commande, exécutée la veille et le matin de la démonstration. Elle
répond à une question différente de `tools/check_compose.py` :

  - `check_compose.py`  : la pile est-elle COHÉRENTE sur disque ? (statique)
  - `preflight.py`      : la pile TOURNE-t-elle et le dépôt est-il livrable ?
                          (dynamique : HTTP, `.env`, git)

Ce qu'elle vérifie :

  1. `.env` présent et secrets non vides (SERVICE_TOKEN, BO_BOOTSTRAP_TOKEN,
     LED_DEVICE_TOKEN, TERMINAL_PIN). Les valeurs ne sont JAMAIS affichées :
     seulement « défini » / « non défini ».
  2. Santé HTTP des services (payment, ml, remediation, waf, backoffice, shop,
     rag, prometheus, alertmanager, grafana, loki, shadow), y compris les
     invariants qui cassent réellement la démo : noyau crypto prêt, modèle ML
     chargé, webhook de remédiation sans erreur, catalogue de la boutique.
  3. Cohérence du PIN : celui de `.env` est-il bien celui que le conteneur
     acceptera ? (un PIN défini mais non transmis est un piège classique)
  4. Dépôt livrable : `git status` propre et aucun secret suivi par git.

Codes de sortie :

  0  démontrable (des avertissements peuvent subsister)
  1  un contrôle BLOQUANT a échoué : services P0 ou secrets

Les services non-P0 absents sont des AVERTISSEMENTS, pas des échecs : la démo
reste faisable sans le RAG ou sans Loki. Le script ne lève jamais parce qu'un
service est absent — c'est justement son cas d'usage.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent

# La console Windows est en cp1252 par défaut : sans cela, le premier « → »
# lève UnicodeEncodeError et le pré-vol meurt en plein tableau. `replace`
# garantit qu'aucun caractère ne peut faire échouer le script.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # flux sans reconfigure (IDE, pytest)
        pass

# Secrets exigés par la démonstration. Les valeurs ne sortent JAMAIS de ce
# script : on ne publie que « défini » ou « non défini ».
REQUIRED_SECRETS = ("SERVICE_TOKEN", "BO_BOOTSTRAP_TOKEN", "LED_DEVICE_TOKEN", "TERMINAL_PIN")

# Motifs de secrets à ne jamais trouver dans un fichier suivi par git.
# Construits par concaténation pour que CE fichier ne se détecte pas lui-même
# (un `git grep` cherche du texte, pas une intention).
SECRET_PATTERN = "hvs" + chr(92) + ".|" + "BEGIN [A-Z ]*" + "PRIVATE" + " " + "KEY"

LABELS = {"ok": "OK", "warn": "AVERT.", "fail": "ÉCHEC"}


class ProbeError(Exception):
    """Échec de sonde déjà mis en forme (message lisible, sans traceback)."""


# ---------------------------------------------------------------------------
# Utilitaires HTTP
# ---------------------------------------------------------------------------
def fetch(url: str, timeout: float) -> tuple[int, bytes]:
    """GET sans dépendance externe. Ne lève que ProbeError."""
    request = urllib.request.Request(url, headers={"User-Agent": "shadow-preflight/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(2_000_000)
    except urllib.error.HTTPError as exc:  # sous-classe de URLError : en premier
        return exc.code, b""
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise ProbeError(f"délai dépassé (>{timeout:g} s)") from exc
        code = getattr(reason, "errno", None) or getattr(reason, "winerror", None)
        if isinstance(reason, ConnectionRefusedError) or code in (61, 111, 10061):
            raise ProbeError("connexion refusée — service arrêté ?") from exc
        raise ProbeError(f"injoignable ({str(reason)[:80]})") from exc


def fetch_json(url: str, timeout: float) -> tuple[int, dict]:
    status, payload = fetch(url, timeout)
    if not payload:
        return status, {}
    try:
        body = json.loads(payload.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ProbeError(f"réponse non JSON ({exc.msg})") from exc
    if not isinstance(body, dict):
        raise ProbeError("réponse JSON inattendue (objet attendu)")
    return status, body


def probe_json(url: str, parser):
    """Construit une sonde : GET JSON, puis invariant métier."""

    def run(timeout: float) -> tuple[str, str]:
        status, body = fetch_json(url, timeout)
        if status != 200:
            return "fail", f"HTTP {status}"
        return parser(body)

    return run


def probe_status(url: str):
    """Sonde « la page répond 200 », sans interpréter le corps."""

    def run(timeout: float) -> tuple[str, str]:
        status, _ = fetch(url, timeout)
        if status != 200:
            return "fail", f"HTTP {status}"
        return "ok", "prêt"

    return run


# ---------------------------------------------------------------------------
# Invariants par service
# ---------------------------------------------------------------------------
def parse_payment(body: dict) -> tuple[str, str]:
    crypto = body.get("crypto_core")
    detail = f"crypto_core={crypto}, pin_source={body.get('pin_source')}"
    if crypto != "ready":
        return "fail", detail + " — noyau crypto dégradé : la transaction de preuve échouera"
    registry = body.get("registry") or {}
    if isinstance(registry, dict) and registry.get("durable") is False:
        return "warn", detail + (
            f", registre en MÉMOIRE (backend={registry.get('backend')}) : "
            "un redémarrage perd les outputs, donc la preuve"
        )
    return "ok", detail


def parse_ml(body: dict) -> tuple[str, str]:
    model = body.get("model")
    error = body.get("last_webhook_error")
    detail = f"model={model}, last_webhook_error={error}"
    if model != "ready":
        return "fail", detail + " — modèle non chargé : aucune détection possible"
    if error:
        return "fail", detail + " — le webhook de remédiation a échoué"
    return "ok", detail


def parse_remediation(body: dict) -> tuple[str, str]:
    state = body.get("state")
    if not state:
        return "fail", "pas d'état publié par le bus"
    return "ok", f"state={state}, seq={body.get('seq')}"


def parse_waf(body: dict) -> tuple[str, str]:
    deception = body.get("deception")
    detail = f"deception={deception}, upstream={body.get('upstream')}"
    if deception is None:
        return "warn", detail + " — déception non publiée"
    return "ok", detail


def parse_backoffice(body: dict) -> tuple[str, str]:
    face = body.get("face_engine")
    detail = (
        f"face_engine={face}, bootstrap_required={body.get('bootstrap_required')}, "
        f"db={body.get('db')}"
    )
    if face == "stub":
        return "warn", detail + " — moteur facial STUB (démo uniquement, pas de reconnaissance réelle)"
    if body.get("status") != "ok":
        return "warn", detail + " — service dégradé"
    return "ok", detail


def parse_rag(body: dict) -> tuple[str, str]:
    index = body.get("index") or {}
    generation = body.get("generation") or {}
    detail = (
        f"{index.get('documents')} document(s), ollama={generation.get('ollama')}, "
        f"modèle={generation.get('model')}"
    )
    if body.get("status") != "ok":
        return "warn", detail + " — index absent, réponses en mode dégradé"
    return "ok", detail


def parse_shadow(body: dict) -> tuple[str, str]:
    return "ok", f"service_token_configured={body.get('service_token_configured')}"


def probe_shop(timeout: float) -> tuple[str, str]:
    """Deux appels : la santé, puis le catalogue (>= 6 articles illustrés)."""
    status, health = fetch_json("http://127.0.0.1:8087/healthz", timeout)
    if status != 200:
        return "fail", f"HTTP {status} sur /healthz"
    base = f"healthz={health.get('status')}, base={health.get('database')}"
    status, catalogue = fetch_json("http://127.0.0.1:8087/products", timeout)
    if status != 200:
        return "fail", base + f" ; /products HTTP {status}"
    products = catalogue.get("products") or []
    illustrated = [p for p in products if str(p.get("image_url") or "").strip()]
    detail = f"{base} ; {len(products)} article(s), {len(illustrated)} avec image_url"
    if len(products) < 6 or len(illustrated) < 6:
        return "warn", detail + " — attendu au moins 6 articles illustrés"
    return "ok", detail


# P0 = la chaîne montrée au jury. Son absence casse la démonstration : échec.
# P1 = confort (boutique, back-office, RAG, logs, honeypot) : avertissement.
SERVICES = (
    {"id": "payment", "label": "service P0 : payment-api (/healthz)", "blocking": True,
     "probe": probe_json("http://127.0.0.1:8080/healthz", parse_payment)},
    {"id": "ml", "label": "service P0 : ml-detector (/healthz)", "blocking": True,
     "probe": probe_json("http://127.0.0.1:8083/healthz", parse_ml)},
    {"id": "remediation", "label": "service P0 : remediation-engine (/state)", "blocking": True,
     "probe": probe_json("http://127.0.0.1:8084/state", parse_remediation)},
    {"id": "waf", "label": "service P0 : waf (/healthz)", "blocking": True,
     "probe": probe_json("http://127.0.0.1:8085/healthz", parse_waf)},
    {"id": "prometheus", "label": "service P0 : prometheus (9090/-/ready)", "blocking": True,
     "probe": probe_status("http://127.0.0.1:9090/-/ready")},
    {"id": "alertmanager", "label": "service P0 : alertmanager (9093/-/ready)", "blocking": True,
     "probe": probe_status("http://127.0.0.1:9093/-/ready")},
    {"id": "grafana", "label": "service P0 : grafana (3001/api/health)", "blocking": True,
     "probe": probe_json("http://127.0.0.1:3001/api/health", lambda body: (
         "ok" if body.get("database") == "ok" else "fail",
         f"database={body.get('database')}, version={body.get('version')}",
     ))},
    {"id": "backoffice", "label": "service P1 : backoffice (/healthz)", "blocking": False,
     "probe": probe_json("http://127.0.0.1:8086/healthz", parse_backoffice)},
    {"id": "shop", "label": "service P1 : shop-api (/healthz + /products)", "blocking": False,
     "probe": probe_shop},
    {"id": "rag", "label": "service P1 : rag-assistant (/healthz)", "blocking": False,
     "probe": probe_json("http://127.0.0.1:8091/healthz", parse_rag)},
    {"id": "loki", "label": "service P1 : loki (3100/ready)", "blocking": False,
     "probe": probe_status("http://127.0.0.1:3100/ready")},
    {"id": "shadow", "label": "service P1 : shadow-api (/healthz)", "blocking": False,
     "probe": probe_json("http://127.0.0.1:8090/healthz", parse_shadow)},
)


def safe_probe(spec: dict, timeout: float) -> tuple[str, str]:
    """Une sonde ne doit JAMAIS faire tomber le pré-vol."""
    try:
        return spec["probe"](timeout)
    except ProbeError as exc:
        return "fail", str(exc)
    except Exception as exc:  # noqa: BLE001 - filet de sécurité assumé
        return "fail", f"{type(exc).__name__}: {exc}"[:160]


# ---------------------------------------------------------------------------
# `.env` et secrets
# ---------------------------------------------------------------------------
def read_env(path: pathlib.Path) -> dict[str, str]:
    """Lit un `.env` sans dépendance. Ne journalise jamais rien."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def compose_block(service: str) -> str:
    """Bloc YAML d'un service, sans PyYAML (lecture textuelle suffisante)."""
    path = ROOT / "docker-compose.yml"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        rf"^  {re.escape(service)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:\s*$|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else ""


def make_check(label: str, status: str, detail: str, blocking: bool) -> dict:
    return {
        "label": label,
        "status": status,
        "detail": detail,
        "blocking": blocking,
    }


def secret_checks() -> list[dict]:
    checks: list[dict] = []
    env_path = ROOT / ".env"
    exists = env_path.is_file()
    checks.append(make_check(
        "secrets : .env présent",
        "ok" if exists else "fail",
        ".env trouvé" if exists else "absent — copier .env.example puis lancer tools/init_env.py",
        blocking=True,
    ))
    values = read_env(env_path) if exists else {}

    for key in REQUIRED_SECRETS:
        defined = bool(values.get(key, "").strip())
        checks.append(make_check(
            f"secrets : {key}",
            "ok" if defined else "fail",
            "défini" if defined else "non défini — la démonstration échouera (valeur jamais affichée)",
            blocking=True,
        ))

    # --- Cohérence du PIN : c'est celui que le présentateur tapera ----------
    pin = values.get("TERMINAL_PIN", "").strip()
    if exists and pin:
        if pin == "1234":
            checks.append(make_check(
                "secrets : PIN de démonstration",
                "warn",
                "PIN = 1234 : valeur publique connue, à régénérer avec tools/init_env.py",
                blocking=False,
            ))
        elif not re.fullmatch(r"\d{4,8}", pin):
            checks.append(make_check(
                "secrets : format du PIN",
                "warn",
                "format inattendu (4 à 8 chiffres attendus) — vérifier la saisie au terminal",
                blocking=False,
            ))
        else:
            checks.append(make_check(
                "secrets : format du PIN",
                "ok",
                "PIN numérique, longueur attendue",
                blocking=False,
            ))

        # Le PIN n'est utile que s'il ARRIVE dans le conteneur. Un `.env`
        # correct mais un compose qui ne transmet pas la variable = PIN
        # aléatoire à chaque démarrage, invisible jusqu'au moment de taper.
        block = compose_block("payment-api")
        if block:
            forwarded = "TERMINAL_PIN" in block
            checks.append(make_check(
                "secrets : PIN transmis à payment-api",
                "ok" if forwarded else "fail",
                "docker-compose.yml transmet TERMINAL_PIN"
                if forwarded
                else "docker-compose.yml ne transmet PAS TERMINAL_PIN à payment-api : "
                     "le conteneur tirera un PIN aléatoire. Ajouter "
                     "`TERMINAL_PIN: ${TERMINAL_PIN:-}` à son bloc `environment`.",
                blocking=True,
            ))

    return checks


# ---------------------------------------------------------------------------
# Dépôt git
# ---------------------------------------------------------------------------
def git(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # Dépôt sur un partage réseau lent, git absent, timeout : on renvoie un
        # code d'erreur plutôt que de laisser remonter une exception.
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=124, stdout="", stderr=str(exc),
        )


def git_checks() -> list[dict]:
    checks: list[dict] = []
    probe = git("rev-parse", "--is-inside-work-tree")
    if probe.returncode != 0:
        return [make_check(
            "dépôt : git disponible", "warn", "pas un dépôt git : contrôles de livraison sautés",
            blocking=False,
        )]

    status = git("status", "--short")
    lines = [line for line in status.stdout.splitlines() if line.strip()]
    checks.append(make_check(
        "dépôt : git status propre",
        "ok" if not lines else "warn",
        "aucune modification" if not lines
        else f"{len(lines)} fichier(s) modifié(s)/non suivi(s) — ne pas livrer un dépôt sale",
        blocking=False,
    ))

    tracked_env = git("ls-files", "--error-unmatch", ".env")
    env_tracked = tracked_env.returncode == 0
    checks.append(make_check(
        "dépôt : .env non suivi",
        "fail" if env_tracked else "ok",
        "`.env` est SUIVI par git — retirer du suivi (git rm --cached .env)" if env_tracked
        else "`.env` hors du dépôt",
        blocking=True,
    ))

    # `git grep` ne voit que les fichiers suivis, et renvoie 1 si rien ne
    # correspond. On n'affiche QUE fichier:ligne : jamais la ligne trouvée,
    # qui contient le secret.
    scan = git("grep", "-I", "-n", "-E", SECRET_PATTERN, "--", ".")
    if scan.returncode == 0:
        hits = [line.split(":", 1)[0] for line in scan.stdout.splitlines() if line.strip()]
        checks.append(make_check(
            "dépôt : aucun secret suivi",
            "fail",
            f"{len(hits)} fichier(s) suivi(s) contiennent un motif de secret "
            f"({', '.join(hits[:3])}{'…' if len(hits) > 3 else ''}) — retirer avant livraison",
            blocking=True,
        ))
    elif scan.returncode == 1:
        checks.append(make_check(
            "dépôt : aucun secret suivi", "ok",
            "aucun jeton Vault ni clé privée dans les fichiers suivis", blocking=True,
        ))
    else:
        checks.append(make_check(
            "dépôt : aucun secret suivi", "warn",
            f"git grep a échoué (code {scan.returncode}) : contrôle non concluant", blocking=False,
        ))
    return checks


# ---------------------------------------------------------------------------
# Exécution
# ---------------------------------------------------------------------------
def run_checks(timeout: float) -> list[dict]:
    checks = secret_checks() + git_checks()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [(spec, pool.submit(safe_probe, spec, timeout)) for spec in SERVICES]
        for spec, future in futures:
            status, detail = future.result()
            checks.append(make_check(spec["label"], status, detail, spec["blocking"]))
    return checks


def effective_status(check: dict) -> str:
    """État affiché : un échec non bloquant est un AVERTISSEMENT.

    La distinction est le cœur de l'outil : le RAG absent n'empêche pas de
    démontrer la chaîne de paiement, un service P0 absent si.
    """
    if check["status"] == "fail" and not check["blocking"]:
        return "warn"
    return check["status"]


def render_table(checks: list[dict]) -> None:
    headers = ("Contrôle", "État", "Détail")
    rows = [(c["label"], LABELS[effective_status(c)], c["detail"]) for c in checks]
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    widths[2] = min(widths[2], 78)

    def shorten(cell: str, width: int) -> str:
        return cell if len(cell) <= width else cell[: width - 1] + "…"

    sep = "+" + "+".join("-" * (width + 2) for width in widths) + "+"

    def line(cells: tuple[str, str, str]) -> str:
        padded = [shorten(cell, width).ljust(width) for cell, width in zip(cells, widths)]
        return "| " + " | ".join(padded) + " |"

    print(sep)
    print(line(headers))
    print(sep)
    for row in rows:
        print(line(row))
    print(sep)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Vérifie que le SI est démontrable (services, secrets, dépôt).",
    )
    parser.add_argument("--json", action="store_true", help="sortie machine (JSON uniquement)")
    parser.add_argument("--timeout", type=float, default=3.0,
                        help="délai HTTP par sonde, en secondes (défaut : 3)")
    args = parser.parse_args(argv)

    started = time.time()
    checks = run_checks(args.timeout)
    counts = {state: sum(1 for c in checks if effective_status(c) == state) for state in LABELS}
    blocking_failures = [c for c in checks if c["status"] == "fail" and c["blocking"]]
    demonstrable = not blocking_failures

    if args.json:
        print(json.dumps({
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "root": str(ROOT),
            "duration_seconds": round(time.time() - started, 2),
            "demonstrable": demonstrable,
            "counts": counts,
            "blocking_failures": len(blocking_failures),
            "checks": checks,
        }, ensure_ascii=False, indent=2))
        return 0 if demonstrable else 1

    print()
    print("Pré-vol de soutenance — RESILIENCE-ALPHA / SHADOW-SETTLE")
    print(f"Dépôt : {ROOT}")
    print()
    render_table(checks)
    print()
    print(f"Bilan : {counts['ok']} OK, {counts['warn']} avertissement(s), "
          f"{counts['fail']} échec(s) — en {time.time() - started:.1f} s")

    warnings = [c for c in checks if effective_status(c) == "warn"]
    if warnings:
        print()
        print("Avertissements (non bloquants) :")
        for check in warnings:
            print(f"  - {check['label']} : {check['detail']}")

    if blocking_failures:
        print()
        print(f"NON DÉMONTRABLE : {len(blocking_failures)} contrôle(s) bloquant(s) en échec.")
        for check in blocking_failures:
            print(f"  - {check['label']} : {check['detail']}")
        print("Corriger les contrôles BLOQUANTS ci-dessus, puis relancer ce script.")
        if any(c["label"].startswith("service P0") for c in blocking_failures):
            print("Rappel : `docker compose up -d`, attendre ~30 s, puis relancer.")
        return 1

    print()
    print("DÉMONTRABLE : les contrôles bloquants sont verts"
          + (" (voir les avertissements ci-dessus)." if warnings else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
