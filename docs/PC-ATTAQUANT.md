# PC Attaquant — préparer le second poste

> Rôle : lancer les attaques depuis un **poste distinct** de celui qui héberge la
> pile. C'est une exigence de la grille (« attaque lancée depuis le PC
> Attaquant »), et c'est aussi ce qui rend la démonstration crédible : le
> présentateur ne tape aucune commande d'attaque sur son propre clavier.
>
> Ce document ne couvre que le second poste. Le PC Hôte (Docker + IA) est décrit
> dans [`INSTALLATION-LOCAL.md`](INSTALLATION-LOCAL.md), le déroulé dans
> [`DEMO.md`](DEMO.md).

---

## 1. Ce que le PC Attaquant doit pouvoir joindre

Trois ports du PC Hôte, en TCP, sur le réseau local :

| Port | Service | Pourquoi |
|---|---|---|
| **8085** | `waf` | point d'entrée « client » : c'est par là que passent `poison`, `replay`, `burst`, `malformed` |
| **8087** | `shop-api` | attaques de la boutique (`attack.py shop`) |
| 8080 | `payment-api` | cible directe de `malformed` (le noyau doit répondre 400, pas paniquer) |

Rien d'autre n'est nécessaire : ni Docker, ni le dépôt complet, ni un secret.
Le jeton de service **n'est pas** sur le PC Attaquant — c'est volontaire.
`attack.py reset` (désarmer la déception) se lance depuis le PC Hôte.

### Trouver l'adresse du PC Hôte

Sur le **PC Hôte** :

```bash
# macOS
ipconfig getifaddr en0          # Wi-Fi   → ex. 10.251.4.134
ipconfig getifaddr en1          # Ethernet

# Windows (PowerShell)
Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.IPAddress -notlike '127.*' } |
  Select-Object InterfaceAlias, IPAddress

# Linux
hostname -I
```

Vérifier depuis le **PC Attaquant** que le WAF répond :

```bash
curl -s http://<IP-PC-HOTE>:8085/healthz
# {"status":"ok","service":"waf","deception":false,...}
```

Si le `curl` échoue alors que le PC Hôte répond en local :

1. les deux postes sont-ils sur le **même réseau** (pas un réseau invité) ?
2. pare-feu du PC Hôte : macOS → *Réglages → Réseau → Pare-feu* (désactivé sur
   le poste de démonstration) ; Windows → autoriser Docker Desktop sur le profil
   **privé** ;
3. `docker compose ps` sur le PC Hôte : les ports sont publiés en `0.0.0.0`,
   donc joignables — un `127.0.0.1` dans le compose serait la cause.

---

## 2. Ce qu'il faut installer sur le PC Attaquant

Un interpréteur Python 3.10+ et **une seule dépendance** :

```bash
python3 -m venv .venv-attaque
# macOS / Linux
.venv-attaque/bin/pip install httpx
# Windows
.venv-attaque\Scripts\pip install httpx
```

Puis copier **deux dossiers** du dépôt (clé USB, `scp`, archive) :

```
Shadow/tools/attack.py
Shadow/tools/attack_shop.py
Shadow/tools/service_token.py     # importé par attack.py (jeton absent = vide)
```

Le reste du dépôt est inutile : les attaques sont des requêtes HTTP, elles ne
lisent ni le code des services ni `.env` (sauf si on y met un jeton, ce qu'on ne
fait pas sur ce poste).

> **Ne jamais copier `.env` sur le PC Attaquant.** Le jeton de service y
> donnerait le droit de désarmer la déception ou de mettre des outputs en
> quarantaine : ce n'est plus un attaquant, c'est un administrateur.

---

## 3. Lancer les attaques

Trois façons équivalentes de désigner le PC Hôte :

