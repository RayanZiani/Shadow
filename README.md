# RESILIENCE-ALPHA — SHADOW-SETTLE

Système de paiement fictif à confidentialité cryptographique, avec détection
d'anomalies par machine learning et remédiation autonome. Workshop M2 2026.

**MVP complet** : les cinq piliers sont branchés les uns aux autres et la
chaîne tourne de bout en bout sous Docker, sans intervention humaine.

## Ce qui est réellement opérationnel

| Brique | État | Preuve |
|---|---|---|
| `monero-crypto-core` | **bLSAG réel**, key image anti-double-dépense, engagements de Pedersen, audit view-only | 8 tests Rust + 10 tests HTTP |
| `payment-api` | Registre d'outputs chiffrés, leurres gamma, anti-double-dépense, PIN, sonde | `tools/e2e_demo.py`, 10 tests |
| `ml-detector` | Modèle entraîné sur capture réelle, seuil appris, anti-rebond, diagnostic | `threshold.json`, 11 tests |
| `remediation-engine` | Bus d'états SSE, remédiation asynchrone, verrou, Ansible réel dans l'image | 12 tests |
| `waf` | Reverse proxy + **déception active** branchée sur le honeypot | 10 tests |
| `shadow-api` | Honeypot borné, journal d'interception consultable | 4 tests |
| `auth-service` | Clé JWT lue dans **Vault** (KV v2), fallback explicite sinon | 5 tests |
| `front-dashboard` | 4 quadrants, anneau SVG, pavé PIN, score ML, audit view-only | `npm run build` |
| `hardware/bridge` | Pont SSE → USB, reconnexion, mode répétition | 9 tests + `--dry-run` |
| `hardware/ring_signer_hsm` | Firmware ESP32, 5 états, parseur de trame | à flasher |
| Observabilité | Prometheus (9 métriques/services, 11 alertes) + Grafana provisionné | `localhost:3001` |

**Chaîne P0 mesurée sur Docker** : attaque → détection → remédiation →
transaction de preuve → déception active en **6,5 à 7,0 s** (limite 90 s,
marge ≈ 83 s), sur trois exécutions consécutives.

## Démarrage

Guide complet (Docker, Rust, Python, Node, Vault, dépannage) :
[`docs/INSTALLATION-LOCAL.md`](docs/INSTALLATION-LOCAL.md).
Runbook de la soutenance : [`docs/DEMO.md`](docs/DEMO.md).

```bash
cp .env.example .env
docker compose build      # ~6-8 min la première fois (torch, cargo)
docker compose up -d
docker compose ps         # 14 services, tous Up / healthy
```

| Accès | URL |
|---|---|
| Dashboard | http://localhost:5173 |
| Grafana (admin / admin au 1er démarrage) | http://localhost:3001 |
| Prometheus | http://localhost:9090 |
| API de paiement | http://localhost:8080 |
| WAF (point d'entrée attaquant) | http://localhost:8085 |

## La chaîne, en une commande

```bash
python tools/e2e_demo.py
```

Lance une vraie attaque **via le WAF**, attend la détection et la remédiation,
vérifie qu'une transaction client passe ensuite, et que l'attaquant est
**dérouté vers le honeypot**. Code de sortie 0 = chaîne complète.

```bash
python tools/attack.py poison     # PC Attaquant : les 4 familles d'attaques
python tools/attack.py list
```

## Le pipeline ML, en une page

**Le piège du sujet** : « une détection par machine learning, et non par de
simples seuils fixes ». Un seuil codé en dur est éliminatoire dans l'esprit du
jury. Ici, tout est appris :

1. `features.py` calcule 6 features, **une seule définition** partagée entre
   l'entraînement et l'inférence (c'était le défaut majeur du squelette :
   entraînement sur des distributions inventées, inférence sur des zéros).
2. `train.py` apprend un `StandardScaler`, un `IsolationForest` et un
   autoencoder PyTorch, puis fixe le seuil sur des fenêtres **hors
   échantillon**.
3. `detector.py` compare le score à ce seuil appris et exige **3 fenêtres
   consécutives** anormales avant de déclencher.

### Ce que la mesure a réellement montré

Quatre erreurs de conception ont été trouvées en recette, et corrigées :

| Défaut | Symptôme | Correction |
|---|---|---|
| Entropie calculée sur les âges **poolés** de la fenêtre | L'entropie passait de 3,0 à 5,4 bits à distribution identique, juste en changeant la cadence | Calcul **par événement** puis moyenne (taille d'anneau constante) |
| `inter_arrival` en écart-type | Une pause de 6 s suffisait à produire un score de 6,2 | **Médiane** des intervalles |
| Sonde de liveness dans la télémétrie | Cadence fixe de 4 s, distribution biaisée | Champ `synthetic`, exclu du calcul |
| Entropie comme feature principale | AUC 0,56 : elle sature à 10 leurres par transaction | Remplacée par l'**âge médian des leurres** (AUC 1,00) |

