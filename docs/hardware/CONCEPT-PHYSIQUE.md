# Aspect physique — Terminal de paiement RESILIENCE-ALPHA

> Document de référence pour le dossier d'ingénierie (pilier DEV/CYBER + gouvernance).
> Version 1 — périmètre volontairement limité : **l'objet physique ne participe pas à la remédiation**.

## 1. Rôle de l'objet dans la démonstration

L'objet physique ne rapporte aucun point en soi. Il sert trois objectifs précis, tous
rattachés à la grille :

| Objectif | Axe de la grille |
|---|---|
| Rendre la **preuve de reprise** visible sans lire un JSON | Démo live (25 pts) |
| Montrer un état du service **avant / pendant / après**, lisible à distance | Clip Chrono (15 pts) |
| Fournir une **surface d'attaque crédible** (PIN, rafales de paiement) qui alimente le ML | Expertise technique (10 pts) |
| Supporter une réponse claire en Q&A : « et si l'objet tombe ? » | Gouvernance (10 pts) |

### Ce que l'objet ne fait PAS

- Il ne détient **aucune clé privée** et ne signe rien. Ce n'est pas un HSM, malgré le nom
  du dossier `ring_signer_hsm/` : c'est un terminal d'affichage et de saisie.
- Il n'est **pas dans le chemin critique** : s'il est débranché, la chaîne
  attaque → détection → remédiation → preuve fonctionne à l'identique.
- Il **ne peut pas déclencher de remédiation**. Aucun bouton, aucune touche, aucun code
  n'appelle `remediation-engine` en écriture.
- Il **ne remplace pas** le dashboard, qui doit pouvoir porter la démo seul.

## 2. Scénario de démonstration (Clip Chrono, 60 s sans coupe)

| Temps | Action physique | État logiciel | Rendu terminal | Rendu dashboard |
|---|---|---|---|---|
| 0–8 s | paiement de démonstration au clavier | tx confirmée | LED bleu/violet en rotation | badge NORMAL, quadrant tx |
| 8–15 s | 3 codes PIN erronés au clavier | `pin_failure_ratio` monte | LED bleu/violet, compteur d'échecs | pic de la courbe d'échecs |
| 15–20 s | PC attaquant : rafale de requêtes | score d'anomalie franchit le seuil appris | **LED rouges** | badge SOUS ATTAQUE, score en hausse |
| 20–35 s | *aucune action* | `remediation-engine` → Ansible | **balayage orange** | journal Ansible qui défile |
| 35–42 s | *aucune action* | tx de preuve auto confirmée | passage au vert | badge NORMAL |
| 42–55 s | **le présentateur retape un paiement** | nouvelle tx confirmée | **vert fixe + bip** | nouvelle ligne dans l'historique |
| 55–60 s | plan large | — | état stable | état stable |

Le geste de 42 s est le cœur de la démo : c'est la preuve qu'un **client** peut de nouveau
payer. Il ne faut jamais le présenter comme une action d'administration.

## 3. Attaque via le terminal (données réelles pour le ML)

Le clavier n'est pas décoratif : il produit les features que `ml-detector` attend.
Le point d'entrée est `POST /pin/verify` dans `payment-api` (compteur de refus sur
une fenêtre de 30 s, `GET /pin/stats` pour le dashboard).

| Feature (`services/ml-detector/detector.py`) | Origine physique | État |
|---|---|---|
| `key_image_rejection_ratio` | ratio de PIN refusés (`/pin/verify`) | **branché** |
| `inter_arrival_jitter` | cadence de frappe / de requêtes | branché (horodatages) |
| `tx_latency_p95` | temps de réponse perçu par le terminal | branché |

`decoy_entropy` et `ring_age_skewness` restent à brancher : ils demandent une
vraie table d'outputs, pas un compteur en mémoire. On ne les invente pas — un
modèle entraîné sur du bruit est pire qu'un placeholder assumé.

**Le clavier ne peut pas déclencher de remédiation.** Un PIN correct autorise une
transaction de démonstration, qui passe ensuite par `/tx/submit` comme n'importe
quel client. Aucun chemin ne va du terminal vers `remediation-engine`.

## 4. Architecture de la liaison

```
                      ┌──────────────────────┐
   PC Attaquant ────► │      payment-api     │
                      └──────────┬───────────┘
                                 │ télémétrie
                                 ▼
                      ┌──────────────────────┐
                      │      ml-detector     │
                      └──────────┬───────────┘
                                 │ POST /incident
                                 ▼
                      ┌──────────────────────┐        SSE /events
                      │  remediation-engine  │────────────┬──────────────┐
                      └──────────┬───────────┘            │              │
                                 │ ansible-playbook       ▼              ▼
                                 │               front-dashboard   hardware_bridge.py
                                 │                (anneau SVG)      (Windows, hôte)
                                 ▼                                       │ USB série
                              Ansible                                    ▼
                                                                     ESP32 + LEDs
                                                                         ▲
                                                                         │ clavier
                                                                         │
                                                             POST /tx/submit (client)
                                                                         │
                                                                         └──► payment-api
```

