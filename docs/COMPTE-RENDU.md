# Compte rendu technique — SHADOW-SETTLE / RESILIENCE-ALPHA

Date : 19-20 octobre 2026
Périmètre : **MVP — cinq piliers interconnectés, de l'attaque à la preuve**
Statut : **opérationnel et mesuré ; les ajouts de la session 5 restent à
valider sur la pile Docker du PC hôte**

Cinq sessions :
1. **Conception du pipeline ML** (poste sans Docker) — §4.
2. **Validation sur l'environnement réel** — §9, sept pannes silencieuses.
3. **Complétion du MVP** — §10 : cryptographie réelle, déception branchée,
   observabilité, gouvernance.
4. **Consigne additionnelle** — §11 : authentification visage du back-office.
5. **Nouvelles règles du jury** — §12 : boutique en base, RAG, ingestion SIEM,
   durcissement, mesure de charge.

> **Ce document est un journal de sessions, pas un état des lieux.** Les
> sections §4 à §10 décrivent le système **à la date de leur session** : un
> « non commencé » ou un « 79 tests » y est exact, à cette date-là.
> L'état courant est dans [`TODO.md`](../TODO.md) (ce qui reste) et
> [`docs/RAPPORT.md`](RAPPORT.md) (ce qui est mesuré). Les sections §11 et
> suivantes décrivent l'état actuel.

---

## 1. Résumé exécutif

La chaîne critique du sujet tourne de bout en bout, sans intervention humaine,
en **6,6 à 7,2 secondes** sur l'environnement conteneurisé complet. La grille
nationale alloue 3 minutes à la démonstration live et 25 points à cet axe : la
marge est de 83 secondes sur l'objectif interne de 90 s.

| Indicateur | Valeur mesurée (Docker, 3 exécutions) |
|---|---|
| Durée détection → reprise | 6,6 s / 7,1 s / 7,2 s |
| Objectif interne | < 90 s |
| Marge | ≈ 83 s |
| Faux déclenchements sur trafic normal | **0 %** (0 déclenchement sur 180 fenêtres) |
| Rappel sur les 4 familles d'attaques | 100 % (1 129 fenêtres normales, 1 600 fenêtres d'attaque) |
| Tests automatisés | **219** (195 pytest + 5 scénarios + 9 unitaires Rust + 10 tests HTTP) — dont 29 pour le back-office et 36 pour la boutique (session 5) |
| Attaquant dérouté vers le honeypot | **oui** (vérifié par `e2e_demo.py`) |
| Services Docker | 19 services, 12 cibles Prometheus |

Cinq sessions distinctes, et ce sont les deux dernières qui comptent pour la suite :

1. **Session 1** (poste sans Docker ni Rust) : le pipeline ML du squelette ne
   pouvait pas fonctionner. Quatre défauts de conception ont été trouvés par la
   mesure et corrigés (§4).
2. **Session 2** (environnement réel installé) : la chaîne tourne sous Docker
   avec Ansible et le noyau Rust compilé. **Sept défauts supplémentaires** ont
   été trouvés — tous invisibles en mode « local-dev », parce que les stubs les
   masquaient. Détail au §9 : c'est la partie la plus utile pour la Q&A.
3. **Session 3** : complétion du MVP — cryptographie réelle, déception active,
   observabilité, gouvernance (§10).
4. **Session 4** : consigne additionnelle — authentification visage du
   back-office, bandeau LED ESP8266 (§11).
5. **Session 5** (20 octobre, soir) : nouvelles règles du jury — le SI devient
   un site de paiement adossé à PostgreSQL, avec RAG, ingestion SIEM et
   durcissement mesuré (§12).

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
| `test_detector.py` | **Nouveau.** 12 tests : invariance au volume, purge de fenêtre, anti-rebond |
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
- 27 tests (bus d'états, flux d'incident, ingestion SIEM, état terminal).

### 2.3 API de paiement (`services/payment-api`)

- Registre d'outputs exposé publiquement : clé publique one-time + **montant
  chiffré**. Un montant n'est lisible qu'avec la View Key.
- Portefeuille de démonstration : le service joue le rôle du wallet (View Key +
  Spend Key), dérive la clé one-time de l'output dépensé, signe un anneau réel.
- `GET /audit/last` et `GET /audit/outputs/{id}` : audit **view-only**, sans
  jamais utiliser la Spend Key.
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
- `test_payment_api.py` : 43 tests (13 à l'origine, 30 ajoutés en sessions 4 et
  5, dont 17 sur le registre et l'idempotence) — gamma, `unspent`, quarantaine,
  ring-size, PIN, non-exposition des clés privées.

### 2.4 Dashboard (`front-dashboard`)

- Anneau de LEDs en SVG, **miroir exact** de l'objet physique (même état, même
  séquence).
- Quadrant « Radar ML » branché sur `/score` en temps réel.
- Consommation SSE avec détection de coupure et numéro de séquence.
- **Pavé PIN** (`PinPad.jsx`) : surface d'attaque physique sans démonter le
  terminal, avec compteur de refus en direct.
- **Quadrant « Audit view-only »** : montant déchiffré par la View Key seule.
- **Depuis la session 5** : vitrine, back d'administration et quadrant
  « Assistant » (§12.2, §12.3).
- Build de production vérifié.

### 2.4 bis Point d'entrée et déception (`services/waf`)

- Reverse proxy vers `payment-api`, **seul point d'entrée « client »**.
- Apprend l'âge des outputs en proxyfiant `/outputs` ; calcule l'âge médian des
  leurres de chaque transaction.
- Déroute vers `shadow-api` **uniquement** quand le playbook a armé la
  déception (`POST /admin/redirect`).
- `POST /admin/reset`, `GET /admin/state`, `/metrics`.
- 10 tests, dont **« le client légitime n'est jamais dérouté »**.

### 2.5 Objet physique (`hardware/`, `docs/hardware/`)

- `docs/hardware/CONCEPT-PHYSIQUE.md` : rôle, scénario de démonstration en
  60 s, contrat d'état, analyse de reprise après panne.
- `hardware/bridge/hardware_bridge.py` : pont SSE → USB, reconnexion,
  mode `--replay` pour répéter sans attaque.
