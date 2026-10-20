# Dossier de gouvernance — RESILIENCE-ALPHA / SHADOW-SETTLE

> **Statut.** Ce document relie une analyse de risques (EBIOS RM), un plan de
> continuité et de reprise (PCA/PRA) et les exigences NIS2/DORA à
> l'**architecture réellement déployée** dans ce dépôt. Chaque mesure citée
> renvoie à un fichier ou un endpoint vérifiable. Les mesures non implémentées
> sont marquées **NON COUVERT** : c'est un choix de rédaction, pas un oubli.
>
> Ce qui rend ce dossier défendable en Q&A : il ne décrit pas un système idéal,
> il décrit celui qui tourne, avec ses limites assumées.

---

## 1. Périmètre

| Élément | Valeur |
|---|---|
| Système étudié | Plateforme de paiement fictive à confidentialité cryptographique |
| Mode de déploiement | `docker compose`, 19 services, un PC hôte |
| Périmètre technique | `services/*`, `front-dashboard`, `ansible/`, `hardware/` |
| Périmètre organisationnel | Un consortium d'étudiants, un PC attaquant, un PC hôte |
| Hors périmètre | Monero réel, réseau public, clients réels, données personnelles |
| Méthode | EBIOS RM (ANSSI, 2018), 5 ateliers |
| Référentiels | NIS2 (directive UE 2022/2555), DORA (règlement UE 2022/2554) |

La démonstration dure 3 minutes et se joue **une fois**. Le risque le plus
coûteux n'est donc pas une compromission : c'est une **panne silencieuse** le
jour J. L'analyse ci-dessous le reflète.

---

## 2. Atelier 1 — Cadrage et biens essentiels

### 2.1 Valeurs métier (ce qui a de la valeur)

| # | Valeur métier | Description | Impact si dégradé |
|---|---|---|---|
| VM1 | **Confidentialité des montants** | Un montant ne doit être lisible que par le porteur et l'auditeur habilité | Fuite = perte de confiance, non-conformité |
| VM2 | **Intégrité des transactions** | Une transaction acceptée ne doit pas pouvoir être rejouée | Double dépense = perte financière directe |
| VM3 | **Disponibilité du paiement** | Un client doit pouvoir payer pendant et après un incident | Perte de service, rupture |
| VM4 | **Preuve de reprise** | Le système doit démontrer qu'il s'est rétabli seul | Perte de la démonstration (25 pts) |
| VM5 | **Traçabilité auditable** | Un auditeur doit pouvoir reconstituer les faits | Non-conformité DORA art. 19 |

### 2.2 Biens supports (composants réels)

| # | Bien support | Service / fichier | Dépend de |
|---|---|---|---|
| BS1 | Noyau cryptographique | `services/monero-crypto-core` (Rust) | — |
| BS2 | API de paiement | `services/payment-api` (port 8080) | BS1 |
| BS3 | Registre des outputs | Journal PostgreSQL + cache mémoire | BS2, BS14 |
| BS4 | Détection ML | `services/ml-detector` (8083) | — |
| BS5 | Moteur de remédiation | `services/remediation-engine` (8084) | Ansible, BS2 |
| BS6 | Playbook de remédiation | `ansible/remediation_playbook.yml` | BS5 |
| BS7 | Source de vérité d'état | `StateBus` dans BS5 | — |
| BS8 | Dashboard | `front-dashboard` (5173) | BS5, BS4, BS2 |
| BS9 | Terminal physique | `hardware/bridge` + ESP32 | BS5 |
| BS10 | Point d'entrée / déception | `services/waf` (8085) | BS2, honeypot |
| BS11 | Honeypot | `services/shadow-api` (8090) | — |
| BS12 | Gestion des secrets | `vault` (8200) | — |
| BS13 | Authentification | `services/auth-service` (8082) | BS12 |
| BS14 | Base de données | `db-postgres` | — |
| BS15 | **Authentification forte du back-office** | `services/backoffice` (8086) + caméra USB | BS14 (SQLite), navigateur du PC hôte |
| BS16 | **Bandeau LED d'état** | `hardware/led_bandeau_esp8266` + ESP8266 | WiFi du point d'accès, BS15 |

### 2.3 Événements redoutés

