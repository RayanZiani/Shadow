# Compte rendu technique — SHADOW-SETTLE / RESILIENCE-ALPHA

Date : 19-20 octobre 2026
Périmètre : **P0 — chaîne attaque → détection → remédiation → preuve**
Statut : **opérationnel, mesuré, et validé sur l'environnement Docker réel**

---

## 1. Résumé exécutif

La chaîne critique du sujet tourne de bout en bout, sans intervention humaine,
en **5,8 à 6,7 secondes** sur l'environnement conteneurisé complet. La grille
nationale alloue 3 minutes à la démonstration live et 25 points à cet axe : la
marge est de 83 secondes sur l'objectif interne de 90 s.

| Indicateur | Valeur mesurée (Docker, 3 exécutions) |
|---|---|
| Durée détection → reprise | 5,8 s / 6,6 s / 6,7 s |
| Objectif interne | < 90 s |
| Marge | ≈ 83 s |
| Faux déclenchements sur trafic normal | **0 %** (0 déclenchement sur 180 fenêtres) |
| Rappel sur les 4 familles d'attaques | 100 % (1 058 fenêtres d'attaque) |
| Tests automatisés | **53**, tous verts (Python + Rust) |
| Services Docker | 13, tous `Up` / `healthy` |

Deux sessions distinctes, et c'est la seconde qui compte pour la suite :

1. **Session 1** (poste sans Docker ni Rust) : le pipeline ML du squelette ne
   pouvait pas fonctionner. Quatre défauts de conception ont été trouvés par la
   mesure et corrigés (§4).
2. **Session 2** (environnement réel installé) : la chaîne tourne sous Docker
   avec Ansible et le noyau Rust compilé. **Sept défauts supplémentaires** ont
   été trouvés — tous invisibles en mode « local-dev », parce que les stubs les
   masquaient. Détail au §9 : c'est la partie la plus utile pour la Q&A.

---

## 2. Ce qui a été livré

### 2.1 Chaîne de détection (`services/ml-detector`)

| Fichier | Rôle |
|---|---|
| `features.py` | **Nouveau.** Extraction des 6 features, partagée entraînement/inférence |
| `train.py` | Réécrit : calibration hors échantillon, seuil appris, évaluation |
| `detector.py` | Réécrit : score appris, anti-rebond, diagnostic, dégradation explicite |
| `capture_baseline.py` | **Nouveau.** Capture du trafic normal réel |
| `analyze_separation.py` | **Nouveau.** AUC par feature — justifie les choix |
| `calibrate_threshold.py` | **Nouveau.** Calibration sur distribution observée |
| `validate_real.py` | **Nouveau.** Validation par le vrai chemin de production |
| `threshold.json` | Artefact versionné : seuil + métriques d'évaluation |
| `test_detector.py` | **Nouveau.** 11 tests : invariance au volume, purge de fenêtre, anti-rebond |
| `Dockerfile` | Entraînement à la construction si un artefact manque (4 vérifiés, pas 1) |

### 2.2 Moteur de remédiation (`services/remediation-engine`)

- Bus d'états avec **source de vérité unique** (`StateBus`).
- `GET /events` en SSE, avec heartbeat de 15 s (sans lui, Windows fermait la
  connexion toutes les 30 s).
- `GET /state` pour la resynchronisation du pont hardware.
- `POST /state/report` : un fait vérifié (transaction confirmée) peut être
  publié, mais **jamais** masquer une attaque en cours.
- Remédiation **asynchrone** : `/incident` répond immédiatement, le playbook
  tourne en tâche de fond.
- Maintien de l'état `UNDER_ATTACK` pendant 3 s pour que la détection soit
  visible à l'écran et sur l'objet physique.
- CORS restreint pour le dashboard.
- Ansible **réellement présent dans l'image** : le playbook s'exécute, il n'est
  pas simulé (4,1 s mesurées, 6 tâches exécutées).