- `hardware/ring_signer_hsm/ring_signer_hsm.ino` : **3 bugs corrigés**,
  parseur de trame, servo piloté sur changement d'état uniquement.
- `hardware/models/socle_terminal.scad` : modèle paramétrique (4 pièces).
- `hardware/README.md` : câblage, impression, recette en 7 tests.

### 2.6 Outils de recette (`tools/`, `local-dev/`)

- `tools/e2e_demo.py` : répétition générale chronométrée, avec 6 contrôles
  bloquants, et attente explicite d'une fenêtre propre avant mesure.
- `tools/run_tests.sh` : lance les tests du dépôt, service par service
  (**79** à la fin de la session 2, **104** après la session 4, **219** après
  la session 5).
- `tools/attack.py` : **PC Attaquant** — 4 familles d'attaques, `reset`.
- `tools/load_test.py`, `tools/chaos.py`, `tools/preflight.py` : charge, chaos,
  pré-vol de soutenance (§12.5, §12.6).
- `docs/DEMO.md` : runbook de soutenance (scénario, rôles, Q&A, plan de secours).
- `observability/` : Prometheus (8 cibles, 11 alertes à cette étape ; **12 cibles,
  24 règles Prometheus et 4 règles Loki** après la session 5) + Grafana
  provisionné.
- `tools/fake_ansible.py` : stub pour rejouer la chaîne sans Ansible.
- `local-dev/` : lancement sans Docker, clairement marqué **non livrable**.
- `requirements-dev.txt` + `docs/INSTALLATION-LOCAL.md` : installation du poste
  (Docker/Colima, Rust, Python, Node, Vault) et procédure de recette.

### 2.7 Nouvelles briques testées

- `services/monero-crypto-core` : **noyau cryptographique réel** — bLSAG, key
  image, Pedersen, View Key. 9 tests Rust + 10 tests HTTP.
- `services/auth-service` : clé JWT lue dans **Vault KV v2**, avec cache de 30 s
  et fallback **explicite** (`/healthz` dit `vault` ou `fallback_env`). 9 tests.
- `services/shadow-api` : honeypot borné en mémoire, journal consultable.
  4 tests.
- `observability/` : métriques `/metrics` sur 12 services, alertes, dashboard.

### 2.8 Documentation de gouvernance

- `docs/GOUVERNANCE.md` : EBIOS RM (5 ateliers), PCA/PRA avec RTO par service,
  correspondance NIS2 art. 21 et DORA art. 5-28, risques résiduels,
  recommandations chiffrées.

---

## 3. Preuves d'exécution

### 3.1 Répétition générale — environnement Docker complet

```
detection_seconds      3.0   3.5   3.7
recovery_seconds       6.6   7.1   7.2
proof_transaction_ok  true  true  true
deception_active      true  true  true
attacker_routed       true  true  true
ansible_rc               0     0     0
chain_complete        true  true  true
```

14 conteneurs `Up` à cette étape (19 après la session 5), Ansible réel (4,1 s de
playbook), noyau Rust bLSAG compilé, Prometheus et Grafana provisionnés.
La stabilité compte autant que la vitesse : une démo qui marche une fois sur
deux vaut zéro point. Trois exécutions consécutives donnent le même résultat
parce que `e2e_demo.py` attend désormais une fenêtre de détection **vide** avant
de mesurer (voir §9.5).

### 3.2 Modèle retenu

```json
{
  "threshold": 4.6091,
  "calibration_set": "holdout_25pct",
  "score_function": "max(0.5*z_iso+0.5*z_ae, z_median_age)",
  "n_normal": 1129,
  "source": "capture:baseline.jsonl",
  "threshold_estimators": {
    "percentile_99.9": 4.2641,
    "mean_plus_6std": 4.6091,
    "retained": 4.6091
  },
  "evaluation": {
    "false_positive_rate": 0.006,
    "false_positive_rate_3_consecutive": 0.002,
    "attack_recall": 1.0
  }
}
```

**Changement d'estimateur, justifié par la mesure.** Le seuil était fixé au
max du 99,5ᵉ percentile et de `moyenne + 4σ`. Sur 1 129 fenêtres réelles, cette
règle donnait 0,89 % de déclenchements sur trois fenêtres consécutives — soit un
faux positif toutes les ~2 minutes de trafic dense, inacceptable pour une démo
de 3 minutes. Mesure comparative :

| Estimateur | Seuil | FPR | FPR sur 3 fenêtres |
|---|---|---|---|
| moyenne + 4σ | 2,97 | 1,42 % | **0,89 %** |
| 99,5ᵉ percentile | 3,44 | 0,53 % | 0,18 % |
| 99,9ᵉ percentile | 4,26 | 0,27 % | 0,09 % |
| **moyenne + 6σ** | **4,61** | **0,27 %** | **0,09 %** |

Le seuil retenu est le **maximum des deux estimateurs conservateurs**
(4,6091 = moyenne + 6σ, contre 4,2641 pour le 99,9ᵉ percentile). Il reste
**appris** (aucune constante dans le code), mais l'estimateur est plus
conservateur — ce qui est cohérent avec le principe §5.3 : le coût d'un faux
déclenchement (25 points) est sans commune mesure avec celui d'une détection 2 s
plus tardive. Et la marge est intacte : le score le plus bas d'une attaque
mesurée est **14,29**, soit **3,1×** le seuil.

