# Démonstration — scénario de 3 minutes

> Runbook de la soutenance. À imprimer ou garder ouvert sur le PC Hôte.
> Grille visée : démonstration en direct (25 pts), Clip Chrono (15 pts),
> expertise technique (10 pts), gouvernance (10 pts).

---

## 0. Avant d'entrer dans la salle (J-1 et J-0)

```bash
# 1. La pile tourne
cd Shadow && docker compose up -d && docker compose ps   # tout Up / healthy

# 2. La chaîne est verte (2 minutes)
python tools/e2e_demo.py

# 3. Le dashboard répond
open http://localhost:5173
```

Trois vérifications qui évitent 90 % des incidents de dernière minute :

| Contrôle | Commande | Attendu |
|---|---|---|
| Modèle chargé | `curl -s localhost:8083/healthz` | `"model": "ready"` |
| Webhook sain | idem | `"last_webhook_error": null` |
| Noyau crypto prêt | `curl -s localhost:8080/healthz` | `"crypto_core": "ready"` |

**Réinitialiser la déception** entre deux répétitions :
`python tools/attack.py reset`

---

## 1. Répartition des rôles

| Rôle | Qui | Où | Fait quoi |
|---|---|---|---|
| Présentateur | 1 personne | PC Hôte, debout | parle, montre le dashboard, tape le paiement final |
| Attaquant | 1 personne | **PC Attaquant** (poste distinct) | lance `tools/attack.py poison` |
| Chronométreur | 1 personne | salle | chronomètre, annonce les temps |
| Régie | 1 personne | PC Hôte | ne touche à rien sauf incident |

Règle absolue : **aucune intervention humaine** entre l'attaque et la reprise.
Si quelqu'un touche au clavier pendant la remédiation, l'axe 1 est perdu.

---

## 2. Le scénario, seconde par seconde

| Temps | Action | Ce qu'on voit | Ce qu'on dit |
|---|---|---|---|
| 0–10 s | Le présentateur tape `1234` sur le pavé du dashboard | LED bleu/violet, badge **NORMAL**, `TX DE PREUVE : OK` | « Le service est nominal : le client paie, le système le prouve en continu. » |
| 10–20 s | Il tape `0000` deux fois | Compteur de refus qui monte, taux en rouge | « Surface d'attaque physique : le terminal alimente le ML en données réelles. » |
| 20–25 s | **L'attaquant lance** `python tools/attack.py poison` | Score ML qui grimpe, badge **SOUS ATTAQUE** | « Attaque depuis un poste distinct : l'attaquant empoisonne les leurres de l'anneau. » |
| 25–30 s | Rien | Badge **REMEDIATION**, console Ansible | « Aucune intervention. Le détecteur a franchi son seuil appris et déclenche le playbook. » |
| 30–40 s | Rien | Journal Ansible qui défile : déception, quarantaine, durcissement | « Déception active, quarantaine des outputs compromis, anneau durci de 11 à 25. » |
| 40–45 s | Rien | Badge **RETABLI** | « Reprise en 7 secondes. La preuve, c'est la transaction suivante. » |
| 45–55 s | **Le présentateur retape un paiement** `1234` | Vert fixe, nouvelle ligne | « Un client peut de nouveau payer. C'est ça, la résilience. » |
| 55–60 s | L'attaquant relance `poison` | Requêtes **déroutées** vers le honeypot | « L'attaquant, lui, tape maintenant dans le vide : il est contenu. » |

**Le geste de 45 s est le cœur de la démo.** C'est la preuve qu'un *client*
paie de nouveau. Ne jamais le présenter comme une action d'administration.

---

## 3. Les quatre attaques disponibles

```bash
python tools/attack.py list      # rappel des commandes
python tools/attack.py poison    # attaque principale — détectée à 100 %
python tools/attack.py replay    # rejeu de key image — bloqué en 409
python tools/attack.py burst     # rafale automatisée — cadence machine
python tools/attack.py malformed # entrées invalides — 400, jamais de panic
python tools/attack.py all       # les quatre, pour une répétition
```

| Attaque | Ce qu'elle exploite | Ce qui la détecte |
|---|---|---|
| `poison` | Tirage **uniforme** des leurres au lieu de la gamma biaisée récents | `decoy_median_age_days` (AUC 1,00) |
| `replay` | Même output, donc même key image | Anti-double-dépense (409) + télémétrie |
| `burst` | Cadence machine (50 ms) au lieu d'humaine | `inter_arrival_median` |
| `malformed` | Anneau trop court, clés invalides | 400 propre, pas de panic |

---

## 4. Ce qu'il faut montrer à l'écran (Clip Chrono)

