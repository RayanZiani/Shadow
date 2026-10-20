# back-office — authentification visage + mot de passe, habilitations

Réponse à la **consigne additionnelle** (Workshop 2026-27 / M2) : renforcer
l'authentification du back-office par une reconnaissance faciale sur la caméra
USB du PC hôte, avec gestion des habilitations, et un bandeau LED ESP8266 qui
reflète chaque étape.

| | |
|---|---|
| Port | **8086** |
| Image | `python:3.12-slim` + OpenCV headless (YuNet + SFace) |
| Base | SQLite, volume Docker `bo-data` (survit au redémarrage) |
| Dépendances | FastAPI, OpenCV headless, NumPy — **pas de PyTorch, pas de GPU** |
| Tests | `python -m pytest test_backoffice.py -q` → **25 tests** |

---

## 1. Le parcours, exactement comme la consigne l'impose

```
        ┌──────────────┐   ┌──────────────────────┐   ┌──────────────┐
        │ 1. identifiant│──►│ 2. caméra USB (hôte) │──►│ 3. mot de passe│
        └──────────────┘   │    reconnaissance    │   └──────┬───────┘
                           └──────────┬───────────┘          │
                                      │                      │
                     visage inconnu   │        reconnu       │  valide
                    ┌─────────────────┘                      ▼
                    ▼                                 session + LED verte
        identifiant vidé, caméra éteinte                   fixe 30 s
        après 30 s de LED rouge fixe                            │
                    │                                           │
                    └──────────► 2 échecs → blocage 5 min ◄──────┘
                                       (compteur serveur)   mot de passe refusé
                                                            LED rouge ×10, retour 1
```

| Étape | Route | Effet LED | Effet en cas d'échec |
|---|---|---|---|
| identifiant | `POST /auth/identify/start` | — | 429 si le compte est bloqué |
| visage | `POST /auth/identify` | `green_blink` si reconnu, `red_solid` 30 s sinon | challenge invalidé, identifiant vidé, caméra éteinte |
| mot de passe | `POST /auth/password` | `green_solid` 30 s si valide, `red_blink` ×10 sinon | retour à l'étape 1 |

**Ce que le serveur ne croit pas.** Le navigateur ne dit jamais « le visage est
reconnu » : il envoie une image, le serveur calcule le descripteur et le compare
au visage enrôlé. Un client modifié ne peut pas sauter l'étape 2. De même, le
compteur d'échecs et le blocage de 5 minutes sont en base : recharger la page,
changer de navigateur ou vider `sessionStorage` ne les remet pas à zéro.

**Ce qui reste au niveau du navigateur.** L'ouverture de la caméra
(`getUserMedia`) et l'extinction du flux après capture. C'est une exigence
matérielle, pas une décision de sécurité : le serveur ne reçoit que des images
ponctuelles et n'en conserve aucune.

**Une image vide n'est pas une tentative d'usurpation.** Si aucun visage n'est
détecté (ou si plusieurs visages se disputent le cadre), la LED passe bien au
rouge fixe 30 s et l'identifiant est vidé — le comportement visible est celui de
la consigne — mais **l'essai n'est pas consommé** : seul un visage réellement
détecté puis non reconnu compte. Cela ne crée aucune faiblesse (sans visage
correspondant, on n'authentifie jamais) et évite qu'une lumière de
vidéoprojecteur ne bloque la démonstration. La réponse le dit explicitement :
`{"reason": "no_face", "counted": false}`.

---

## 2. Reconnaissance faciale

| Brique | Choix | Pourquoi |
|---|---|---|
| Détection | **YuNet** (232 Ko) | rapide sur CPU, 5 points de repère pour l'alignement |
| Descripteur | **SFace** (37 Mo, 128-d) | similarité cosinus documentée, pas d'entraînement à faire |
| Seuil | **0,40** (cosinus) | valeur par défaut d'OpenCV ; surchargeable par `BO_FACE_THRESHOLD` |
| Coût | ~11 ms (image vide), ~29 ms (visage détecté) sur un CPU portable | compatible avec un « IA légère » |
| Refus | 0 visage, plusieurs visages comparables, image illisible | message explicite, jamais un faux « reconnu » |

Les modèles sont **téléchargés et vérifiés par SHA-256 à la construction de
l'image**, jamais au runtime : aucun échec réseau possible pendant la
démonstration. Pour un poste sans Docker, ou pour construire **hors ligne** :

```bash
python services/backoffice/fetch_models.py
docker compose build backoffice   # les modèles locaux sont copiés, pas retéléchargés
```

Le `Dockerfile` accepte les deux cas : modèles présents dans `models/` → copiés
et vérifiés ; absents → téléchargés et vérifiés. Une empreinte qui ne correspond
pas fait **échouer la construction**, plutôt que de lancer une reconnaissance
faciale avec un modèle différent de celui validé.

**Ce qui n'est pas fait, et qui est publié dans `/healthz`** :
`"liveness": "not_implemented"`. Une photo imprimée tenue devant la caméra
passerait. C'est une limite assumée (anti-spoofing = modèle dédié + profondeur
ou mouvement) et elle est écrite, pas cachée. Réponse Q&A : « le facteur visage
protège contre l'usurpation d'identifiant, pas contre un attaquant qui
connaîtrait déjà le mot de passe et présenterait une photo ; le mot de passe
reste le second facteur ».