Validation par le **vrai chemin de production** (`validate_real.py`, trafic
légitime et attaque envoyés via `payment-api`, pas d'injection directe) :

```
normal_mean  0.80  normal_max  1.24
attack_mean 14.62  attack_min 14.29
false_positive_rate 0.0   attack_detection_rate 1.0
false_trigger_rate  0.0
```

Aucun chevauchement : le score le plus haut du trafic normal (1,24) est 3,7 fois
sous le seuil (4,61), le score le plus bas de l'attaque (14,29) est 3,1 fois
au-dessus. La marge est telle qu'un ajustement fin du seuil n'a aucun intérêt :
c'est la séparation qui compte, pas le curseur.

Note sur `attack_trigger_rate` (0,69) : le taux de *déclenchement* est plus bas
que le taux de *détection* (1,0) parce que le script lit le score après chaque
transaction, et l'anti-rebond exige 3 fenêtres consécutives. Une attaque qui
dure — c'est le cas réel — déclenche ; une mesure ponctuelle n'a pas toujours
le temps de voir la série.

### 3.3 Séparation des features (mesurée, pas supposée)

`analyze_separation.py` sur 1 129 fenêtres normales et 1 600 fenêtres d'attaque :

| Feature | Trafic normal | Attaque | AUC |
|---|---|---|---|
| `decoy_median_age_days` | 3,850 ± 0,167 | 25,731 ± 1,859 | **1,000** |
| `ring_age_skewness` | 0,934 ± 0,191 | 1,456 ± 0,226 | 0,947 |
| `ring_verify_cpu_us` | 2 502 ± 573 | idem | 0,500 |
| `inter_arrival_median` | 0,356 ± 0,356 | idem | 0,500 |
| `tx_latency_p95` | 46,10 ± 12,76 | idem | 0,500 |
| `key_image_rejection_ratio` | 0 | 0 | 0,500 |

Lecture honnête : **une seule feature discrimine l'attaque principale**, et
elle est physiquement justifiée (voir §4.4). `ring_age_skewness` est un
complément (0,94) qui devient plus faible à mesure que la taille d'anneau
grandit : à 25 leurres, la distribution se lisse. Les autres mesurent des
attaques différentes (rejeu de key image, anneau malformé, rafale automatisée)
et servent au modèle générique — c'est précisément pour cela qu'elles restent
dans le vecteur : un modèle à une seule feature ne détecterait qu'une attaque.

Note sur `ring_verify_cpu_us` : elle valait 0 µs tant que le noyau était un
placeholder. Depuis bLSAG, elle mesure **2 502 ± 573 µs** pour un anneau de 11 —
une vraie vérification cryptographique. Elle ne discrimine pas l'attaque
principale (AUC 0,50) mais portera le signal « anneau malformé » : un anneau de
taille anormale coûte un temps de vérification proportionnel à N.

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
trafic normal (gamma, biais récent) : 3,850 j ± 0,167
leurres empoisonnés (uniforme)      : 25,731 j ± 1,859
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

- seuil = `max(percentile 99,9, moyenne + 6σ)` ;
- 3 fenêtres consécutives anormales avant déclenchement ;
- refus de conclure si la fenêtre contient moins de 8 événements.

### 5.4 Aucun seuil en dur

Exigence explicite du sujet. Le seuil est appris, stocké dans
`threshold.json`, et traçable (`threshold_estimators` documente les deux
estimateurs et celui qui a été retenu).

---

## 6. Limites assumées (à lire avant de conclure que tout est prêt)

### 6.1 Ce qui est validé, et comment

`docker compose up` complet (**14 services** à la session 2, **15** après la
consigne additionnelle, **19** après la session 5), noyau Rust compilé avec
bLSAG, Ansible réel, **79 tests** verts à cette étape (**104** après la
session 4, **219** après la session 5), trois répétitions
générales consécutives, Prometheus et Grafana provisionnés. Les stubs
`local-dev/crypto_shim.py` et `tools/fake_ansible.py` ne sont plus nécessaires :
ils restent dans le dépôt, marqués non livrables, pour un poste sans Docker.

### 6.2 Cryptographie : bLSAG, pas CLSAG — et pas de Bulletproofs

La signature d'anneau est **réelle et vérifiable** (§10.1), mais deux écarts
subsistent par rapport à Monero de production :

- **bLSAG** au lieu de **CLSAG** : la signature est linéaire en N (~2× plus
  grosse à N=11). Le modèle de sécurité est le même ; c'est une optimisation de
  taille, pas une régression.
- **Pas de preuves de domaine** : sans Bulletproofs, rien n'empêche un montant
  négatif modulo l de satisfaire `∑C_in − ∑C_out = 0`. Cela suppose un
  attaquant authentifié qui construit lui-même ses engagements — ce n'est pas
  le scénario de la démo, mais c'est une vraie faiblesse, et elle est publiée
  dans `/healthz` (`not_implemented`) plutôt que dissimulée.

### 6.3 Le modèle est calibré sur un seul poste

1 129 fenêtres capturées ici. **À refaire sur le PC hôte
de démonstration** :

```bash
cd services/ml-detector
../../.venv/bin/python capture_baseline.py --seconds 420 --out baseline.jsonl
../../.venv/bin/python train.py --from-capture baseline.jsonl --epochs 600
cd ../.. && docker compose build ml-detector && docker compose up -d ml-detector
```

Un seuil calibré ailleurs peut produire des faux positifs — c'est arrivé trois
fois pendant le projet. La capture varie désormais cadence **et** taille
d'anneau : sans cela, le trafic post-remédiation (taille durcie à 25) sortait de
la distribution normale (§9.4).

### 6.4 `auth-service` : AppRole Vault non provisionné

La lecture Vault fonctionne (KV v2, cache 30 s, fallback explicite, 9 tests).
Mais l'**AppRole** n'est pas provisionné : en démonstration, le playbook utilise
`VAULT_TOKEN` (token de service) plutôt qu'un `role_id`/`secret_id` à portée
restreinte. C'est un cran en dessous de ce que demanderait la production, et
c'est écrit dans le playbook.

### 6.5 RAG : non commencé *(à cette date — livré depuis, voir §12.3)*

Le pilier IA/DATA est couvert par la détection ML, pas par l'assistant RAG
(Ollama + LangChain). C'est le seul pilier partiellement traité.

> **Depuis** : `services/rag-assistant` livre l'assistant (index BM25 construit
> au build, **14 documents / 597 passages**, 16 tests, quadrant dans le
> dashboard). Seul le branchement d'Ollama sur le PC IA reste à faire — le
> service répond en mode extractif en attendant (§12.7).

### 6.6 Le modèle 3D n'a pas été imprimé

C'est une v1 paramétrique ; les cotes du terminal doivent être mesurées avant
impression. Non bloquant : l'objet physique n'est jamais nécessaire.

### 6.7 Le registre des outputs n'est plus en mémoire — limite levée le 20 octobre

*Cette section disait l'inverse. C'était vrai jusqu'à la session 4 ; elle est
réécrite ici avec la date de la correction.*

