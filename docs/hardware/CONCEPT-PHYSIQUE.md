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

**Une seule chronologie** : celle de [`docs/DEMO.md`](../DEMO.md) §2. Le tableau
ci-dessous la reprend à l'identique et ajoute seulement le rendu de l'objet
physique. Les temps ne sont pas une variante : si l'un des deux documents
change, l'autre change.

| Temps | Action | État logiciel | Rendu terminal / bandeau | Rendu dashboard |
|---|---|---|---|---|
| 0–5 s | le présentateur tape son **identifiant** | la caméra USB s'ouvre, mot de passe fermé | LED éteinte | champ visage actif |
| 5–11 s | **scan du visage** | descripteur comparé à l'enrôlement | **vert clignotant** | miroir de l'état, caméra |
| 11–18 s | **mot de passe** | deux facteurs validés | **vert fixe 30 s** | dashboard ouvert |
| 18–23 s | ouverture de la **vitrine** | catalogue de 6 articles en base | LED éteinte | catalogue, images, stock |
| 23–28 s | deux articles au **panier** | stock réservé | — | panier, total recalculé |
| 28–33 s | clic **Payer** | commande `paid`, key image | — | preuve de paiement |
| 33–38 s | **historique** | commande, paiement, événements | — | historique client |
| 38–42 s | **PC attaquant** : `attack.py poison` | score d'anomalie franchit le seuil appris | **LED rouges** | badge SOUS ATTAQUE |
| 42–46 s | *aucune action* | `remediation-engine` → Ansible | **balayage orange** | badge REMEDIATION |
| 46–52 s | *aucune action* | déception, quarantaine, anneau 11 → 25 | balayage orange | journal Ansible qui défile |
| 52–57 s | **paiement de preuve** avec le **PIN du jour** | transaction confirmée | **vert fixe** | badge RETABLI, nouvelle ligne |
| 57–60 s | l'attaquant relance `poison` | requêtes déroutées vers le honeypot | vert fixe | déception active |

Le geste de 52 s est le cœur de la démo : c'est la preuve qu'un **client** peut
de nouveau payer. Il ne faut jamais le présenter comme une action
d'administration. Le paiement de la boutique (28–33 s) est un paiement **en
ligne** : il ne demande pas le PIN, qui authentifie le terminal de paiement
(saisi sur le pavé du dashboard, cf. `docs/DEMO.md` §0) et sert à la transaction
de preuve.

## 3. Attaque via le terminal (données réelles pour le ML)

Le clavier n'est pas décoratif : il produit les features que `ml-detector` attend.
Le point d'entrée est `POST /pin/verify` dans `payment-api` (compteur de refus sur
une fenêtre de 30 s, `GET /pin/stats` pour le dashboard).

| Feature (`services/ml-detector/features.py`) | Origine physique | État |
|---|---|---|
| `key_image_rejection_ratio` | ratio de PIN refusés (`/pin/verify`) | **branché** |
| `inter_arrival_median` | cadence de frappe / de requêtes | branché (horodatages) |
| `tx_latency_p95` | temps de réponse perçu par le terminal | branché |
| `decoy_median_age_days` | âge médian des leurres tirés (`/decoys`) | branché (registre réel) |
| `ring_age_skewness` | asymétrie des âges de l'anneau | branché (registre réel) |
| `ring_verify_cpu_us` | temps de vérification cryptographique | branché, **mesuré ~2 500 µs** (2 502 ± 573) |

Les six features sont donc alimentées par le vrai chemin. Une seule porte le
signal de l'attaque principale (`decoy_median_age_days`, AUC 1,00) ; les autres
servent les autres familles d'attaques. `ring_verify_cpu_us` mesure la
vérification **bLSAG réelle** du noyau Rust (ce n'est plus un placeholder) :
environ **2 500 µs** par signature sur le poste de démonstration, avec un écart
type de 573 µs. C'est cette variabilité qui rend la feature exploitable : une
rafale de vérifications fait monter la latence p95 avant même que le score ML ne
franchisse son seuil.

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
| `hardware/led_bandeau_esp8266/src/main.cpp` | firmware ESP8266 de la consigne additionnelle (LED bicolore, interrogation du back-office) |
| `services/remediation-engine/main.py` | bus d'événements `/events` et `/state` |

Le port série est en lecture/écriture seule : **aucune commande du terminal ne remonte
vers le moteur de remédiation.**

---

## 9. Bandeau LED ESP8266 (second objet)

Le terminal à anneau (ESP32) reflète l'état **du service** (NORMAL → SOUS ATTAQUE
→ REMEDIATION → RETABLI). Le bandeau LED (ESP8266) est un **second objet**, avec
un autre rôle : il reflète l'état **de l'authentification du back-office**. Les
deux ne partagent ni firmware, ni contrat, ni chemin réseau — et c'est
volontaire : une panne de l'un ne dit rien de l'autre.

