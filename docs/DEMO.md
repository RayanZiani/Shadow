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
| Back-office prêt | `python tools/backoffice_check.py` | 0 échec, `face_engine: ready` |
| Caméra | ouvrir http://localhost:5173 | l'aperçu s'affiche, la LED de la webcam s'allume |

`tools/e2e_demo.py` publie aussi `backoffice_ready` dans son résumé : ce n'est
pas bloquant (le back-office n'est pas dans la chaîne P0), mais un `false` le
jour J se voit avant de commencer.

**Réinitialiser la déception** entre deux répétitions :
`python tools/attack.py reset`

**Réinitialiser le back-office** (comptes bloqués, sessions ouvertes) :
débloquer depuis l'écran « Habilitations », ou `docker compose restart backoffice`
(les comptes et les visages sont conservés dans le volume `bo-data`).

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

> **Version consigne additionnelle.** Les 18 premières secondes montrent
> l'authentification à deux facteurs (c'est la nouveauté demandée), puis la
> chaîne attaque → remédiation → preuve tient dans les 42 secondes restantes.
> Si le jury ne veut voir que la chaîne P0, commencer à 18 s : le tableau
> ci-dessous se lit alors de 18 s à 60 s, et la démonstration reste complète.

| Temps | Action | Ce qu'on voit | Ce qu'on dit |
|---|---|---|---|
| 0–6 s | Le présentateur tape son **identifiant** sur le dashboard | la **caméra USB du PC hôte** s'allume | « Le back-office n'accepte plus un simple mot de passe : l'identifiant déclenche la caméra. » |
| 6–12 s | Il lance le **scan du visage** | **LED verte clignotante** (bandeau ESP8266 + miroir à l'écran) | « Le visage est comparé à celui enrôlé pour ce compte. La carte passe au vert clignotant : le mot de passe est attendu. » |
| 12–18 s | Il tape le **mot de passe** | **LED verte fixe 30 s**, dashboard ouvert | « Deux facteurs. Si le visage ne correspond pas, la LED passe au rouge fixe 30 s et l'identifiant est vidé ; deux échecs bloquent cinq minutes. » |
| 18–26 s | Il tape `1234` sur le pavé du dashboard | LED bleu/violet, badge **NORMAL**, `TX DE PREUVE : OK` | « Le service est nominal : le client paie, le système le prouve en continu. » |
| 26–31 s | Il tape `0000` deux fois | Compteur de refus qui monte, taux en rouge | « Surface d'attaque physique : le terminal alimente le ML en données réelles. » |
| 31–36 s | **L'attaquant lance** `python tools/attack.py poison` | Score ML qui grimpe, badge **SOUS ATTAQUE** | « Attaque depuis un poste distinct : l'attaquant empoisonne les leurres de l'anneau. » |
| 36–40 s | Rien | Badge **REMEDIATION**, console Ansible | « Aucune intervention. Le détecteur a franchi son seuil appris et déclenche le playbook. » |
| 40–47 s | Rien | Journal Ansible qui défile : déception, quarantaine, durcissement | « Déception active, quarantaine des outputs compromis, anneau durci de 11 à 25. » |
| 47–52 s | Rien | Badge **RETABLI** | « Reprise en 7 secondes. La preuve, c'est la transaction suivante. » |
| 52–57 s | **Le présentateur retape un paiement** `1234` | Vert fixe, nouvelle ligne | « Un client peut de nouveau payer. C'est ça, la résilience. » |
| 57–60 s | L'attaquant relance `poison` | Requêtes **déroutées** vers le honeypot | « L'attaquant, lui, tape maintenant dans le vide : il est contenu. » |

**Le geste de 52 s est le cœur de la démo.** C'est la preuve qu'un *client*
paie de nouveau. Ne jamais le présenter comme une action d'administration.

**Le geste de 6 s est la réponse à la consigne additionnelle.** Ne pas couper
la caméra : c'est la preuve visuelle que l'authentification est réelle, et le
bandeau LED la rend lisible à distance sans regarder l'écran.

---

## 2 bis. La boutique (nouvelles règles du jury)

Le SI n'est plus seulement un tableau de bord : c'est un **site de paiement**.
La séquence d'achat tient en 25 secondes et se montre avant ou après la chaîne
P0. Elle prouve trois choses d'un coup : articles en base, paiement réel,
historique persisté.

| Temps | Action | Ce qu'on voit | Ce qu'on dit |
|---|---|---|---|
| 0–8 s | Ouvrir http://localhost:5173 (sans être connecté) | catalogue de 6 articles, stock affiché | « Le front vitrine lit la base : chaque article est un enregistrement, pas une page figée. » |
| 8–14 s | « Ajouter au panier » sur deux articles | panier, total recalculé | « Le panier est en base, le stock est vérifié à l'ajout. » |
| 14–20 s | Cliquer **Payer** | commande `paid`, key image affichée | « Le paiement n'est pas simulé : le noyau émet un output du montant exact et le dépense par une signature d'anneau. » |
| 20–25 s | Recharger la page | l'historique montre la commande | « La commande et sa preuve sont en base. Elles survivent à un redémarrage. » |

Puis, en back-office (identifiant + visage + mot de passe) :

| Action | Ce qu'on voit | Ce qu'on dit |
|---|---|---|
| « Habilitations » → « Boutique » | tableau des articles et des commandes | « Le back gère le catalogue et voit la preuve de chaque paiement. » |
| Modifier le stock, retirer un article | la vitrine se met à jour | « Une seule source de vérité : la base. » |

**Ce qu'il ne faut pas dire** : « c'est une démo ». Le stock est réellement
décrémenté, la commande est réellement payée, et un paiement refusé produit une
commande `failed` avec son motif.

**Q&A — « et si le paiement échoue ? »**
Le stock réservé est restitué dans la même seconde, la commande passe en
`failed`, le panier est conservé. C'est vérifié par un test
(`test_le_paiement_refuse_restitue_le_stock`) : pas de stock fantôme.

**Q&A — « comment l'IA agit-elle sur une alerte ? »**
Prometheus évalue 20 règles, Alertmanager pousse au moteur (`POST /alert`).
Une alerte **actionnable** ouvre un incident et lance le playbook ; une alerte
de confort (bandeau LED) est journalisée sans rien déclencher. Le chemin est le
même que pour le détecteur ML : un seul playbook, une seule preuve de reprise.

**Q&A — « votre assistant invente-t-il ? »**
Non. Il répond à partir de l'index du dossier et cite ses sources
(`docs/RAPPORT.md`). Sans Ollama, il passe en mode extractif et le dit :
`/healthz` → `generation.ollama: unreachable`. Un assistant qui cite vaut mieux
qu'un assistant qui devine.

---

## 3. Les attaques disponibles

```bash
python tools/attack.py list      # rappel des commandes
python tools/attack.py poison    # attaque principale — détectée à 100 %
python tools/attack.py replay    # rejeu de key image — bloqué en 409
python tools/attack.py burst     # rafale automatisée — cadence machine
python tools/attack.py malformed # entrées invalides — 400, jamais de panic
python tools/attack.py all       # les quatre, pour une répétition
python tools/attack.py shop      # 6 attaques sur la boutique (nouveau)
```

`attack.py shop` répond à « les CYBER doivent essayer de faire tomber le SI » :
six familles d'attaques sur la vitrine et son panier, en une seconde, avec un
code de sortie. Chaque attaque vérifie le code HTTP **et** l'absence d'effet de
bord (aucune commande créée, stock jamais négatif, service vivant).

```
quantités invalides      5/5 refusées (422)
panier forgé             4/4 refusés
injection SQL par SKU    3/3 refusées, catalogue intact
historique d'autrui      3/3 refusés, 0 commande retournée
course sur le stock      aucune survente
falsification de prix    montant facturé = prix en base
```

Détail des failles fermées et des limites assumées : `docs/SECURITE.md`.

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

**« Une photo de l'utilisateur suffit-elle à passer ? »**
Oui, aujourd'hui : il n'y a pas de détection de vivacité, et c'est écrit dans
`/healthz` (`liveness: not_implemented`). Le visage est un facteur d'identité,
pas un anti-spoofing ; le mot de passe reste le second facteur, et il n'est
demandé qu'après la reconnaissance. Ce qui est réellement couvert : un
identifiant volé ne suffit plus, un mot de passe volé ne suffit plus, et une
photo seule ne suffit pas non plus (il faut aussi le mot de passe).

**« Pourquoi la caméra est-elle ouverte par le navigateur et pas par un
conteneur ? »**
Parce que la consigne impose la caméra sur le **PC hôte**, et que Docker
Desktop/WSL2 n'a pas de passthrough webcam fiable. Le navigateur du PC hôte est
le seul composant qui a un accès garanti au matériel. Le serveur reçoit une
image JPEG ponctuelle, en calcule le descripteur, et n'en conserve **aucune**.

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
| La caméra ne s'ouvre pas | fermer Teams/Zoom, recharger la page ; vérifier que l'URL est `localhost` | « une autre application tient la caméra, je la libère » |
| « visage non reconnu » sur le bon utilisateur | se placer face à la lumière, visage entier dans le cadre, relancer le scan | « la lumière du vidéoprojecteur me met à contre-jour » |
| Compte bloqué après deux essais | écran « Habilitations » → débloquer (ou attendre 5 min) | « le blocage est une fonctionnalité, pas une panne » |
| LED du bandeau éteinte | l'authentification fonctionne quand même ; vérifier l'alim et le WiFi de l'ESP | « l'objet est un miroir, la chaîne ne dépend pas de lui » |
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