Depuis la session 4, `services/payment-api/registry.py` journalise dans
PostgreSQL les outputs, les key images, les dépenses et le portefeuille ; le
registre est rechargé au démarrage. Un redémarrage de `payment-api` ne remet
plus les compteurs à zéro et n'invalide plus la transaction de preuve en cours.
**17 tests** couvrent le registre et l'idempotence.

Ce qui restait ouvert : une coupure de PostgreSQL faisait passer
`RegistryStore.available` à `False` **définitivement**, et les écritures
suivantes étaient perdues en silence. La reconnexion avec backoff exponentiel
(1 s → 30 s) et compteur `lost_writes` a été implémentée en session 5 (§12.6).
**À vérifier sur le PC hôte** : `docker compose stop db-postgres`, quelques
paiements, `start` → `/healthz` doit repasser `durable: true` avec
`lost_writes` non nul.

---

## 7. Plan pour la suite

### Fait dans cette session

| Chantier | État |
|---|---|
| Noyau cryptographique réel (bLSAG, Pedersen, View Key) | **fait** (§10.1) |
| Déception active branchée (WAF → honeypot) | **fait** (§10.2) |
| Observabilité Prometheus + Grafana | **fait** (§10.3) |
| Dossier EBIOS RM / PCA-PRA / NIS2-DORA | **fait** (§10.4) |
| Outils de démonstration (PC Attaquant, runbook) | **fait** (§10.5) |

### Priorité 1 — Bulletproofs

Preuves de domaine sur les montants. C'est la seule faiblesse cryptographique
restante, et la seule ligne de `/healthz` qui dit encore
`not_implemented` sur un point de sécurité. 3 à 5 jours.

### Priorité 2 — AppRole Vault

Créer la politique `remediation-policy`, provisionner `role_id`/`secret_id`,
retirer `VAULT_TOKEN` du compose. ½ journée.

### Priorité 3 — Registre persistant — **fait** (20 octobre)

PostgreSQL est déjà dans la stack. `OUTPUTS` est migré en table
(`services/payment-api/registry.py`), le registre est rechargé au démarrage et
les key images sont écrites dans la même transaction que la dépense. 17 tests.
Ce qui restait — la reconnexion automatique après une coupure de la base — est
implémenté en session 5 (§12.6), **à vérifier sur le PC hôte**.

### Priorité 4 — RAG Ollama — **fait pour le service** (20 octobre)

L'assistant local existe : `services/rag-assistant` (index BM25, 14 documents /
597 passages, 16 tests, quadrant dans le dashboard). Il répond en **extractif**
quand Ollama est absent. Le branchement d'Ollama sur le PC IA (modèle ≤ 8 Md de
paramètres) reste à faire : voir §12.3 et §12.7.

### Priorité 5 — Objet physique

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

# Tous les tests (219)
./tools/run_tests.sh

# PC Attaquant (4 familles d'attaques)
python tools/attack.py list
python tools/attack.py poison
python tools/attack.py reset

# Tests d'un service
cd services/remediation-engine && ../../.venv/bin/python -m pytest test_state_bus.py -v
cd services/ml-detector && ../../.venv/bin/python -m pytest test_detector.py -v
cd services/payment-api && ../../.venv/bin/python -m pytest test_payment_api.py -v
cd hardware/bridge && ../../.venv/bin/python -m pytest test_hardware_bridge.py -v
cd services/monero-crypto-core && cargo test --release
cd services/monero-crypto-core && ../../.venv/bin/python -m pytest test_crypto_flow.py -v
cd services/waf && ../../.venv/bin/python -m pytest test_waf.py -v

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

Les commandes ci-dessus sont écrites pour Git Bash / Linux. Sous Windows
PowerShell, le lanceur de tests est `.\tools\run_tests.ps1` : mêmes suites,
mêmes codes de sortie, et tests Rust inclus si `cargo` est présent.

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
| Toutes les attaques déroutées | déception restée armée → `python tools/attack.py reset` |
| Grafana refuse le mot de passe | volume d'un ancien démarrage → `docker compose down -v` |

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

---

## 10. Complétion du MVP (session 3)

Objectif : transformer un P0 démontrable en MVP couvrant réellement les cinq
piliers. Quatre chantiers, tous vérifiables.

### 10.1 Noyau cryptographique réel (pilier DEV, 10 pts nationaux)

`monero-crypto-core` n'est plus un squelette. Ce qui est implémenté :

| Primitive | Détail | Test |
|---|---|---|
| Adresse one-time | `P = H_s(rA)G + B`, `R = rG` | `adresse_one_time_et_derivation...` |
| Clé dérivée porteur | `x = H_s(aR) + b`, vérifiée contre `P` | idem |
| **Key image** | `I = x·H_p(P)` avec `H_p` = hash-**vers-le-groupe** (SHA-512 → `from_uniform_bytes`) | `key_image_deterministe...` |
| Signature d'anneau | **bLSAG** complet : `c_i`, `s_i`, fermeture `s_π = α − c_π·x` | `signature_d_anneau_valide...` |
| Engagements | `C = aG + rH`, `H` sans log discret connu | `pedersen_est_additif...` |
| Équilibre | `∑C_in − ∑C_out = 0` | `commit_split_equilibre...` |
| Montants | masque `Keccak("amount" ‖ shared)` | `chiffrement_du_montant...` |
| Audit view-only | `GET /audit/last` déchiffre sans Spend Key | `test_crypto_flow.py` |

**Le point qui compte en Q&A** : la key image utilise un vrai hash-vers-groupe.
Si l'on avait défini `H_p(P) = h(P)·G` avec `h` connu (la tentation naturelle),
n'importe qui pourrait forger une key image arbitraire — choisir `I = zG`, puis
calculer `R_π = (u·h + c_π·z)G` — et l'anti-double-dépense deviendrait
décoratif. C'est le genre d'erreur qui ne se voit pas à la lecture et qui
détruit la crédibilité technique du projet.