Le clip doit être **continu, sans coupe, 60 secondes**. Cadrage conseillé :

```
┌──────────────────────────────────────────────────────────┐
│  [terminal physique]        [dashboard plein écran]      │
│   anneau de LEDs            badge d'état + score ML      │
│                             console Ansible              │
│                             audit view-only              │
└──────────────────────────────────────────────────────────┘
```

Trois éléments doivent rester lisibles **à distance** :

1. le **badge d'état** (NORMAL → SOUS ATTAQUE → REMEDIATION → RETABLI) ;
2. le **score ML et son seuil** (deux nombres, pas une courbe) ;
3. l'**anneau de LEDs** (rouge = attaque, orange = remédiation, vert = reprise).

Le quadrant « Audit view-only » est un bonus : il montre qu'un auditeur lit le
montant **sans pouvoir dépenser**. C'est l'argument DORA art. 19.

---

## 5. Q&A — les cinq questions qui viendront

**« Votre détection, c'est un seuil fixe déguisé ? »**
Non. Le seuil est appris sur une capture de trafic réel (1 089 fenêtres), prend
le max de deux estimateurs, et est stocké dans `threshold.json` avec les
métriques. `grep -r "threshold.*=.*[0-9]" services/ml-detector/*.py` ne renvoie
aucune constante d'exploitation. Le seuil runtime mesuré est **refusé** s'il
provient d'un autre modèle (`runtime_threshold_ignored`).

**« Une seule feature porte votre détection, ce n'est pas fragile ? »**
C'est assumé et mesuré. `decoy_median_age_days` a une AUC de 1,000 sur
l'attaque principale, et elle est physiquement justifiée : Monero tire selon une
gamma biaisée récents (3,6 j mesurés), un attaquant pioche uniformément (19-31 j).
Les cinq autres features couvrent les autres familles d'attaques (rejeu, rafale,
anneau malformé). Un modèle à une seule feature ne détecterait qu'une attaque.

**« Votre crypto, c'est du vrai Monero ? »**
C'est un modèle Monero **simplifié mais réel** : Ristretto255, adresses
one-time par ECDH, key image `I = x·H_p(P)` avec un vrai hash-vers-groupe (donc
pas de key image forgeable), engagements de Pedersen, signature d'anneau liée
**bLSAG**, et déchiffrement par View Key séparée. Ce n'est pas CLSAG (la
signature est ~2× plus grosse) et il n'y a pas de Bulletproofs : un montant
négatif modulo l passerait la vérification d'équilibre. Les deux écarts sont
dans `/healthz` et dans le dossier de gouvernance.

**« Et si l'objet physique tombe ? »**
Rien. Le pont est en lecture seule, aucun abonné SSE n'est obligatoire, et
aucun chemin ne va du terminal vers le moteur de remédiation. C'est testé
(`test_hardware_bridge.py` vérifie l'absence de POST).

**« Comment savez-vous que ça marche sur un autre poste ? »**
On ne le sait pas encore, et c'est écrit. Le modèle doit être recalibré sur le
PC hôte (7 minutes, `capture_baseline.py` + `train.py`). Un seuil calibré
ailleurs produit des faux positifs — c'est arrivé trois fois pendant le projet.

---

## 6. Si ça casse pendant la démo

| Symptôme | Geste immédiat | Ce qu'on dit |
|---|---|---|
| Badge bloqué sur NORMAL, score bas | L'attaquant relance `poison` | « L'attaque n'a pas porté, je la relance. » |
| Score monte mais rien ne se passe | Vérifier `curl localhost:8083/healthz` → `last_webhook_error` | « Le webhook est cassé, je le signale. » |
| `REMEDIATION_FAILED` | `curl localhost:8084/history` → `ansible_rc` | « Le playbook a échoué, voici la trace. » |
| Dashboard figé | `docker compose restart front-dashboard` | « Le dashboard est un miroir, la chaîne continue. » |
| Plus rien ne répond | `docker compose up -d` puis `python tools/e2e_demo.py` | « Je relance la pile. » |

**Ne jamais** redémarrer `payment-api` en pleine démonstration : le registre
d'outputs est en mémoire et serait réinitialisé.

---

## 7. Après la démo

```bash
python tools/e2e_demo.py        # doit repasser au vert
python tools/attack.py reset    # désarmer la déception
curl -s localhost:8084/history | python3 -m json.tool   # trace pour le dossier
```

Récupérer les chiffres du jour pour le dossier technique :

```bash
python tools/e2e_demo.py | tee docs/mesures-soutenance.txt
curl -s localhost:8083/threshold | python3 -m json.tool
```