| | Terminal à anneau | Bandeau LED |
|---|---|---|
| Carte | ESP32 | ESP8266 (NodeMCU / D1 mini) |
| Source | SSE `remediation-engine` → pont USB | `GET /led/state` sur `backoffice:8086` |
| Sens | le PC pousse l'état | l'ESP **interroge** toutes les 300 ms |
| Ce qu'il montre | l'état du service | l'étape d'authentification |

### Rôle

Le bandeau rend l'authentification **lisible à distance**, sans regarder
l'écran : le jury voit qu'un visage a été reconnu, qu'un mot de passe est
attendu, ou qu'un facteur a été refusé. C'est un miroir, pas un facteur : il ne
signe rien, ne déverrouille rien et ne déclenche aucune remédiation.

### Contrat d'état

Le back-office publie l'état ; le firmware l'interroge
(`GET /led/state?device=resilience-led`, en-tête `X-LED-Token`).

| `state` | LED | Déclencheur | Temporisation |
|---|---|---|---|
| `off` | éteinte | démarrage, déconnexion, inactivité | — |
| `green_blink` | vert clignotant 2 Hz | visage reconnu, mot de passe attendu | jusqu'à la saisie |
| `green_solid` | vert fixe | mot de passe validé | **30 s** puis extinction |
| `red_blink` | rouge, **10 flashs** | mot de passe refusé | puis extinction |
| `red_solid` | rouge fixe | visage non reconnu | **30 s** puis extinction |

Deux garanties :

1. **Le firmware est maître du temps** : les 30 s et les 10 flashs sont comptés
   localement, donc la séquence en cours se termine même si le back-office
   disparaît.
2. **Pas de vert mensonger** : sans contact HTTP depuis 5 s, la LED s'éteint. Un
   bandeau vert alors que le service est tombé serait pire que pas de bandeau.

Un état inconnu retombe sur `off` : le contrat est fermé.

### Fréquence de polling : 300 ms

L'ESP **interroge** le PC hôte, il ne reçoit pas de POST. C'est le sens imposé
par le réseau : le PC est le point d'accès (`192.168.137.1`), l'ESP est derrière
son NAT. Un POST serveur → ESP demanderait une IP fixe, une réservation DHCP et
une règle de pare-feu entrante — trois causes de panne le jour J pour 300 ms de
latence en moins.

Le coût est négligeable : une trame de moins de 200 octets toutes les 300 ms,
filtrée par ArduinoJson, et un compteur `bo_led_polls_total` côté back-office.

### Indépendance

**Débranché, le parcours d'authentification est identique.** Le navigateur reste
la source de vérité ; l'ESP ne fait que lire. Les cas de panne sont assumés :

| Panne | Comportement |
|---|---|
| ESP débranché | aucune erreur côté back-office, l'authentification continue |
| WiFi perdu | LED éteinte, reconnexion toutes les 5 s |
| Back-office arrêté | LED éteinte au bout de 5 s |
| Jeton `LED_DEVICE_TOKEN` faux | `401`, LED éteinte — jamais un état inventé |

Montage, flashage (CLion + PlatformIO) et recette en 5 tests :
[`hardware/led_bandeau_esp8266/README.md`](../../hardware/led_bandeau_esp8266/README.md).
Le point d'accès `RESILIENCE-G1` et la règle de pare-feu du port 8086 sont
décrits dans [`docs/INSTALLATION-LOCAL.md`](../INSTALLATION-LOCAL.md) §3.
