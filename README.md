# RESILIENCE-ALPHA — SHADOW-SETTLE

Système de paiement fictif à confidentialité cryptographique, avec détection
d'anomalies par machine learning et remédiation autonome. Workshop M2 2026.

## Ce qui est réellement opérationnel

| Brique | État | Preuve |
|---|---|---|
| `payment-api` | Fonctionnel : registre d'outputs, leurres gamma, anti-double-dépense, PIN, sonde | `tools/e2e_demo.py` |
| `ml-detector` | Modèle entraîné, seuil appris, anti-rebond, diagnostic | `threshold.json` (métriques) |
| `remediation-engine` | Bus d'états SSE, remédiation asynchrone, verrou | 7 + 5 tests |
| `front-dashboard` | 4 quadrants, anneau SVG miroir du terminal, SSE | `npm run build` |
| `hardware/bridge` | Pont SSE → USB, reconnexion, mode répétition | `--dry-run` |
| `hardware/ring_signer_hsm` | Firmware ESP32, 5 états, parseur de trame | à flasher |
| `monero-crypto-core` | **Squelette** : stealth-address réel, CLSAG à brancher | voir plus bas |

**Chaîne P0 mesurée** : attaque → détection → remédiation → transaction de
preuve en **12,3 s** (limite fixée à 90 s, marge 78 s). Répétable.

## Démarrage

```bash
cp .env.example .env
docker compose up db-postgres vault -d
docker compose up monero-crypto-core payment-api auth-service -d
docker compose up ml-detector remediation-engine shadow-api -d
docker compose up front-dashboard -d
```

Le modèle ML est entraîné **à la construction de l'image** (jamais en live).
Pour un modèle calibré sur le trafic réel — la seule version défendable devant
un jury :

```bash
cd services/ml-detector
python capture_baseline.py --seconds 300     # trafic normal, ~5 min
python train.py --from-capture baseline.jsonl
docker compose build ml-detector
```

## Répétition générale

```bash
python tools/e2e_demo.py
```

Lance une vraie attaque (leurres empoisonnés), attend la détection et la
remédiation, vérifie qu'une transaction passe ensuite, et chronomètre.
Code de sortie 0 = chaîne complète. À lancer avant chaque soutenance.

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

Sans ces corrections, le détecteur produisait 70 à 100 % de faux
déclenchements sur du trafic normal. Après : **0 % sur 3 fenêtres consécutives**
et 81 % de rappel sur les 4 familles d'attaques.

`analyze_separation.py` mesure l'AUC de chaque feature et documente lesquelles
portent réellement le signal :

| Feature | AUC (leurres empoisonnés) |
|---|---|
| `decoy_median_age_days` | **1,00** (séparation parfaite) |
| `ring_age_skewness` | 0,92 (complémentaire) |
| `decoy_entropy` (abandonnée) | 0,56 |
| les 3 autres | 0,50 (bruit pour cette attaque) |

Justification physique : un wallet Monero tire ses leurres selon une gamma
biaisée vers les outputs récents — **3,56 j ± 0,67** mesuré sur ce système. Un
attaquant qui empoisonne les leurres les tire uniformément dans le registre —
**31,1 j ± 12,7**. Aucun chevauchement. C'est un résultat honnête à assumer en
Q&A : une seule feature discrimine l'attaque principale, et elle est
physiquement justifiée.

## Ce qui reste à faire

- **`monero-crypto-core`** : `ring-sign` / `ring-verify` / `audit-view` sont des
  placeholders. C'est la brique la plus lourde et la plus différenciante
  (CLSAG + engagements de Pedersen + View Key séparée). Priorité absolue.
- `auth-service` : la clé JWT doit être lue dans Vault, pas dans une variable
  d'environnement.
- `remediation_playbook.yml` : retirer les `ignore_errors` au fur et à mesure
  que chaque endpoint admin existe réellement.

## Priorités

- **P0** — chaîne attaque → ML → Ansible → preuve : **fait et mesuré**.
- **P1** — honeypot, WAF/SIEM, dashboard, dossier EBIOS/PCA-PRA/DORA.
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
```