```bash
# 1. variable d'environnement (couvre WAF, paiement et boutique)
SHADOW_HOST=10.251.4.134 python tools/attack.py poison

# 2. option explicite (ne déplace que le WAF, le reste suit le même hôte)
python tools/attack.py poison --target http://10.251.4.134:8085

# 3. tout à la main, si les ports diffèrent
SHADOW_WAF=http://10.251.4.134:8085 \
SHADOW_PAYMENT=http://10.251.4.134:8080 \
SHADOW_SHOP=http://10.251.4.134:8087 \
python tools/attack.py all
```

Sous Windows PowerShell, la variable s'écrit autrement :

```powershell
$env:SHADOW_HOST = "10.251.4.134"
python tools\attack.py poison
```

### Les commandes de la démonstration

| Commande | Quand | Attendu |
|---|---|---|
| `attack.py poison` | **38 s** du storyboard | 12 transactions acceptées, score ML qui monte, badge SOUS ATTAQUE |
| `attack.py poison` (2ᵉ fois) | **57 s** | requêtes **déroutées** vers le honeypot, `deception: true` |
| `attack.py replay` | Q&A | `200` puis `409` : la key image est consommée une fois |
| `attack.py burst` | Q&A | rafale à ~9 req/s, détectée par la cadence |
| `attack.py malformed` | Q&A | `400` propre sur les trois cas, service vivant |
| `attack.py shop` | avant/après le clip | 6 familles refusées, aucune commande frauduleuse, stock jamais négatif |
| `attack.py list` | répétition | rappel des commandes, aucune requête émise |

Codes de sortie : `0` si l'attaque s'est déroulée comme prévu, `1` sinon — le
chronométreur peut donc se fier au code, pas à la couleur du terminal.

---

## 4. Ce que le PC Attaquant ne fait pas

| Geste | Pourquoi c'est exclu |
|---|---|
| `attack.py reset` | exige `SERVICE_TOKEN` ; se lance **sur le PC Hôte** entre deux répétitions |
| redémarrer un conteneur | pas de Docker sur ce poste, et aucune intervention humaine n'est permise pendant la remédiation |
| `attack.py shop` juste avant le clip | l'attaque de survente **vide réellement** un article : relancer `python tools/restock.py` sur le PC Hôte ensuite |
| ouvrir `localhost:5173` | le dashboard est sur le PC Hôte ; le vidéoprojecteur n'affiche qu'une machine |

---

## 5. Répétition à blanc (5 minutes, la veille)

Sur le PC Hôte, la pile tourne (`docker compose ps` : 19 services). Puis, dans
cet ordre :

```bash
# 1. PC Hôte — la cible est prête et la déception désarmée
python tools/preflight.py
python tools/attack.py reset
python tools/restock.py --check

# 2. PC Attaquant — la cible est joignable
curl -s http://<IP-PC-HOTE>:8085/healthz
python tools/attack.py list

# 3. PC Attaquant — l'attaque principale, une fois
python tools/attack.py poison --target http://<IP-PC-HOTE>:8085

# 4. PC Hôte — la chaîne a réagi, puis on remet à zéro
curl -s localhost:8084/state
python tools/attack.py reset
python tools/restock.py
```

Critère de fin : l'étape 3 affiche `12 acceptées · 0 rejetées`, et l'étape 4
montre un état `RECOVERED_TX` (ou `NORMAL_TX` si la remédiation est déjà finie).

---

## 6. Pendant la démonstration

- Le PC Attaquant reste **connecté** et son terminal est déjà ouvert dans
  `tools/`, avec la variable `SHADOW_HOST` exportée : aucune saisie d'adresse
  devant le jury.
- Le terminal du PC Attaquant peut être projeté : il ne contient aucun secret.
- L'attaquant **annonce** ce qu'il lance (« j'empoisonne les leurres »), puis se
  tait : entre 38 s et 52 s, personne ne touche à aucun clavier.
- Si la première attaque ne porte pas (score qui ne monte pas), on la relance —
  c'est prévu dans `DEMO.md` §6.