---

## 3. Habilitations (écran « Habilitations » du dashboard)

| Action | Route | Rôle requis |
|---|---|---|
| lister les comptes (verrou, visage, dernière connexion) | `GET /admin/users` | admin |
| créer un compte | `POST /admin/users` | admin |
| changer de rôle / activer-désactiver / changer le mot de passe | `PATCH /admin/users/{id}` | admin |
| désactiver (révoque les sessions) | `DELETE /admin/users/{id}` | admin |
| enrôler un visage depuis la caméra | `POST /admin/users/{id}/face` | admin |
| retirer un visage | `DELETE /admin/users/{id}/face` | admin |
| débloquer un compte | `POST /admin/users/{id}/unlock` | admin |
| journal d'audit | `GET /admin/audit` | admin |
| sessions ouvertes / révocation | `GET /admin/sessions`, `DELETE /admin/sessions/{id}` | admin |
| statistiques | `GET /admin/stats` | admin |

Trois rôles : **admin** (tout), **operator** (lecture de son profil), **auditor**
(lecture, aucune administration). Le dernier administrateur actif ne peut être ni
désactivé ni rétrogradé : sinon le back-office devient définitivement
inaccessible — panne vécue sur des systèmes réels, évitée ici par un 409.

Un compte **sans visage enrôlé ne peut pas se connecter**. C'est volontaire : le
visage n'est pas un facteur optionnel.

---

## 4. Sécurité des secrets

| Élément | Traitement |
|---|---|
| mots de passe | PBKDF2-HMAC-SHA256, 600 000 itérations, sel de 16 octets, comparaison à temps constant |
| sessions | 32 octets aléatoires, **seul le SHA-256 est stocké**, révocation immédiate |
| énumération de comptes | identifiant inconnu, compte inactif et visage absent renvoient la même réponse |
| jeton de bootstrap | `BO_BOOTSTRAP_TOKEN` dans `.env` (jamais versionné), à retirer après usage |
| jeton du bandeau LED | `LED_DEVICE_TOKEN`, comparé à temps constant |
| journal | aucun mot de passe, aucun descripteur facial : uniquement événement, identifiant, IP |
| en-têtes | `nosniff`, `X-Frame-Options: DENY`, `no-referrer`, `no-store` sur `/auth` et `/admin` |

---

## 5. Bandeau LED ESP8266

Le firmware est dans [`hardware/led_bandeau_esp8266/`](../../hardware/led_bandeau_esp8266/)
(CLion + PlatformIO). Contrat : l'ESP **interroge** `GET /led/state` toutes les
300 ms. Pourquoi ce sens : le PC hôte est le point d'accès, la carte est derrière
son NAT — aucun POST entrant à autoriser, aucune IP fixe à réserver.

```bash
curl -s "localhost:8086/led/state?device=recette" -H "X-LED-Token: led-demo-token"
# {"enabled":true,"seq":4,"state":"green_blink","seconds":0,"blinks":0,"ts":...}
```

---

## 6. Mise en route

```bash
cp .env.example .env
# 1. jeton d'enrôlement du premier administrateur
#    BO_BOOTSTRAP_TOKEN=<valeur aléatoire>   (dans .env)
docker compose up -d backoffice front-dashboard
```

Puis :

1. ouvrir **http://localhost:5173** (pas l'IP du réseau : `getUserMedia` exige
   un contexte sécurisé, `localhost` en est un, une IP LAN non) ;
2. la page de premier démarrage demande identifiant, mot de passe, jeton de
   bootstrap et un scan du visage → le compte est créé ;
3. retirer `BO_BOOTSTRAP_TOKEN` de `.env` puis `docker compose up -d backoffice`
   (le bootstrap se ferme, `/healthz` renvoie `bootstrap_required: false`) ;
4. se connecter : identifiant → scan → mot de passe.

Contrôle de recette :

```bash
python tools/backoffice_check.py
```

Il vérifie : service sain, base accessible, **moteur réel** (pas le stub), un
administrateur enrôlé, l'endpoint LED protégé, aucun compte bloqué, métriques
exposées.

---

## 7. Limites assumées

| Limite | Pourquoi | Où c'est dit |
|---|---|---|
| pas de détection de vivacité | modèle anti-spoofing hors périmètre | `/healthz` → `liveness: not_implemented` |
| un seul visage par compte | suffisant pour la démo, multi-modèles = chantier | ici |
| pas de MFA matérielle supplémentaire | le PIN/terminal reste la surface d'attaque du ML | `docs/GOUVERNANCE.md` |
| SQLite et non PostgreSQL | volume minuscule, démarrage sans dépendance | `db.py`, docstring |
| caméra non testée en HTTPS distant | la démo se fait sur le PC hôte, en `localhost` | ici |

Le moteur `stub` existe pour les tests et le développement sans caméra. Il est
**refusé en soutenance** : `/healthz` renvoie `demo_only: true`, l'interface
affiche un bandeau, et une alerte Prometheus se déclenche (`bo_face_engine_stub`).