### Règles d'architecture

1. **Source de vérité unique** : `remediation-engine` publie l'état. Le dashboard et l'ESP32
   le consomment, ils ne le recalculent jamais.
2. **Un seul contrat d'état**, identique pour les deux rendus (voir §5).
3. **Découplage strict** : si `hardware_bridge.py` est arrêté, `remediation-engine` continue
   sans erreur (les abonnés SSE sont optionnels).
4. **Le pont tourne sur Windows**, pas dans un conteneur : le passthrough USB sous
   Docker Desktop/WSL2 est une cause de panne classique en démonstration.
5. **Série USB en primaire**, Wi-Fi en secours uniquement (`#ifdef`), car le point d'accès
   mobile Windows ajoute DHCP + pare-feu aux risques.

## 5. Contrat d'état

Format JSON, émis par `GET /events` (SSE) et disponible via `GET /state` :

```json
{
  "seq": 42,
  "state": "UNDER_ATTACK",
  "ts": 1760000000.0,
  "detail": { "anomaly_score": 1.83, "ring_n": 11, "key_image": "ab12…" }
}
```

Valeurs de `state` :

| Valeur | Signification | Rendu terminal |
|---|---|---|
| `IDLE` | aucun état reçu depuis le démarrage | LEDs éteintes |
| `NORMAL_TX` | transaction légitime confirmée | rotation bleu/violet |
| `UNDER_ATTACK` | seuil ML franchi | points rouges |
| `REMEDIATING` | playbook Ansible en cours | balayage orange/blanc |
| `RECOVERED_TX` | transaction de preuve confirmée | vert fixe |
| `REMEDIATION_FAILED` | playbook en échec ou probe KO | rouge fixe + double bip |

`seq` est strictement croissant : il permet au pont de détecter une reconnexion et
d'ignorer les doublons.

### Trame série (ESP32)

Le pont envoie une ligne ASCII par changement d'état :

```
STATE:UNDER_ATTACK\n
STATE:UNDER_ATTACK;SCORE:1.83;N:11\n     (mode enrichi, optionnel)
```

Le firmware répond `ACK:<trame>` pour permettre au pont de journaliser la synchronisation.

## 6. Reprise après panne (réponse Q&A)

| Panne | Comportement | Ce qu'on dit au jury |
|---|---|---|
| ESP32 débranché | le pont log et continue, dashboard intact | « l'objet est un miroir ; la chaîne de remédiation n'en dépend pas » |
| Pont arrêté | `/events` n'a plus d'abonné, aucune erreur | idem |
| État manqué pendant une reconnexion | le pont relit `/state` au connect | « l'état est idempotent et rejouable » |
| Leds figées | `/state` + mode `--replay` pour resynchroniser | « procédure de recette de 5 secondes » |

Ce tableau est le livrable attendu par l'axe gouvernance : il montre une analyse de
dépendance réelle, pas une intention.

## 7. Matériel et impression

### Matériel

| Élément | Rôle | Remarque |
|---|---|---|
| Terminal de paiement à clavier | IHM de saisie | déjà disponible |
| ESP32 | lecture clavier + pilotage LEDs + série | déjà disponible |
| Bandeau / anneau WS2812 | état visible à distance | alim 5 V séparée |
| Alimentation 5 V 3 A | LEDs + ESP32 | **ne pas** alimenter par l'USB du PC |
| Écran OLED SSD1306 (option) | score et état en clair | I2C, 128×64 |
| Servo MG90S (option) | trappe + jeton de preuve | mécanique fragile, à éviter en v1 |

### Pièces à imprimer

| # | Pièce | Matière | Remarque |
|---|---|---|---|
| 1 | Socle incliné 15° avec logement ESP32 | PLA, 0.2 mm, 3 périmètres | une seule pièce, < 180 mm |
| 2 | Montant + rainure LED | PLA | découpé pour tenir sur petit plateau |
| 3 | Diffuseur LED | **PETG translucide, 0 % remplissage** | indispensable pour la caméra |
| 4 | Plaque signalétique | PLA, changement de couleur | cadrage du clip |

Tolérances : **0,25 mm** sur les assemblages, **0,5 mm** sur les passages de câble.
Prévoir deux passes : une de vérification, une finale.

## 8. Ce qui est codé dans ce dépôt

| Fichier | Rôle |
|---|---|
| `hardware/bridge/hardware_bridge.py` | pont SSE → série, avec replay et reconnexion |
| `hardware/ring_signer_hsm/ring_signer_hsm.ino` | firmware ESP32 (états, effets LED, parseur de trame) |
| `services/remediation-engine/main.py` | bus d'événements `/events` et `/state` |

Le port série est en lecture/écriture seule : **aucune commande du terminal ne remonte
vers le moteur de remédiation.**