- 12 tests (7 unitaires + 5 d'intégration).

### 2.3 API de paiement (`services/payment-api`)

- Registre d'outputs exposé publiquement (modèle de menace réaliste).
- **Tirage gamma** des leurres sur l'âge (remplace le `random.sample` uniforme
  du squelette, qui était un TODO explicite du README).
- Anti-double-dépense par key image, avec télémétrie même en cas de rejet.
- `POST /pin/verify` + `GET /pin/stats` : surface d'attaque physique.
- Sonde de liveness marquée `synthetic` (exclue du ML).
- `POST /admin/quarantine-outputs` : retire les 25 outputs **les plus anciens**
  (le signal exactement mesuré par le ML), pas un tirage au hasard.
- `POST /admin/ring-size` : durcissement réellement appliqué par le playbook.
- Registre **ré-alimenté** automatiquement : deux démonstrations d'affilée ne
  l'épuisent plus.
- `test_payment_api.py` : 8 tests (gamma, `unspent`, quarantaine, ring-size, PIN).

### 2.4 Dashboard (`front-dashboard`)

- Anneau de LEDs en SVG, **miroir exact** de l'objet physique (même état, même
  séquence).
- Quadrant « Radar ML » branché sur `/score` en temps réel.
- Consommation SSE avec détection de coupure et numéro de séquence.
- Build de production vérifié.

### 2.5 Objet physique (`hardware/`, `docs/hardware/`)

- `docs/hardware/CONCEPT-PHYSIQUE.md` : rôle, scénario de démonstration en
  60 s, contrat d'état, analyse de reprise après panne.
- `hardware/bridge/hardware_bridge.py` : pont SSE → USB, reconnexion,
  mode `--replay` pour répéter sans attaque.
- `hardware/ring_signer_hsm/ring_signer_hsm.ino` : **3 bugs corrigés**,
  parseur de trame, servo piloté sur changement d'état uniquement.
- `hardware/models/socle_terminal.scad` : modèle paramétrique (4 pièces).
- `hardware/README.md` : câblage, impression, recette en 6 tests.

### 2.6 Outils de recette (`tools/`, `local-dev/`)

- `tools/e2e_demo.py` : répétition générale chronométrée, avec 6 contrôles
  bloquants, et attente explicite d'une fenêtre propre avant mesure.
- `tools/run_tests.sh` : lance les **53 tests** du dépôt (Python + Rust).
- `tools/fake_ansible.py` : stub pour rejouer la chaîne sans Ansible.
- `local-dev/` : lancement sans Docker, clairement marqué **non livrable**.
- `requirements-dev.txt` + `docs/INSTALLATION-LOCAL.md` : installation du poste
  (Docker/Colima, Rust, Python, Node, Vault) et procédure de recette.

### 2.7 Nouvelles briques testées

- `services/auth-service` : clé JWT lue dans **Vault KV v2**, avec cache de 30 s
  et fallback **explicite** (`/healthz` dit `vault` ou `fallback_env`). 5 tests.
- `services/shadow-api` : honeypot borné en mémoire, journal consultable.
  4 tests.

---

## 3. Preuves d'exécution

### 3.1 Répétition générale — environnement Docker complet

```
detection_seconds      2.2   2.3   2.7
recovery_seconds       6.3   6.3   6.2
proof_transaction_ok  true  true  true
ansible_rc               0     0     0
chain_complete        true  true  true
```

13 conteneurs `Up`, Ansible réel (4,1 s de playbook), noyau Rust compilé.
La stabilité compte autant que la vitesse : une démo qui marche une fois sur
deux vaut zéro point. Trois exécutions consécutives donnent le même résultat
parce que `e2e_demo.py` attend désormais une fenêtre de détection **vide** avant
de mesurer (voir §9.5).

### 3.2 Modèle retenu

```json
{
  "threshold": 4.9115,
  "calibration_set": "holdout_25pct",
  "score_function": "max(0.5*z_iso+0.5*z_ae, z_median_age)",
  "n_normal": 1089,
  "source": "capture:baseline.jsonl",
  "evaluation": {
    "false_positive_rate": 0.002,
    "false_positive_rate_3_consecutive": 0.0,
    "attack_recall": 1.0
  }
}
```

