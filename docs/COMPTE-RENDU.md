# Compte rendu technique — SHADOW-SETTLE / RESILIENCE-ALPHA

Date : 19-20 octobre 2026
Périmètre de cette session : **P0 — chaîne attaque → détection → remédiation → preuve**
Statut : **opérationnel et mesuré**

---

## 1. Résumé exécutif

La chaîne critique du sujet tourne de bout en bout, sans intervention humaine,
en **12 secondes**. La grille nationale alloue 3 minutes à la démonstration
live et 25 points à cet axe : la marge est de 78 secondes sur l'objectif
interne de 90 s.

| Indicateur | Valeur mesurée |
|---|---|
| Durée détection → reprise | 9,9 à 10,2 s |
| Durée reprise → transaction de preuve validée | 12,3 à 12,7 s (3 exécutions) |
| Objectif interne | < 90 s |
| Marge | ≈ 78 s |
| Faux déclenchements sur trafic normal | **0 %** (3 fenêtres consécutives) |
| Rappel sur 4 familles d'attaques | 81 % |
| Tests automatisés | 7 unitaires + 5 vérifications d'intégration, tous verts |

Ce qui n'était pas prévu et qui a été découvert : le pipeline ML du squelette
**ne pouvait pas fonctionner** (entraînement sur des distributions inventées,
inférence sur des zéros). Quatre défauts de conception ont été identifiés par
la mesure et corrigés. Détail au §4 — c'est la partie la plus utile pour le
dossier technique et pour les questions du jury.

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
| `Dockerfile` | Entraînement à la construction, jamais à l'exécution |

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

### 2.3 API de paiement (`services/payment-api`)

- Registre d'outputs exposé publiquement (modèle de menace réaliste).
- **Tirage gamma** des leurres sur l'âge (remplace le `random.sample` uniforme
  du squelette, qui était un TODO explicite du README).
- Anti-double-dépense par key image, avec télémétrie même en cas de rejet.
- `POST /pin/verify` + `GET /pin/stats` : surface d'attaque physique.
- Sonde de liveness marquée `synthetic` (exclue du ML).
- `POST /admin/quarantine-outputs` : endpoint réellement appelé par le
  playbook Ansible.

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
  bloquants.
- `tools/fake_ansible.py` : stub pour rejouer la chaîne sans Ansible.
- `local-dev/` : lancement sans Docker, clairement marqué **non livrable**.

---

## 3. Preuves d'exécution

### 3.1 Répétition générale (3 exécutions consécutives)

```
detection_seconds      9.8   9.9   10.2
recovery_seconds      12.3  12.4  12.7
proof_transaction_ok  true  true  true
ansible_rc               0     0     0
chain_complete        true  true  true
```

La stabilité compte autant que la vitesse : une démo qui marche une fois sur
deux vaut zéro point.

### 3.2 Modèle retenu

```json
{
  "threshold": 3.3685,
  "calibration_set": "holdout_25pct",
  "score_function": "max(0.5*z_iso+0.5*z_ae, z_median_age)",
  "n_normal": 199,
  "evaluation": {
    "false_positive_rate": 0.005,
    "false_positive_rate_3_consecutive": 0.0,
    "attack_recall": 0.8106
  }
}
```

### 3.3 Séparation des features (mesurée, pas supposée)

| Feature | Trafic normal | Attaque | AUC |
|---|---|---|---|
| `decoy_median_age_days` | 3,553 ± 0,289 | 28,291 ± 4,515 | **1,000** |
| `ring_age_skewness` | 0,880 ± 0,285 | 1,451 ± 0,282 | 0,921 |
| `ring_verify_cpu_us` | 1 147 ± 97 | idem | 0,500 |
| `inter_arrival_median` | 0,954 ± 0,199 | idem | 0,500 |
| `tx_latency_p95` | 292,4 ± 2,8 | idem | 0,500 |
| `key_image_rejection_ratio` | 0 | 0 | 0,500 |