Deux écarts assumés, publiés dans `/healthz` (`not_implemented`) :
bLSAG au lieu de CLSAG (signature ~2× plus grosse, même sécurité) et absence de
Bulletproofs (un montant négatif modulo l passerait l'équilibre).

Effet mesuré : `ring_verify_cpu_us` passe de 0 µs (placeholder) à **~1 200 µs**
pour un anneau de 11. La feature ML correspondante devient enfin informative —
elle ne l'était pas, et on ne l'avait pas maquillée.

### 10.2 Déception active branchée (pilier CYBER)

Le honeypot existait mais **personne ne lui redirigeait le trafic** : l'étape 1
du playbook visait `http://waf:8081/admin/redirect`, un service inexistant.

Ajout de `services/waf` (port 8085) : reverse proxy qui apprend l'âge des
outputs en proxyfiant `/outputs`, et déroute vers `shadow-api` les transactions
dont l'âge médian des leurres dépasse 8 j — **uniquement quand le playbook a
armé la déception**.

Le test qui compte : *le client légitime n'est jamais dérouté*, même déception
armée. Une déception qui casse le paiement du client est pire que pas de
déception.

Effet mesuré : `e2e_demo.py` vérifie désormais `attacker_routed_to_honeypot`,
et le playbook affiche `ignored=0` (plus aucune tâche masquée).

### 10.3 Observabilité réelle (pilier INFRA)

Prometheus et Grafana démarraient sans configuration : des conteneurs `Up` qui
ne mesuraient rien. Trois ajouts :

- **`/metrics`** sur les 7 services de cette étape (12 après la session 5),
  format texte Prometheus, exposant les
  invariants et non des compteurs HTTP : `shadow_model_ready`,
  `shadow_webhook_error`, `shadow_runtime_threshold_ignored`,
  `shadow_crypto_core_ready`, `shadow_waf_deception_active`, etc.
- **11 règles d'alerte** (24 règles Prometheus et 4 règles Loki après la
  session 5), chacune reliée à
  une panne réellement rencontrée
  (§9). On n'alerte pas « attaque en cours » : c'est le comportement attendu.
  On alerte sur le silence.
- **Dashboard Grafana provisionné** (10 panneaux à cette étape, 13 après la
  session 5, source de données incluse) :
  modèle prêt, webhook, sonde, déception, score vs seuil, légitime vs dérouté.

### 10.4 Gouvernance (pilier TRANSFORMATION, 10 pts nationaux)

`docs/GOUVERNANCE.md` : EBIOS RM complet (5 ateliers), PCA/PRA avec RTO par
service, correspondance NIS2 art. 21 et DORA art. 5-28, registre des risques
résiduels, recommandations chiffrées.

Le document est relié à l'architecture réelle : chaque mesure cite un fichier
ou un endpoint. Les éléments non couverts sont marqués **NON COUVERT** (MFA,
gestion des tiers, plan de formation) — un dossier qui prétend tout couvrir
perd sa crédibilité au premier contrôle.

### 10.5 Outils de démonstration

| Outil | Rôle |
|---|---|
| `tools/attack.py` | PC Attaquant : 4 familles d'attaques, `--list`, `reset` |
| `tools/e2e_demo.py` | Répétition complète, vérifie aussi la déception |
| `docs/DEMO.md` | Runbook de soutenance : scénario seconde par seconde, rôles, Q&A, plan de secours |
| Pavé PIN dans le dashboard | Surface d'attaque physique sans démonter le terminal |

### 10.6 Résultat de la session 3

```
detection_seconds      3.0   3.1   3.4
recovery_seconds       6.6   7.1   7.2
proof_transaction_ok  true  true  true
deception_active      true  true  true
attacker_routed       true  true  true
ansible_rc               0     0     0
chain_complete        true  true  true
```

Trois exécutions consécutives, sans intervention. `tools/attack.py all` :
12/12 leurres empoisonnés déroutés, rejeu bloqué en 409, 40 transactions en
rafale à 9,7 tx/s, 3 entrées malformées refusées en 400 sans panic.

### 10.7 Ce que le MVP ne couvre toujours pas

| Élément | Impact | Traitement |
|---|---|---|
| Bulletproofs | Un montant négatif modulo l passe l'équilibre | Priorité 1 gouvernance |
| AppRole Vault | Token de service au lieu d'un rôle à portée restreinte | Priorité 2 |
| ~~Registre en mémoire~~ — **levé le 20/10** | Plus de perte des outputs au redémarrage | `registry.py`, PostgreSQL (§6.7, §12.2) |
| ~~RAG~~ — **service livré le 20/10** | Pilier IA/DATA couvert ; Ollama reste à brancher | §12.3, reste en §12.7 |
| Objet physique | Non flashé, modèle 3D non imprimé | Non bloquant par conception |
| Recalibrage ML | Le modèle est calibré sur ce poste, pas sur le PC de démo | 7 min, à faire la veille |
| **Authentification visage du back-office** | Absente à cette étape | **Livrée en session 4** (§11) |

> Au 20 octobre au soir, les points non barrés restent ouverts : §12.7 les
> reprend avec les autres limites de la session 5.

---

## 11. Consigne additionnelle : authentification visage (session 4)

Consigne reçue : renforcer l'authentification du back-office par une caméra USB
sur le PC hôte, une IA légère de reconnaissance faciale, la gestion des
habilitations, et un ESP8266 à LED bicolore connecté au point d'accès.

### 11.1 Ce qui a été construit

| Brique | Fichier | Choix |
|---|---|---|
| Service d'authentification | `services/backoffice/` (8086) | FastAPI + SQLite, 29 tests (25 à la session 4) |
| Reconnaissance faciale | `faces.py` | YuNet (232 Ko) + SFace (37 Mo), cosinus, **29 ms** par visage sur CPU |
| Page de connexion | `front-dashboard/src/auth/` | identifiant → caméra → mot de passe, miroir de la LED à l'écran |
| Habilitations | `AdminPanel.jsx` | comptes, rôles, visages, déblocage, sessions, audit |
| Bandeau LED | `hardware/led_bandeau_esp8266/` | PlatformIO (CLion), C++, interrogation 300 ms |
| Observabilité | `prometheus.yml`, `alert.rules.yml`, `loki.yml` | 12ᵉ cible, 24 alertes + 4 règles de logs, Alertmanager → remédiation |
| Recette | `tools/backoffice_check.py` | 6 contrôles, code de sortie 0 |

### 11.2 Les trois décisions qui ont demandé un arbitrage