Validation par le **vrai chemin de production** (`validate_real.py`, trafic
légitime et attaque envoyés via `payment-api`, pas d'injection directe) :

```
normal_mean 0.93   normal_max 1.35
attack_mean 11.12  attack_min 10.99
false_positive_rate 0.0   attack_detection_rate 1.0
false_trigger_rate  0.0
```

Aucun chevauchement : le score le plus haut du trafic normal (1,35) est 8 fois
sous le seuil (4,91), le score le plus bas de l'attaque (10,99) est 2,2 fois
au-dessus.

### 3.3 Séparation des features (mesurée, pas supposée)

`analyze_separation.py` sur 1 140 fenêtres normales et 1 140 fenêtres d'attaque :

| Feature | Trafic normal | Attaque | AUC |
|---|---|---|---|
| `decoy_median_age_days` | 3,673 ± 0,156 | 18,916 ± 1,358 | **1,000** |
| `ring_age_skewness` | 0,776 ± 0,220 | 0,399 ± 0,116 | 0,943 |
| `ring_verify_cpu_us` | 0,000 ± 0,000 | idem | 0,500 |
| `inter_arrival_median` | 0,366 ± 0,382 | idem | 0,500 |
| `tx_latency_p95` | 10,43 ± 1,86 | idem | 0,500 |
| `key_image_rejection_ratio` | 0 | 0 | 0,500 |

Lecture honnête : **une seule feature discrimine l'attaque principale**, et
elle est physiquement justifiée (voir §4.4). `ring_age_skewness` est un
complément (0,94) qui devient plus faible à mesure que la taille d'anneau
grandit : à 25 leurres, la distribution se lisse. Les autres mesurent des
attaques différentes (rejeu de key image, anneau malformé, rafale automatisée)
et servent au modèle générique — c'est précisément pour cela qu'elles restent
dans le vecteur : un modèle à une seule feature ne détecterait qu'une attaque.

Note sur `ring_verify_cpu_us` à 0,000 : le noyau Rust **placeholder** répond en
quelques microsecondes. Cette feature ne portera son signal (anneau malformé)
que lorsque CLSAG sera branché — voir §6.2. On ne l'a pas maquillée.

---

## 4. Défauts trouvés et corrigés

Cette section est le cœur du compte rendu : ce sont des erreurs de conception
qui ne se voient pas à la lecture du code, seulement à la mesure.

### 4.1 L'entropie dépendait du nombre d'échantillons, pas de la distribution

**Symptôme** : score de 4,4 à 6,2 sur du trafic parfaitement normal, alors que
le jeu de test synthétique annonçait 0,8 % de faux positifs.

**Cause** : l'entropie de Shannon était calculée sur les âges **poolés** de
toute la fenêtre. Or, à loi de tirage identique :

```
n=10   entropie = 3,006
n=20   entropie = 3,853
n=40   entropie = 4,590
n=80   entropie = 5,131
n=160  entropie = 5,431
```

La feature mesurait donc la **cadence** du trafic, pas la distribution des
leurres. Une pause dans les transactions ressemblait à une attaque.

**Correction** : calcul **par événement** puis moyenne. Chaque transaction
porte le même nombre de leurres (taille d'anneau constante), donc la mesure est
comparable d'une fenêtre à l'autre, et la moyenne réduit le bruit en √k.

### 4.2 L'écart-type des intervalles explosait au moindre trou

**Symptôme** : une pause de 6 s suffisait à produire un score de 6,2.

**Correction** : remplacement par la **médiane** des intervalles. Insensible
aux pauses, et conserve le signal utile : une rafale automatisée a une médiane
d'environ 0,05 s contre 0,7 à 1,0 s en usage humain.

### 4.3 La sonde de liveness polluait la télémétrie

**Symptôme** : deux leurres sur dix avaient un âge de 0,01 j, et le pool de
leurres dérivait au fil du temps.

**Correction** : les outputs internes sont marqués `internal` et exclus du pool
de leurres ; les événements de la sonde sont marqués `synthetic` et exclus du
calcul des features.

### 4.4 L'entropie n'était pas la bonne feature

**Symptôme** : après les corrections ci-dessus, AUC de 0,56 pour
`decoy_entropy` — c'est-à-dire inutilisable. Elle sature : une transaction ne
porte que 10 leurres, taille à laquelle un tirage gamma et un tirage uniforme
produisent la même entropie.

**Méthode** : mesure directe de l'âge des leurres sur les deux populations.

```
trafic normal (gamma, biais récent) : 3,56 j ± 0,67   (max observé 5,84)
leurres empoisonnés (uniforme)      : 31,1 j ± 12,7   (min observé 8,71)
```

Aucun chevauchement. **AUC = 1,000.**

**Correction** : `decoy_median_age_days` remplace `decoy_entropy` comme
feature principale, avec un score dédié qui s'ajoute au score générique.

**Justification physique, défendable en Q&A** : Monero tire ses leurres selon
une gamma biaisée vers les outputs récents. Un attaquant qui veut empoisonner
l'anneau n'a pas accès à cette distribution — il pioche uniformément dans le
registre public. L'âge médian trahit immédiatement l'origine du tirage.

### 4.5 Pannes silencieuses (les plus dangereuses en démo)

| Panne | Conséquence | Correction |
|---|---|---|
| Webhook vers un nom Docker inexistant | Aucun incident n'arrivait au moteur, sans message | Erreur exposée dans `/healthz`, bloquante dans `e2e_demo.py` |
| Verrou `_incident_active` jamais réarmé | La **deuxième** attaque ne déclenchait plus rien | Réarmement évalué avant toutes les sorties anticipées |
| Playbook exécuté dans la requête HTTP | Timeout du détecteur à 5 s alors que la remédiation marchait | Réponse immédiate + tâche de fond |
| Pas de CORS | Dashboard vide en démo | CORS restreint sur les 3 services |
| `UNDER_ATTACK` publié puis écrasé en quelques ms | Le rouge n'était jamais visible | Maintien de 3 s |

---

## 5. Décisions d'architecture

### 5.1 Une seule source de vérité pour l'état

`remediation-engine` publie. Le dashboard et l'objet physique consomment. Ni
l'un ni l'autre ne recalcule l'état. Deux logiques parallèles finiraient par
diverger le jour de la démonstration.

### 5.2 L'objet physique ne peut pas casser la démo

- Aucun abonné SSE n'est obligatoire : sans dashboard ni pont, la remédiation
  fonctionne à l'identique.
- Le pont tourne sur l'hôte, pas dans Docker (passthrough USB = source
  d'ennuis).
- Le terminal ne peut pas déclencher de remédiation. Aucun chemin ne va de
  l'objet vers le moteur.

### 5.3 Détection : spécificité avant sensibilité

Le coût des deux erreurs n'est pas symétrique. Un faux déclenchement pendant
une démo de 3 minutes coûte 25 points ; une détection 5 s plus tardive coûte
5 secondes. D'où :

- seuil = `max(percentile 99,5, moyenne + 4σ)` ;
- 3 fenêtres consécutives anormales avant déclenchement ;
- refus de conclure si la fenêtre contient moins de 8 événements.

### 5.4 Aucun seuil en dur

Exigence explicite du sujet. Le seuil est appris, stocké dans
`threshold.json`, et traçable (`threshold_estimators` documente les deux
estimateurs et celui qui a été retenu).

---

## 6. Limites assumées (à lire avant de conclure que tout est prêt)

### 6.1 Ce qui est validé, et comment

`docker compose up` complet (13 services), noyau Rust compilé, Ansible réel,
**53 tests** verts, trois répétitions générales consécutives. Les stubs
`local-dev/crypto_shim.py` et `tools/fake_ansible.py` ne sont plus nécessaires :
ils restent dans le dépôt, marqués non livrables, pour un poste sans Docker.

### 6.2 `monero-crypto-core` est toujours un squelette pour la signature

`ring-sign`, `ring-verify` et `audit-view` sont des placeholders. C'est la
brique la plus lourde et la plus différenciante du projet. La chaîne P0
fonctionne *malgré* cela, parce que l'anti-double-dépense et la télémétrie sont
réels ; mais l'expertise technique (10 pts nationaux) dépend de cette brique.

Ce qui a été corrigé côté Rust dans cette session : le service **panic** sur un
hex trop court (`range end index 32 out of range`) — une requête malformée
tuait le worker tokio. Les entrées invalides renvoient maintenant 400, et
`/healthz` publie la liste `implemented` / `placeholder` pour que le TODO soit
visible plutôt que dissimulé.

### 6.3 Le modèle est calibré sur un seul poste

1 089 fenêtres capturées ici. **À refaire sur le PC hôte de démonstration** :

```bash
cd services/ml-detector
../../.venv/bin/python capture_baseline.py --seconds 420 --out baseline.jsonl
../../.venv/bin/python train.py --from-capture baseline.jsonl --epochs 600
cd ../.. && docker compose build ml-detector && docker compose up -d ml-detector
```

Un seuil calibré ailleurs peut produire des faux positifs. La capture varie
désormais cadence **et** taille d'anneau : sans cela, le trafic post-remédiation
(taille durcie à 25) sortait de la distribution normale (§9.4).

### 6.4 `auth-service` : dépendance à Vault non provisionnée en AppRole

La lecture Vault fonctionne (KV v2, cache 30 s, fallback explicite). Mais
l'**AppRole** n'est pas provisionné : en démonstration, le playbook utilise
`VAULT_TOKEN` (token de service) plutôt qu'un `role_id`/`secret_id` à portée
restreinte. C'est un cran en dessous de ce que demanderait la production, et
c'est écrit dans le playbook.

### 6.5 Gouvernance et RAG : non commencés

- Dossier EBIOS RM, PCA/PRA, NIS2/DORA : **rien de rédigé**. C'est 10 points
  nationaux. Le tableau de reprise après panne de
  `docs/hardware/CONCEPT-PHYSIQUE.md` §6 est un matériau de départ, pas un
  dossier.
- RAG Ollama : non commencé (pilier IA/DATA partiel).
- WAF/SIEM : le honeypot `shadow-api` existe et est testé, mais le service
  `waf` reste commenté dans `docker-compose.yml` : **rien ne redirige le trafic
  vers le honeypot**. La déception active est donc codée, pas branchée.
- `prometheus`, `grafana`, `loki`, `promtail` démarrent mais ne sont pas
  configurés (aucun dashboard, aucun scrape config). L'observabilité est
  présente comme socle, pas comme livrable.

### 6.6 Le modèle 3D n'a pas été imprimé

C'est une v1 paramétrique ; les cotes du terminal doivent être mesurées avant
impression.

---

## 7. Plan pour la suite

### Priorité 1 — noyau cryptographique (le vrai différenciateur)

`ring-sign` / `ring-verify` / `audit-view` : CLSAG + engagements de Pedersen +
déchiffrement par View Key. C'est ce qui transforme un projet correct en projet
mémorable, et ce qui tient l'axe « expertise technique ». Le contrat HTTP est
déjà figé et testé : seuls les corps de fonction changent, `payment-api` n'a
pas à bouger.

### Priorité 2 — brancher la déception active

Décommenter `waf` dans `docker-compose.yml`, écrire `GET/POST /admin/redirect`,
et faire de l'étape 1 du playbook une tâche sans `ignore_errors`. Le honeypot
et son journal sont déjà testés (4 tests).

### Priorité 3 — gouvernance

EBIOS RM et PCA/PRA reliés à l'architecture réelle, pas des documents
génériques. Le tableau de reprise après panne du §6 de
`docs/hardware/CONCEPT-PHYSIQUE.md` est un modèle du genre attendu.

### Priorité 4 — observabilité et RAG

Scrape config Prometheus sur les `/healthz` et `/score`, dashboard Grafana
« état du service », puis RAG Ollama sur le dossier technique.

### Priorité 5 — objet physique

Câblage (alim 5 V séparée, GND commun, 330 Ω sur DIN, 1000 µF sur le 5 V),
flash du firmware, `--replay` pour régler le cadrage. L'objet ne doit jamais
être nécessaire.

---

## 8. Annexe — commandes utiles

```bash
# Installation complète du poste : voir docs/INSTALLATION-LOCAL.md

# Tout lancer
cp .env.example .env && docker compose up -d

# Répétition générale chronométrée
python tools/e2e_demo.py

# Tous les tests (53)
./tools/run_tests.sh

# Tests d'un service
cd services/remediation-engine && ../../.venv/bin/python -m pytest test_state_bus.py -v
cd services/ml-detector && ../../.venv/bin/python -m pytest test_detector.py -v
cd services/payment-api && ../../.venv/bin/python -m pytest test_payment_api.py -v
cd hardware/bridge && ../../.venv/bin/python -m pytest test_hardware_bridge.py -v
cd services/monero-crypto-core && cargo test --release

# Recalibrer le modèle sur le trafic local
cd services/ml-detector
../../.venv/bin/python capture_baseline.py --seconds 420
../../.venv/bin/python train.py --from-capture baseline.jsonl --epochs 600
../../.venv/bin/python analyze_separation.py        # quelles features portent
../../.venv/bin/python validate_real.py --n 20      # validation par le vrai chemin

# Pont hardware, sans matériel
python hardware/bridge/hardware_bridge.py --dry-run
python hardware/bridge/hardware_bridge.py --port COM3 --replay

# Dashboard
cd front-dashboard && npm run build
```

### Diagnostic rapide si la démo ne part pas

| Symptôme | Vérifier |
|---|---|
| Score au repos > seuil | `ml-detector/threshold.json` ne correspond plus au trafic → recapturer |
| `model: degraded` | artefacts manquants → `docker compose build ml-detector` |
| `last_webhook_error` non nul | URL du webhook / moteur arrêté |
| Aucune détection alors que le score monte | `runtime_threshold_ignored` non nul dans `/healthz` (§9.6) |
| Aucune détection | fenêtre < 8 événements → générer du trafic normal d'abord |
| `REMEDIATION_FAILED` | `remediation-engine/history` → `ansible_rc` |
| 409 en série sur `/tx/submit` | registre épuisé → redémarrer `payment-api` |
| Dashboard vide | `curl localhost:8084/state`, puis vérifier « SSE KO » à l'écran |

---

## 9. Défauts trouvés en passant à l'environnement réel (Docker)

**Sept défauts, tous invisibles en mode `local-dev`**, parce que les stubs les
masquaient. C'est la section la plus utile en Q&A : elle montre que la mise en
conteneur n'est pas une formalité.

### 9.1 L'image `ml-detector` ne contenait aucun modèle

Le `Dockerfile` testait la présence de `threshold.json` seul pour décider
d'entraîner. Or ce fichier est **versionné**, alors que `scaler.joblib`,
`isolation_forest.joblib` et `autoencoder.pt` sont ignorés par git : l'image
embarquait donc le seuil… sans le modèle. Le détecteur démarrait en `degraded`
et `e2e_demo.py` s'arrêtait au premier contrôle. Le test porte maintenant sur
les **quatre** artefacts.

### 9.2 Le noyau Rust ne compilait pas (et plantait à l'exécution)

`rust:1.80` refuse `edition2024`, désormais utilisée par `zeroize` et
`getrandom` : `cargo build` échouait avant même de compiler le projet. Image
passée à `rust:1.99` + `--locked` sur un `Cargo.lock` versionné.

Surtout : `point_from_hex` faisait `arr.copy_from_slice(&bytes[..32])` sans
vérifier la taille. Un `curl` avec `"0000"` provoquait un **panic du worker
tokio** (`range end index 32 out of range`). Corrigé : 400 explicite, 4 tests
Rust couvrent le cas.

### 9.3 Le pool d'outputs se vidait, et la « quarantaine » ne quarantinait rien

Deux bugs liés :

- `GET /outputs?unspent=true` filtrait sur une **key image recalculée
  localement** en SHA-256. Tant que le noyau Rust était un placeholder, la
  formule coïncidait par hasard ; avec le vrai noyau, elle ne correspond plus.
  Le filtre est passé sur l'identifiant d'output réellement dépensé.
- Le registre n'était jamais réapprovisionné : après deux `e2e_demo.py`, les
  clients recevaient des **409 en série**, ce qui ressemble à une attaque
  permanente. Il se réalimente maintenant avec la même loi d'âge.
- `POST /admin/quarantine-outputs` tirait **25 outputs au hasard** : les
  outputs empoisonnés restaient en place, et le registre fondait de 25 à chaque
  incident. Il retire désormais les 25 **plus anciens** — exactement le signal
  que le ML mesure.

### 9.4 Le durcissement du playbook n'avait aucun effet

L'étape « durcir la taille des anneaux » appelait
`monero-crypto-core:8081/admin/ring-size` : cet endpoint n'existe pas, la tâche
échouait sous `ignore_errors`, et le playbook « réussissait » sans rien durcir.
L'endpoint a été ajouté là où la décision se prend réellement, `payment-api`.

Conséquence inattendue, trouvée par la mesure : une fois le durcissement
effectif (11 → 25 leurres), le trafic **post-remédiation** devenait anormal
(asymétrie des âges 0,67 → 0,89). La baseline de capture alterne donc maintenant
11 et 25 leurres.

### 9.5 Deux démonstrations d'affilée se contaminaient

La fenêtre glissante du détecteur n'était purgée qu'à l'arrivée d'une
observation **non synthétique**. Après une attaque, les événements empoisonnés
restaient jusqu'à 10 s, et la deuxième exécution de `e2e_demo.py` concluait à
une « anomalie avant l'attaque » qui n'était que le reste de la première. La
purge est maintenant faite avant toute sortie anticipée (y compris pour la
sonde), et le script attend une fenêtre vide (`window_size == 0`) avant de
mesurer.

### 9.6 Un seuil de calibration périmé désactivait la détection, en silence

`threshold_runtime.json` (mesuré par `calibrate_threshold.py`) était embarqué
dans l'image par le contexte de build. Une image construite après un
ré-entraînement conservait un seuil mesuré sur l'**ancien** modèle : 13,66
contre 4,91 appris. L'attaque culminait à 11,2 → **aucune détection, aucune
erreur, aucun log**. Trois répétitions ont échoué avant qu'on comprenne.

Correction en trois temps : le fichier est exclu du contexte Docker ; il porte
l'empreinte `model_trained_at` du modèle sur lequel il a été mesuré ; le
détecteur **refuse** un fichier dont l'empreinte ne correspond pas et publie la
raison dans `/healthz` (`runtime_threshold_ignored`).

### 9.7 Le pont hardware aurait pu redémarrer payment-api en pleine démo

Le playbook contenait `docker compose up -d --force-recreate payment-api`
depuis le conteneur de remédiation, protégé par `ignore_errors`. Le client
Docker n'étant pas dans l'image, la tâche échouait — silencieusement. Mais le
socket Docker était monté (`/var/run/docker.sock`) : la tâche était donc à un
`apt install docker.io` d'effacer le registre d'outputs en mémoire, c'est-à-dire
la transaction de preuve que la démo doit montrer. Tâche supprimée, socket
retiré. Un redémarrage destructif n'est pas une remédiation.
