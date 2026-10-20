#!/usr/bin/env bash
# Lance TOUS les tests du dépôt, service par service.
#
#   ./tools/run_tests.sh          # tout
#   ./tools/run_tests.sh --fast   # sans l'entraînement/validation ML
#
# Prérequis : un venv à la racine (voir README « Tests ») et, pour le noyau
# cryptographique, cargo (rustup). Le script s'arrête au premier échec et
# affiche un récapitulatif.
#
# Pourquoi service par service (et pas `pytest services/`) : chaque service
# expose un `main.py`. Lancés dans le même processus, Python résoudrait
# `import main` vers le premier module homonyme importé, et des tests
# échoueraient pour une raison sans rapport avec le code testé. Ce script
# exécute donc chaque suite dans son propre dossier, comme le ferait la CI.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
FAILED=()
PASSED=()

run() {
  local name="$1"; shift
  printf '\n\033[1m=== %s\033[0m\n' "$name"
  if "$@"; then
    PASSED+=("$name")
  else
    FAILED+=("$name")
  fi
}

if [[ ! -x "$PY" ]]; then
  echo "Python introuvable : $PY"
  echo "Créez le venv :  python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt"
  exit 2
fi

run "remediation-engine (bus d'états)" \
  bash -c "cd '$ROOT/services/remediation-engine' && '$PY' -m pytest test_state_bus.py -q"
run "remediation-engine (flux d'incident)" \
  bash -c "cd '$ROOT/services/remediation-engine' && '$PY' test_incident_flow.py"
run "remediation-engine (ingestion SIEM)" \
  bash -c "cd '$ROOT/services/remediation-engine' && '$PY' -m pytest test_alert_ingest.py -q"
run "ml-detector (features & anti-rebond)" \
  bash -c "cd '$ROOT/services/ml-detector' && '$PY' -m pytest test_detector.py -q"
run "payment-api (modèle de menace)" \
  bash -c "cd '$ROOT/services/payment-api' && '$PY' -m pytest test_payment_api.py -q"
run "shadow-api (honeypot)" \
  bash -c "cd '$ROOT/services/shadow-api' && '$PY' -m pytest test_shadow_api.py -q"
run "auth-service (gestion de la clé JWT)" \
  bash -c "cd '$ROOT/services/auth-service' && '$PY' -m pytest test_auth_service.py -q"
run "backoffice (authentification visage + habilitations)" \
  bash -c "cd '$ROOT/services/backoffice' && '$PY' -m pytest test_backoffice.py -q"
run "shop-api (boutique, panier, paiement, historique)" \
  bash -c "cd '$ROOT/services/shop-api' && '$PY' -m pytest test_shop_api.py -q"
run "rag-assistant (indexation et sources)" \
  bash -c "cd '$ROOT/services/rag-assistant' && '$PY' -m pytest test_rag.py -q"
run "waf (déception active)" \
  bash -c "cd '$ROOT/services/waf' && '$PY' -m pytest test_waf.py -q"
run "hardware (contrat série)" \
  bash -c "cd '$ROOT/hardware/bridge' && '$PY' -m pytest test_hardware_bridge.py -q"

if command -v cargo >/dev/null 2>&1; then
  run "monero-crypto-core (Rust, unitaires)" \
    bash -c "cd '$ROOT/services/monero-crypto-core' && cargo test --release -q"
else
  echo "cargo absent : tests Rust ignorés (installez rustup)"
fi

# Tests d'intégration qui parlent aux services réels. Ils se déclarent ignorés
# si la pile n'est pas démarrée : `docker compose up -d`.
run "monero-crypto-core (bLSAG + audit, HTTP)" \
  bash -c "cd '$ROOT/services/monero-crypto-core' && '$PY' -m pytest test_crypto_flow.py -q"

if [[ "${1:-}" != "--fast" ]]; then
  run "ml-detector (artefacts présents)" \
    bash -c "cd '$ROOT/services/ml-detector' && '$PY' train.py --check >/dev/null"
fi

printf '\n\033[1m=== Récapitulatif ===\033[0m\n'
for name in "${PASSED[@]:-}"; do [[ -n "$name" ]] && printf '  OK    %s\n' "$name"; done
for name in "${FAILED[@]:-}"; do [[ -n "$name" ]] && printf '  ECHEC %s\n' "$name"; done

if ((${#FAILED[@]})); then
  exit 1
fi
echo "  Tout est vert."