**Où ouvrir la caméra ?** Dans un conteneur, l'accès webcam sous Docker
Desktop/WSL2 n'est pas fiable — et la consigne impose la caméra sur le PC
*hôte*. La capture se fait donc dans le navigateur du PC hôte
(`getUserMedia`), qui envoie **une image JPEG ponctuelle** au back-office.
Le serveur ne conserve aucune image, seulement un descripteur de 128 octets.

**Qui décide de la reconnaissance ?** Le serveur, jamais le client. Le
navigateur ne peut pas annoncer « visage reconnu » : il envoie une image, le
serveur calcule et compare. Le compteur d'échecs et le blocage de 5 minutes sont
en base — recharger la page ne les remet pas à zéro. C'est la différence entre
une sécurité et une mise en scène.

**Dans quel sens circule l'état de la LED ?** L'ESP interroge le back-office
toutes les 300 ms. Le sens inverse (POST serveur → carte) supposerait une IP
fixe, une réservation DHCP et une règle de pare-feu entrante sur le point
d'accès : trois causes de panne le jour J, pour 300 ms gagnées. Le firmware
compte lui-même les 30 s et les 10 flashs, et **s'éteint s'il perd le
back-office** : pas de feu vert sans service.

### 11.3 Ce qui a été mesuré

```
Chargement des modèles       0,08 s
Détection + descripteur     11 ms (image sans visage) / 29 ms (un visage)
Similarité même visage      0,918  (image réduite à 60 % et recompressée JPEG 60)
Seuil retenu                0,40   (défaut OpenCV)
Tests back-office           29 verts (25 à la session 4)
Firmware ESP8266            compile : 35,4 % RAM, 28,0 % flash
Image Docker                opencv-python-headless, aucun GPU, aucun PyTorch
```

Le test qui compte : un visage **différent** de celui enrôlé est refusé
(`not_recognized`), et le challenge est invalidé — rejouer le mot de passe
ensuite renvoie `challenge_used`. C'est vérifié par un test automatisé, pas
seulement à la main.

### 11.4 Ce que la session n'a pas fait

| Élément | Pourquoi | Où c'est dit |
|---|---|---|
| Détection de vivacité | modèle anti-spoofing hors périmètre | `/healthz` → `liveness: not_implemented` |
| TLS sur le back-office | la démo se fait en `localhost` | `TODO.md` §1.0 |
| Firmware flashé | pas de carte sous la main à cette étape | `hardware/led_bandeau_esp8266/README.md` §4 |
| HTTPS + caméra depuis un autre poste | `getUserMedia` exige un contexte sécurisé | `docs/INSTALLATION-LOCAL.md` §8 |

Ces quatre points sont toujours ouverts au 20 octobre au soir. §12.7 les
reprend avec les autres limites de la session 5 (Bulletproofs, AppRole,
Ollama, mesures sous Docker).

Une limite écrite ne coûte pas de point. Une limite découverte en Q&A en coûte.

---

## 12. Nouvelles règles du jury — session 5 (20 octobre, soir)

Le 20 octobre, le jury a précisé ce qu'il attendait. Trois exigences changent
l'architecture, pas l'emballage : le SI doit être **un site de paiement** avec
une base, un front et un back ; l'**IA** doit répondre sur le dossier et agir
sur les alertes de supervision ; le SI doit être **durci**, sans faille
volontaire. Cette section dit ce qui a été construit, ce qui a été mesuré, et
ce qui ne l'a pas été.

### 12.1 Le changement de périmètre

| Avant (sessions 1-4) | Après (session 5) |
|---|---|
| Trois transactions de démonstration autour d'un terminal | **Site vitrine de paiement** : catalogue, panier, commande, historique |
| Registre d'outputs en mémoire, puis PostgreSQL pour le seul registre | **PostgreSQL** pour le registre, la boutique et le portefeuille |
| Dashboard technique, sans parcours client | **Front vitrine** + **back d'administration** dans le même bundle |
| Paiement d'une transaction isolée, sans commande | Paiement **réel** par `payment-api /tx/pay` (bLSAG), adossé à une commande et à un stock |
| IA = détection ML | IA = détection ML **+ RAG + ingestion SIEM** |

Ce que cela coûte : deux services de plus (`shop-api`, `rag-assistant`), une
base à administrer, et un chemin d'achat qui traverse trois services. Ce que
cela rapporte : le SI ressemble à ce qu'un jury attend d'un « SI de paiement »,
et les attaques CYBER ont une cible crédible. La pile passe à **19 services**.

### 12.2 La boutique

`services/shop-api` (port **8087**) porte la boutique, sur **PostgreSQL**
(`db-postgres`). Le catalogue est amorcé une fois : **6 articles**
(`TERM-01`, `RING-24`, `HSM-CARD`, `SENSOR-01`, `VAULT-LIC`, `AUDIT-J`), avec
prix, stock et image.