Lecture honnête : **une seule feature discrimine l'attaque principale**, et
elle est physiquement justifiée (voir §4.4). Les autres mesurent des attaques
différentes (rejeu de key image, anneau malformé, rafale automatisée) et
servent au modèle générique.

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

## 6. Limites assumées de cette session

**À lire avant de conclure que tout est prêt.**

1. **Docker et Rust ne sont pas installés sur le poste de travail utilisé.**
   La chaîne a été validée avec :
   - `local-dev/crypto_shim.py` — cryptographie **factice** (SHA-256 au lieu de
     CLSAG/Pedersen) ;
   - `tools/fake_ansible.py` — stub de playbook.

   Ces deux fichiers sont marqués non livrables. **`docker compose up` et le
   noyau Rust réel restent à valider.** C'est le premier point à faire demain.

2. **`monero-crypto-core` est toujours un squelette** pour `ring-sign`,
   `ring-verify` et `audit-view`. C'est la brique la plus lourde et la plus
   différenciante du projet. La chaîne P0 fonctionne *malgré* cela, parce que
   l'anti-double-dépense et la télémétrie sont réels ; mais l'expertise
   technique (10 pts nationaux) dépend de cette brique.

3. **Le modèle est calibré sur 199 fenêtres capturées sur un seul poste.**
   À refaire sur le PC hôte de démonstration :
   ```bash
   python capture_baseline.py --seconds 300
   python train.py --from-capture baseline.jsonl
   ```
   Un seuil calibré ailleurs peut produire des faux positifs.

4. **Le modèle 3D n'a pas été imprimé.** C'est une v1 paramétrique ; les cotes
   du terminal doivent être mesurées avant impression.

5. **`auth-service` lit toujours sa clé JWT dans une variable
   d'environnement**, pas dans Vault.

---

## 7. Plan pour la suite

### Priorité 1 — valider l'environnement réel (½ journée)

```bash
docker compose up db-postgres vault -d
docker compose up monero-crypto-core payment-api auth-service -d
docker compose up ml-detector remediation-engine shadow-api -d
docker compose up front-dashboard -d
python tools/e2e_demo.py
```

Points de vigilance : le modèle ML doit être `ready` au démarrage, et le
webhook ne doit remonter aucune erreur.

### Priorité 2 — noyau cryptographique (le vrai différenciateur)

`ring-sign` / `ring-verify` / `audit-view` : CLSAG + engagements de Pedersen +
déchiffrement par View Key. C'est ce qui transforme un projet correct en projet
mémorable, et ce qui tient l'axe « expertise technique ».

### Priorité 3 — gouvernance

EBIOS RM et PCA/PRA reliés à l'architecture réelle, pas des documents
génériques. Le tableau de reprise après panne du §6.4 de
`docs/hardware/CONCEPT-PHYSIQUE.md` est un modèle du genre attendu.

### Priorité 4 — objet physique

Câblage (alim 5 V séparée, GND commun, 330 Ω sur DIN, 1000 µF sur le 5 V),
flash du firmware, `--replay` pour régler le cadrage. L'objet ne doit jamais
être nécessaire.

---

## 8. Annexe — commandes utiles

```bash
# Répétition générale chronométrée
python tools/e2e_demo.py

# Tests
cd services/remediation-engine
python -m pytest test_state_bus.py -v
python test_incident_flow.py

# Recalibrer le modèle sur le trafic local
cd services/ml-detector
python capture_baseline.py --seconds 300
python train.py --from-capture baseline.jsonl
python analyze_separation.py        # vérifier quelles features portent

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
| `model: degraded` | artefacts manquants → `python train.py` |
| `last_webhook_error` non nul | URL du webhook / moteur arrêté |
| Aucune détection | fenêtre < 8 événements → générer du trafic normal d'abord |
| `REMEDIATION_FAILED` | `remediation-engine/history` → `ansible_rc` |