`analyze_separation.py` mesure l'AUC de chaque feature sur 1 140 fenêtres :

| Feature | AUC (leurres empoisonnés) |
|---|---|
| `decoy_median_age_days` | **1,00** (séparation parfaite) |
| `ring_age_skewness` | 0,94 (complémentaire) |
| les 4 autres | 0,50 (bruit pour cette attaque, utiles aux autres) |

Justification physique : un wallet Monero tire ses leurres selon une gamma
biaisée vers les outputs récents — **3,67 j ± 0,16** mesuré sur ce système. Un
attaquant qui empoisonne les leurres les tire uniformément dans le registre —
**18,9 j ± 1,4**. Aucun chevauchement. C'est un résultat honnête à assumer en
Q&A : une seule feature discrimine l'attaque principale, et elle est
physiquement justifiée.

## La cryptographie, en une page

`monero-crypto-core` implémente un modèle Monero **simplifié mais réel** sur
Ristretto255 :

| Primitive | Implémentation |
|---|---|
| Adresse one-time | `P = H_s(rA)G + B`, `R = rG` (ECDH) |
| Clé dérivée par le porteur | `x = H_s(aR) + b` |
| Key image (anti-double-dépense) | `I = x·H_p(P)`, hash-**vers-le-groupe** (Elligator) : personne ne connaît le log discret, donc pas de forge |
| Signature d'anneau liée | **bLSAG** sur `n` clés, message lié aux engagements |
| Engagements | `C = aG + rH`, homomorphes, aveugles sommant à zéro |
| Confidentialité du montant | masque `Keccak("amount" ‖ shared)` appliqué au montant |
| Audit **view-only** | `GET /audit/last` : lit le montant **sans** la Spend Key |

Deux écarts assumés, publiés dans `/healthz` (`not_implemented`) et dans le
dossier de gouvernance :

- **CLSAG** au lieu de bLSAG → signature ~2× plus grosse, même modèle de
  sécurité ;
- **pas de Bulletproofs** → un montant négatif modulo l pourrait passer la
  vérification d'équilibre.

## Tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
./tools/run_tests.sh
```

**79 tests** : bus d'états et flux d'incident (12), détecteur ML (11), API de
paiement (10), noyau Rust unitaire (8) et HTTP (10), WAF (10), pont hardware
(9), honeypot (4), auth-service (5).

## Ce qui reste à faire

- **Bulletproofs** (preuves de domaine) : seule vraie faille cryptographique
  restante. Priorité 1 du dossier de gouvernance.
- **AppRole Vault** : le playbook utilise aujourd'hui un token de service.
- **Registre persistant** : les outputs sont en mémoire (perdus au redémarrage).
- **RAG Ollama** : non commencé (pilier IA/DATA partiel).
- **Objet physique** : firmware à flasher, modèle 3D v1 jamais imprimé.
- **Recalibrage ML** sur le PC hôte de démonstration (7 min, obligatoire).

Détail complet, avec chiffres et justifications :
[`docs/COMPTE-RENDU.md`](docs/COMPTE-RENDU.md) et
[`docs/GOUVERNANCE.md`](docs/GOUVERNANCE.md).

## Priorités

- **P0** — chaîne attaque → ML → Ansible → preuve → déception : **fait et mesuré**.
- **P1** — honeypot, WAF, observabilité, dashboard, dossier EBIOS/PCA-PRA/DORA :
  **fait**.
- **P2** — hardware, RAG Ollama. L'objet physique ne doit **jamais** être
  nécessaire à la démo.

## Objet physique

Rôle, scénario de démonstration, contrat d'état et câblage :
[`docs/hardware/CONCEPT-PHYSIQUE.md`](docs/hardware/CONCEPT-PHYSIQUE.md).
Montage et impression : [`hardware/README.md`](hardware/README.md).

## Sécurité du livrable

Aucun secret en clair dans le ZIP (règle éliminatoire). `.gitignore` couvre
`.env`, `secrets/`, les clés et les artefacts de build. Vérifier avant de
zipper :

```bash
git secrets --scan
grep -rn "hvs\.\|BEGIN.*PRIVATE KEY" --include="*.py" --include="*.yml" --include="*.md" .
```