| Brique | Réalisation |
|---|---|
| Catalogue | table `products`, `GET /products`, `GET /products/{id}` |
| Panier | `carts` / `cart_items`, quantité bornée 1..20, jeton de panier |
| Commande | `orders` + `order_items` (prix figé au moment de l'achat) |
| Paiement | `payments` + `payment_events`, statut `pending → paid \| failed` |
| Historique | `GET /orders` par **capability token** de 256 bits (`customer_ref`) |
| Back | `AdminShop.jsx` → `/admin/products`, `/admin/orders` |

Trois décisions qui comptent :

**Le stock est réservé atomiquement.**
`UPDATE products SET stock = stock - ? WHERE id = ? AND active = TRUE AND
stock >= ?` : le nombre de lignes affectées est vérifié. Deux acheteurs
simultanés sur le dernier article donnent une commande et un `409`, jamais un
stock négatif. Un échec de paiement restitue le stock et laisse le panier
intact.

**Le paiement est réel.** `POST /checkout` appelle `payment-api /tx/pay` :
output du montant exact, signature d'anneau **bLSAG**, key image stockée dans
`payments`. Le montant vient de la base, jamais du client. Une commande n'est
`paid` qu'après une preuve cryptographique ; un refus produit une commande
`failed` avec son motif dans `payment_events`.

**L'historique n'est pas énumérable.** `customer_ref` est un capability token :
sans lui, `GET /orders/{reference}` répond **404** et non 403 — un 403
confirmerait l'existence de la commande. Le format de la référence est validé
**avant** la comparaison, pour ne pas rouvrir l'oracle d'existence (S1, §12.4).

### 12.3 L'IA : RAG et ingestion SIEM

**`services/rag-assistant` (8091).** Index **BM25** construit au build de
l'image sur `docs/`, `README.md` et `TODO.md` : **14 documents, 597 passages**.
`POST /ask` renvoie une réponse **avec ses sources**. Si Ollama est joignable,
il rédige à partir des passages ; sinon le service répond en **extractif** —
les phrases du dossier, citées — et `/healthz` le dit. Un assistant qui invente
serait pire qu'un assistant qui cite.

**Disjoncteur.** Après un échec d'Ollama, plus aucune tentative pendant 30 s, et
`/healthz` ne fait **aucun appel réseau** : il lit l'état du disjoncteur. Sans
cela, le test de charge a mesuré un effondrement à 0,5 req/s (§12.5).

**Quadrant « Assistant »** dans le dashboard : question, réponse, sources et
moteur réellement utilisé (`ollama:...` ou `extractif`).

**Ingestion SIEM.** `POST /alert` reçoit les alertes d'Alertmanager. Une liste
**fermée** d'alertes est actionnable ; les autres sont journalisées sans ouvrir
d'incident. Le moteur réutilise exactement le chemin de l'incident ML : même
verrou, même playbook, même preuve de reprise.

**Corrélation multi-alertes.** Une panne réelle produit 3 à 5 alertes en
cascade. La première ouvre l'incident ; les suivantes, dans une fenêtre de 10 s,
sont **rattachées** au même incident et publiées dans `detail.alerts`, au lieu
d'être ignorées pendant le verrou.

### 12.4 Le durcissement

Deux passes. La première a fermé **7 failles** qu'un jury CYBER exploite en
quelques `curl` ; l'audit de code en a ajouté **5** — soit **douze failles
F1 à F12**, documentées dans `docs/SECURITE.md` — puis **4 bugs critiques**
qui cassaient le service ou faussaient une mesure.

| # | Faille | Ce qu'elle permettait | Correction |
|---|---|---|---|
| F1 | login marchand | un JWT valide avec n'importe quel `api_secret` | SHA-256 + table de secrets, **503 fail-closed** |
| F2 | `/admin/*` payment et WAF | quarantaine de 25 outputs, anneau à 64, déception désarmée | `SERVICE_TOKEN`, comparaison à temps constant |
| F3 | `/incident`, `/state/report` | playbook déclenché par n'importe qui | idem F2 |
| F4 | PIN `1234` en dur | terminal ouvert avec une valeur publique | PIN généré, blocage après 5 refus |
| F5 | anneau non borné | 100 000 leurres = saturation CPU | plafond à 64 |
| F6 | `/ingest` ouvert | évasion : diluer la fenêtre ML | jeton de service obligatoire |
| F7 | entrées de la boutique | `"3"` converti, SKU `X'; DROP TABLE …` stocké | `strict=True`, motif de SKU, **422** |
| F8 | double dépense concurrente | deux `curl` simultanés passaient tous les deux | key image réservée **avant** le premier `await` |
| F9 | double débit au double-clic | deux commandes et deux paiements pour un panier | panier réclamé atomiquement, relâché si échec |
| F10 | oracle d'existence | 500 si la commande existe, 404 sinon | format validé avant comparaison, comparaison en octets |
| F11 | verrouillage du PIN | `{"pin":"é"}` → 500 sans compter l'échec | comparaison en octets, refus compté |
| F12 | alerte SIEM morte | la règle Loki visait le mauvais service | sélecteur corrigé, vérifié par `check_compose.py` |

Chaque faille a **son test** dans `docs/SECURITE.md`.

**Les quatre bugs critiques de l'audit de code**, chacun avec son test de
régression :

| # | Bug | Effet réel | Test |
|---|---|---|---|
| C1 | SQL booléen contre PostgreSQL | `active = 1` : psycopg envoie un `int4`, PostgreSQL n'a pas de cast → « Application startup failed », conteneur en boucle | `test_postgres_dialect.py` (4 tests) — la preuve reste `docker compose up` |
| C2 | double dépense concurrente | la propriété centrale du projet contournable par deux requêtes simultanées | `test_une_double_depense_concurrente_est_refusee` |
| C4 | double débit au double-clic | deux commandes et deux paiements pour un panier | `test_deux_checkouts_sur_le_meme_panier` |
| S1 | oracle 404 | un `customer_ref` non-ASCII levait 500 si la commande existait, 404 sinon | `test_un_token_non_ascii_ne_fuit_pas_lexistence` |

**Douze points moyens** restent au tableau de l'audit ; ils sont écrits dans
`TODO.md` §0.6 avec un critère d'acceptation chacun. La deuxième passe (§12.6)
en ferme plusieurs. Les plus gênants de ceux qui restent : l'itération de
structures mutées sous charge (`/metrics`, `detector.py`), `StateBus.publish`
appelé depuis le threadpool, et les `assert` des listes blanches qui
disparaissent sous `python -O`. Les taire serait la faute ; les corriger tous
avant la soutenance serait un mauvais arbitrage.

### 12.5 Les mesures

**Test de charge** (`tools/load_test.py`, paliers de 1, 5, 10 et 20 clients,
rapport réécrit dans `docs/mesures-charge.md`) :

| Cible | Mesure | Comportement | Erreurs |
|---|---|---|---|
| Paiement (`GET /outputs`) | **630 req/s** | pas de rupture jusqu'à 20 clients : le débit sature, la latence file | 0 % |
| RAG (`POST /ask`, extractif) | **336 req/s** | stable ; c'est le disjoncteur qui l'a rendu possible | 0 % |
| Boutique (`POST /checkout`) | ~130 ms par achat à vide | **point de rupture** : p95 > 1 s dès 5 clients | 0 % |

**Le défaut que la mesure a trouvé** : au premier passage, `/ask` rendait
**0,5 req/s** avec 2 318 ms de p95 à un seul client, parce qu'il attendait le
timeout d'Ollama à chaque appel et que `/healthz` interrogeait Ollama. Le
disjoncteur et un `/healthz` sans appel réseau ont donné **×670 de débit**
(336 req/s) et p95 divisé par 580. C'est le type de défaut qu'une relecture de
code ne trouve pas.

**Chaos engineering** (`tools/chaos.py`) : **6 scénarios**, chacun avec un
invariant métier, pas seulement un code HTTP.

| Scénario | Invariant | État |
|---|---|---|
| `token` | jeton retiré → 503 fail-closed sur les routes d'administration, jamais 500 | **vérifié sur ce poste** |
| `ml` | arrêter le détecteur ne casse pas le paiement | à passer sous Docker |
| `remediation` | arrêter le moteur ne casse pas le paiement | à passer sous Docker |
| `crypto` | arrêter le noyau → 503 explicite, jamais 500 | à passer sous Docker |
| `shop` | arrêter la boutique laisse le paiement intact | à passer sous Docker |
| `rag` | arrêter le RAG ne touche ni la boutique ni le paiement | à passer sous Docker |

**Logs et base.** Loki + Promtail collectent les logs des conteneurs ;
**4 règles de logs** poussent à Alertmanager, dont **3 actionnables** par le
moteur de remédiation. `postgres-exporter` expose la base, avec
**3 alertes** : base indisponible, pool saturé, volume de la base. Grafana
passe à **13 panneaux**, dont 3 PostgreSQL.

### 12.6 Ce que la session a corrigé dans le code (deuxième passe)

La première passe livrait les briques ; la seconde a fermé ce qui restait entre
elles. Ces corrections sont dans le code, pas seulement dans ce document.

| Correction | Ce que ça change |
|---|---|
| **Corrélation multi-alertes** | un incident unique, alertes rattachées dans `detail.alerts` (fenêtre 10 s) |
| **`/tx/pay` idempotent par `reference`** | un rejeu renvoie le résultat déjà émis, pas un second paiement |
| **Rate limit optionnel sur `/tx/pay`** | `TX_PAY_RATE_LIMIT`, désactivé par défaut pour ne pas fausser les mesures |
| **Reconnexion PostgreSQL du registre** | backoff 1 s → 30 s, compteur `lost_writes` : une perte silencieuse redevient visible |
| **Balayage des commandes `pending`** | TTL de 120 s, passage en `failed` et **restitution du stock** |
| **Images produit servies** | `/static/products/*.svg`, repli typographique au SKU si l'image manque |
| **Garde « dernier admin » atomique** | `BEGIN IMMEDIATE` : deux rétrogradations concurrentes → une passe, l'autre en 409 |
| **Console Ansible alimentée** | `ansible_stdout_tail` + `ansible_rc` affichés dans le quadrant remédiation |
| **3 panneaux PostgreSQL dans Grafana** | connexions actives, taille de la base, transactions longues |
| **`tools/run_tests.ps1`** | les mêmes suites sous Windows PowerShell, récapitulatif et codes de sortie identiques |
| **`tools/preflight.py`** | pré-vol de soutenance : secrets, PIN réellement transmis, santé des services, dépôt livrable |

Plusieurs de ces lignes figurent encore comme « à faire » dans `TODO.md` §0.6 :
le fichier n'a pas été remis à jour après cette deuxième passe.

### 12.7 Ce que la session n'a pas fait

| Élément | Pourquoi | Où c'est dit |
|---|---|---|
| **Bulletproofs** | preuves de domaine : 3 à 5 jours, hors du temps de la session | `TODO.md` §1.1 |
| **AppRole Vault** | le playbook utilise encore `VAULT_TOKEN` | `TODO.md` §1.2 |
| **Vivacité anti-photo** | une photo imprimée passe le facteur visage | `TODO.md` §1.0, `/healthz` |
| **TLS** | la démo est en `localhost`, la caméra l'exige | `TODO.md` §0.5.1 |
| **Firmware non flashé** | pas de carte sous la main | `hardware/led_bandeau_esp8266/README.md` §4 |
| **Ollama non branché** | pas de PC IA disponible ; le RAG répond en extractif | `TODO.md` §0.5.3 |
| **Mesures sous Docker/PostgreSQL** | le poste de développement n'a ni Docker ni `cargo` ; les chiffres §12.5 viennent d'un lancement hors conteneurs | `docs/mesures-charge.md` |
| **5 scénarios de chaos** | ils arrêtent des conteneurs : impossibles sans Docker | §12.5, `tools/chaos.py` |

### 12.8 Résultat de la session

| Suite | Tests |
|---|---|
| `remediation-engine` | 27 |
| `ml-detector` | 12 |
| `payment-api` | 43 (dont 17 registre/idempotence) |
| `shadow-api` | 4 |
| `auth-service` | 9 |
| `backoffice` | 29 |
| `shop-api` | 36 (dont 4 dialecte PostgreSQL) |
| `rag-assistant` | 16 |
| `waf` | 10 |
| `hardware/bridge` | 9 |
| **Sous-total pytest** | **195** |
| `test_incident_flow.py` (mode script) | 5 |
| Rust unitaires | 9 |
| Rust HTTP | 10 |
| **Total** | **219** |

Mesuré **sur le poste Windows**, sans Docker et sans `cargo` : **195 tests
pytest verts** à la mesure de la session. Les 9 tests unitaires Rust et les
10 tests HTTP n'ont pas été exécutés ici (ni `cargo`, ni pile démarrée) :
**à passer sur le PC hôte** avec `.\tools\run_tests.ps1`.

Ce que ça change pour la soutenance :

- le SI a une **cible crédible** : un site de paiement avec base, front, back,
  historique et preuve cryptographique ;
- les attaques CYBER ont un **résultat chiffré** : 6 familles, 6/6 refusées,
  0 commande frauduleuse, stock jamais négatif, service toujours vivant ;
- les limites sont **écrites et datées**, y compris celles de cette session ;
- le bundle du dashboard passe à **206,82 kB** (201,76 kB avant) : le parcours
  d'achat et le quadrant assistant tiennent dans le même build.

Reste à faire avant de le dire « validé » : repasser les 219 tests, les 5
scénarios de chaos restants et les mesures de charge **sur la pile Docker du PC
hôte**. Tant que ce n'est pas fait, ces résultats valent pour le poste de
développement, pas pour l'environnement de démonstration.