| # | Événement redouté | Valeur métier affectée | Gravité |
|---|---|---|---|
| ER1 | Un montant est lu par un tiers non habilité | VM1 | 3 |
| ER2 | Une transaction est rejouée (double dépense) | VM2 | 4 |
| ER3 | Le paiement est indisponible pendant la démonstration | VM3, VM4 | 5 |
| ER4 | La remédiation ne se déclenche pas (panne silencieuse) | VM4 | 5 |
| ER5 | L'attaquant n'est pas contenu et continue de taper | VM3, VM5 | 3 |
| ER6 | L'auditabilité est perdue (aucune trace exploitable) | VM5 | 3 |
| ER7 | **Un tiers accède au back-office avec un identifiant ou un mot de passe volé** | VM1, VM5 | 4 |
| ER8 | **Un compte légitime est bloqué et la démonstration s'arrête** | VM3, VM4 | 3 |

---

## 3. Atelier 2 — Sources de risque

| # | Source | Motivation | Capacité | Pertinence ici |
|---|---|---|---|---|
| SR1 | Attaquant externe opportuniste | Financière | Moyenne | **Oui** — scénario principal de la démo |
| SR2 | Attaquant externe avancé | Financière / réputationnelle | Élevée | Partielle (pas d'APT réaliste sur 3 min) |
| SR3 | Erreur d'exploitation | Aucune | — | **Oui** — cause réelle de 7 pannes trouvées en recette |
| SR4 | Défaillance matérielle (PC, câble USB) | Aucune | — | **Oui** — l'objet physique peut tomber |
| SR5 | Compromission de la chaîne de build | Sabotage | Moyenne | Partielle (images non épinglées par digest) |

**Constat de recette à assumer** : sur les 11 défauts corrigés pendant le
projet, **aucun** n'était une attaque réussie. Tous venaient de SR3 (erreur
d'exploitation) ou SR4 (dépendance matérielle). C'est le vrai risque d'un
système qui doit tenir 3 minutes devant un jury.

---

## 4. Atelier 3 — Scénarios stratégiques

### SS1 — Empoisonnement des leurres (SR1)

Un attaquant qui veut casser la confidentialité d'un paiement peut tenter de
forcer l'anneau de signature à contenir des outputs qu'il contrôle. Il lit le
registre public (`GET /outputs`) et tire **uniformément**, alors qu'un wallet
honnête tire selon une gamma biaisée récents (`GET /decoys`).

- Chemin d'attaque : `POST /tx/submit` avec `decoy_ids` choisis.
- Détection : `decoy_median_age_days`, AUC 1,000 (§5.1).
- Réponse : quarantaine des outputs les plus anciens, durcissement de l'anneau.

### SS2 — Rejeu de transaction (SR1)

Rejouer une transaction déjà acceptée pour débiter deux fois.

- Chemin d'attaque : renvoyer une key image déjà dépensée.
- Détection : `key_image_rejection_ratio`.
- Réponse : rejet 409 **et** publication de la télémétrie (sinon l'attaque est
  invisible pour le ML — c'est un défaut corrigé, cf. `payment-api/main.py`).

### SS3 — Indisponibilité par saturation (SR1)

Rafale automatisée pour saturer le service et faire échouer la démo.

- Détection : `inter_arrival_median` (0,05 s en rafale contre 0,7 s en usage
  humain).
- Réponse : déception active (le trafic suspect part au honeypot), le client
  légitime continue de payer.

### SS4 — Panne silencieuse (SR3) — **le scénario le plus probable**

Un webhook pointe vers un nom d'hôte inexistant, une tâche Ansible échoue sous
`ignore_errors`, un seuil de calibration est périmé : le système a l'air sain
et ne détecte plus rien.

- Mesures : `/healthz` expose `last_webhook_error`, `runtime_threshold_ignored`,
  `crypto_core`, `crypto_last_error` ; `tools/e2e_demo.py` échoue bruyamment.

### SS5 — Perte de l'objet physique (SR4)

L'ESP32 est débranché en pleine démonstration.

- Réponse : le pont log et retente, la remédiation continue (aucun abonné SSE
  obligatoire). Analyse complète : `docs/hardware/CONCEPT-PHYSIQUE.md` §6.

---

## 5. Atelier 4 — Scénarios opérationnels et mesures

### 5.1 Chaîne de détection

| Mesure | Implémentation | Preuve |
|---|---|---|
| Features calculées par un module unique | `services/ml-detector/features.py` | 12 tests |
| Seuil **appris**, jamais codé en dur | `train.py` → `threshold.json` | `threshold_estimators` |
| Calibration hors échantillon (25 %) | `train.py` | `calibration_set: holdout_25pct` |
| Anti-rebond 3 fenêtres consécutives | `detector.py` | test dédié |
| Refus de conclure sous 8 événements | `MIN_EVENTS = 8` | test dédié |
| Sonde de liveness exclue | champ `synthetic` | test dédié |
| Purge de fenêtre avant toute sortie | `_purge_window` | test dédié |
| Seuil runtime refusé s'il vient d'un autre modèle | `model_trained_at` | `runtime_threshold_ignored` |

### 5.2 Chaîne cryptographique

| Mesure | Implémentation | Preuve |
|---|---|---|
| Signature d'anneau liée (bLSAG) | `monero-crypto-core/src/main.rs` | 9 tests Rust + 10 tests HTTP |
| Key image anti-double-dépense | `I = x·H_p(P)`, hash-vers-groupe sans log discret connu | test de forge rejetée |
| Confidentialité des montants | chiffrement par masque dérivé de l'ECDH | `audit-view` |
| Audit **view-only** (sans Spend Key) | `GET /audit/last`, `/crypto/audit-view` | test dédié |
| Engagements de Pedersen équilibrés | `/crypto/commit-split`, `/crypto/commitment-balance` | test de désaccord détecté |
| Entrées invalides → 400, jamais de panic | `BadRequest` | test Rust |

### 5.3 Remédiation et déception

| Mesure | Implémentation | Preuve |
|---|---|---|
| Remédiation asynchrone (pas de timeout) | `/incident` répond 202 | 5 tests d'intégration |
| Un seul incident à la fois | `asyncio.Lock` | test de concurrence |
| Quarantaine **ciblée** (outputs les plus anciens) | `/admin/quarantine-outputs` | 1 test |
| Durcissement réel de l'anneau | `/admin/ring-size` | 1 test |
| Déception active branchée | `waf` → `shadow-api` | 10 tests |
| Le client légitime n'est **jamais** dérouté | règle sur l'âge des leurres | test dédié |
| Playbook sans tâche décorative | `remediation_playbook.yml` | `ansible_rc`, sortie Ansible |
| Aucun accès Docker depuis le moteur | socket retiré | `docker-compose.yml` |

### 5.4 Gestion des secrets

| Mesure | Implémentation | Limite |
|---|---|---|
| Clé JWT dans Vault (KV v2) | `auth-service/main.py` | AppRole non provisionné |
| Fallback explicite, jamais silencieux | `/healthz` → `jwt_key_source` | — |
| Aucun secret dans le dépôt | `.gitignore`, `.env.example` | — |
| Rotation de clé par le playbook | étape 3, conditionnelle | dépend d'un token de service |
| **Authentification à deux facteurs du back-office** | identifiant + visage (SFace) + mot de passe, `services/backoffice` | pas de détection de vivacité |
| **Anti-bruteforce borné** | 2 échecs → 5 min, compteur en base, `Retry-After` | un déni de service ciblé reste possible (ER8) |
| **Journal d'audit des tentatives** | table `audit`, écran « Habilitations » | pas d'export signé (hors périmètre) |
| **Moindre privilège** | 3 rôles, dernier admin protégé, sessions révocables | pas de revue d'accès périodique |

---

## 6. Atelier 5 — Risques résiduels

| # | Risque résiduel | Probabilité | Gravité | Traitement |
|---|---|---|---|---|
| RR1 | **Pas de preuve de domaine (Bulletproofs)** : un montant négatif modulo l passerait la vérification d'équilibre | Faible (attaquant authentifié) | 3 | **Accepté et documenté** (`/healthz` → `not_implemented`) |
| RR2 | bLSAG au lieu de CLSAG : signature ~2× plus grosse | Certaine | 1 | Accepté (aucun impact fonctionnel) |
| RR3 | Registre des outputs en mémoire : perte au redémarrage | **Fermé** | 2 | Journal PostgreSQL (`registry.py`, 8 tests) |
| RR4 | Modèle ML calibré sur un seul poste | Moyenne | 4 | **Réduit** : `capture_baseline.py` rejouable en 7 min, seuil runtime refusé s'il est périmé |
| RR5 | AppRole Vault non provisionné | Certaine | 2 | Accepté en démo, écrit dans le playbook |
| RR6 | Règles WAF limitées à l'âge des leurres | Moyenne | 2 | Accepté : le ML reste le détecteur, le WAF ne fait que filtrer au bord |
| RR7 | Images Docker non épinglées par digest | Moyenne | 3 | Recommandé (§9) |
| RR8 | **Pas de détection de vivacité faciale** : une photo imprimée passerait le facteur visage | Moyenne | 2 | **Accepté et documenté** (`/healthz` → `liveness: not_implemented`) ; le mot de passe reste exigé |
| RR9 | **Blocage de 5 min exploitable en déni de service** : un tiers peut bloquer un compte connu | Faible | 2 | Accepté (imposé par la consigne) ; déblocage par un administrateur, alerte `ComptesBloques` |
| RR10 | Back-office en HTTP sur `localhost` | Faible (trafic local) | 2 | Accepté en démo ; TLS recommandé en production |

---

## 7. PCA / PRA

### 7.1 Objectifs de continuité

| Service | RTO visé | RPO | Justification |
|---|---|---|---|
| `payment-api` | **15 s** | 0 (registre PostgreSQL) | C'est le service que la démo doit montrer disponible |
| `ml-detector` | **30 s** | n/a (modèle figé dans l'image) | Un redémarrage recharge les artefacts |
| `remediation-engine` | **10 s** | perte de `/history` | L'état est republié au démarrage (`IDLE`) |
| `monero-crypto-core` | **10 s** | n/a (stateless) | Aucun état à restaurer |
| `waf` | **5 s** | perte du compteur de déception | La déception est réarmée par le playbook |
| `front-dashboard` | **5 s** | n/a | Recalcule tout depuis SSE + `/score` |
| `vault` | **60 s** | données persistées (`vault-data`) | Hors chemin critique de la démo |
| `backoffice` | **15 s** | comptes, visages et blocages persistés (`bo-data`) | Un redémarrage ne perd ni les comptes ni les blocages |
| Bandeau LED ESP8266 | **0** (non critique) | n/a | S'éteint s'il ne joint plus le back-office |
| Terminal physique | **0** (non critique) | n/a | Miroir d'état, jamais nécessaire |

**RTO agrégé de la chaîne P0 : 15 s** — contre 6,3 s mesurées en reprise
automatique. La marge est volontaire : le RTO couvre le cas où la remédiation
échoue et où l'exploitant redémarre à la main.

### 7.2 Scénarios de continuité

| Panne | Détection | Continuité | Reprise |
|---|---|---|---|
| `payment-api` tombe | `/healthz` + sonde `auto-probe` (`ok: false`) | Le WAF renvoie 502 explicite | `docker compose up -d payment-api` (coupure de quelques secondes, registre conservé) |
| `ml-detector` tombe | `e2e_demo.py` s'arrête au contrôle 1 | **Le paiement continue** (le ML n'est pas dans le chemin critique) | Redémarrage, artefacts rechargés |
| `remediation-engine` tombe | `/state` injoignable | Détection toujours visible sur `/score` | Redémarrage, `IDLE` republié |
| `waf` tombe | `/healthz` | Trafic direct sur `payment-api` possible | Redémarrage, `/admin/reset` |
| Ansible absent | `ansible_rc = 127` | — | `docker compose build remediation-engine` |
| Terminal débranché | Log du pont | Chaîne intacte | Rebrancher, resynchronisation `/state` |
| Seuil périmé | Score au repos > seuil | — | `capture_baseline.py` + `train.py` (7 min) |
| Registre épuisé | 409 en série | — | Ré-alimentation automatique + redémarrage |

### 7.3 Sauvegarde

| Donnée | Stratégie | Test de restauration |
|---|---|---|
| Code et configuration | Git (dépôt distant) | `git clone` sur un poste neuf |
| Artefacts ML | Reconstruits par `train.py` (déterministe, graine fixe) | `train.py --check` |
| Secrets | Vault (`vault-data`) ; jamais dans Git | `vault kv get secret/jwt` |
| Registre des outputs | PostgreSQL (`outputs`, `key_images`) | `pg-data` |
| Modèle 3D | `hardware/models/*.scad` (paramétrique) | Réimpression |

---

## 8. NIS2 et DORA — correspondance

### 8.1 NIS2 (directive UE 2022/2555), article 21

| Exigence | Mesure dans ce système | Preuve |
|---|---|---|
| 21.2.a — analyse de risques | Ce document, EBIOS RM | §2-6 |
| 21.2.b — gestion des incidents | Détection ML + remédiation autonome | `e2e_demo.py` |
| 21.2.c — continuité d'activité | PCA/PRA, RTO par service | §7 |
| 21.2.d — sécurité de la chaîne d'approvisionnement | Versions figées (`requirements.txt`, `Cargo.lock`) | `--locked` |
| 21.2.e — sécurité du développement | Tests automatisés (164), revue par la mesure | `tools/run_tests.sh` |
| 21.2.f — évaluation de l'efficacité | `validate_real.py`, `analyze_separation.py` | §5.1 |
| 21.2.g — hygiène et formation | Hors périmètre technique | — |
| 21.2.h — cryptographie | Ristretto255, bLSAG, Pedersen, View Key séparée | §5.2 |
| 21.2.i — ressources humaines, contrôle d'accès | PIN terminal, JWT court (300 s) | `auth-service` |
| 21.2.j — MFA | **NON COUVERT** (PIN seul) | — |

### 8.2 DORA (règlement UE 2022/2554)

| Exigence | Mesure | Preuve |
|---|---|---|
| Art. 5 — gouvernance du risque TIC | Ce dossier, propriétaire identifié par brique | §2.2 |
| Art. 6 — cadre de gestion du risque | EBIOS RM + registre des risques résiduels | §6 |
| Art. 9 — protection et prévention | Chiffrement des montants, anti-rejeu, durcissement | §5.2 |
| Art. 10 — détection | ML non supervisé, seuil appris, anti-rebond | §5.1 |
| Art. 11 — réponse et rétablissement | Remédiation autonome + déception + preuve de reprise | §5.3 |
| Art. 12 — sauvegarde et restauration | §7.3 | — |
| Art. 13 — apprentissage | 11 défauts documentés et corrigés | `COMPTE-RENDU.md` §9 |
| Art. 17 — gestion des incidents | `/incident`, `/history`, `diagnostic` | `remediation-engine` |
| Art. 19 — **traçabilité** | Audit **view-only** : montants lisibles sans capacité de dépense | `/audit/last` |
| Art. 28 — risque tiers | **NON COUVERT** (pas de fournisseur critique) | — |

### 8.3 Ce que la démonstration prouve, et ce qu'elle ne prouve pas

**Prouvé** : détection non supervisée, remédiation sans intervention humaine,
preuve de reprise par une transaction réelle, containment de l'attaquant,
auditabilité sans capacité de dépense.

**Non prouvé** : résistance à un attaquant qui possède les clés, conformité
formelle (pas d'audit externe), MFA, gestion des tiers, plan de formation.

---

## 9. Recommandations à la direction

| Priorité | Recommandation | Coût | Effet |
|---|---|---|---|
| 1 | **Bulletproofs** (preuves de domaine) | 3-5 j | Ferme RR1, seule vraie faille cryptographique |
| 2 | **AppRole Vault** + politique dédiée | 0,5 j | Supprime le token de service du playbook (RR5) |
| 3 | ~~**Registre persistant**~~ — **fait** | 2 j | Ferme RR3 (`registry.py`, 8 tests) |
| 4 | Images Docker épinglées par digest + scan Trivy | 0,5 j | Ferme RR7, exigence NIS2 21.2.d |
| 5 | Recalibrage du ML sur le PC de démonstration | 0,25 j | Ferme RR4 |
| 6 | MFA sur le terminal (PIN + challenge) | 1 j | NIS2 21.2.j |
| 7 | Observabilité : scrape Prometheus + alertes sur `/healthz` | 1 j | Détection des pannes silencieuses (SR3) |

**Message de synthèse** : le système est *techniquement* résilient (reprise en
6,3 s, 0 faux déclenchement, containment de l'attaquant) et *honnêtement*
documenté. Le risque résiduel dominant n'est pas l'attaque : c'est la panne
silencieuse. Les priorités 5 et 7 sont donc les moins coûteuses et les plus
rentables pour la soutenance.

---

## 10. Ce qui n'est pas couvert

- **EBIOS RM atelier 5 formel** : les mesures sont listées et implémentées,
  mais aucun plan d'action chiffré ni indicateur de suivi.
- **Homologation** : pas d'audit externe, pas de pen-test formel.
- **RGPD** : aucune donnée personnelle traitée (montants et clés uniquement).
- **Plan de formation et sensibilisation** : hors périmètre étudiant.
- **PCA organisationnel** (astreinte, cellule de crise) : non traité.
