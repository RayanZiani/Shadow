# Consigne additionnelle — ce qui a été livré, et comment le prouver

> Réponse point par point à `WorkShop 2026-27 M2 - Consigne Additionnelle.pdf`
> (renforcement de l'authentification du back-office).
> Chaque exigence renvoie à un fichier, une route ou une commande de recette.
>
> **État** : **17 exigences recensées** · **16 couvertes en logiciel** · **13
> prouvées par test** · **3 preuves restent à faire sur la carte** (E10, E11,
> E13–E16 côté rendu matériel) · **E17** est une exigence d'exploitation,
> prouvée par la recette réseau (§1 bis). `services/backoffice` (port 8086,
> **29 tests verts**) · firmware ESP8266 **compilé** (35,4 % RAM, 28,0 % flash),
> **reste à flasher sur la carte** · service dédié, 12ᵉ cible Prometheus,
> 5 alertes dédiées.

---

## 1. La consigne, découpée en exigences vérifiables

La colonne **« reste à prouver sur la carte »** est distincte de la preuve
logicielle : elle ne peut être constatée que sur la carte réelle (flash,
association WiFi, rendu de la LED). Une exigence peut avoir les deux.

| # | Exigence de la consigne | Livré | Preuve logicielle (test / recette) | Reste à prouver sur la carte |
|---|---|---|---|---|
| E1 | page d'authentification identifiant / mot de passe déjà présente | page unique du back-office, en français, charte respectée | `front-dashboard/src/auth/LoginPage.jsx` ; recette navigateur §5 | — |
| E2 | **activation d'une caméra USB juste après la saisie de l'identifiant** | la caméra s'ouvre à l'étape 2, pas avant | `CameraView.jsx`, `camera.js` ; recette §5 (la demande de caméra n'apparaît qu'après l'identifiant) | — |
| E3 | la caméra **confirme l'identificateur** | descripteur SFace comparé au visage enrôlé du compte | `faces.py`, `POST /auth/identify` ; test `test_visage_reconnu_puis_mot_de_passe_ouvre_la_session` | — |
| E4 | si reconnu → **saisie du mot de passe** | challenge `face_ok=1`, mot de passe exigé ensuite | `main.py` (`challenge_or_fail(require_face=True)`) ; test `test_le_mot_de_passe_nest_pas_accepte_sans_visage` | — |
| E5 | si non reconnu → **identifiant remis à vide, caméra éteinte** | challenge invalidé, `next: retry`, flux caméra stoppé | test `test_visage_inconnu_vide_lidentifiant_et_eteint_la_camera` | — |
| E6 | **1 nouvel essai puis blocage 5 minutes** | 2 échecs → 429 + `Retry-After` ; une image *sans visage* ne consomme pas l'essai (cadrage, pas usurpation) | tests `test_deux_echecs_puis_blocage_cinq_minutes`, `test_absence_de_visage_refusee` | — |
| E7 | caméra **obligatoirement sur le PC hôte** | `getUserMedia` dans le navigateur du PC hôte, seul à avoir la webcam | `docs/INSTALLATION-LOCAL.md` §8 ; recette §5 | — |
| E8 | **IA légère de reconnaissance de visage intégrée au back-office** | YuNet (232 Ko) + SFace (37 Mo), CPU, ~30 ms | `faces.py`, `/healthz` → `face_engine: ready` ; test `test_le_moteur_reel_charge_et_refuse_une_image_sans_visage` | — |
| E9 | **gestion complète des habilitations** | créer, rôle, activer/désactiver, visage, débloquer, sessions, audit | `AdminPanel.jsx`, `/admin/*` ; tests `test_creation_utilisateur_et_role_applique`, `test_le_dernier_admin_ne_peut_pas_etre_desactive` | — |
| E10 | objet **ESP8266** (CLion + PlatformIO) avec **LED bicolore** | firmware PlatformIO, LED vert/rouge | `hardware/led_bandeau_esp8266/` ; compilation PlatformIO (35,4 % RAM, 28,0 % flash) | **flash + boot** : LED éteinte (couvre E12), témoin WiFi |
| E11 | ESP **connecté en WiFi au point d'accès** de la solution | `WIFI_SSID = RESILIENCE-G<n>` dans `secrets.h` | `hardware/led_bandeau_esp8266/README.md` ; `secrets.h` généré depuis `secrets.example.h` | **association au hotspot** : IP `192.168.137.x`, journal `pio device monitor` |
| E12 | LED **éteinte par défaut** | `off` au boot et après chaque séquence | firmware `setup()`, `/led/state` ; test `test_un_etat_expire_retombe_a_off` | — (couvert par le boot de E10) |
| E13 | identifiant saisi + personne reconnue → **vert clignotant** jusqu'au mot de passe | `green_blink` | test `test_la_led_passe_au_vert_clignotant_puis_fixe` | **vert clignotant** observé en moins de 400 ms |
| E14 | mot de passe validé → **vert fixe 30 s** puis extinction, page accessible | `green_solid`, `seconds: 30` | idem + firmware `serviceState()` | **vert fixe 30 s** puis extinction automatique |
| E15 | mot de passe refusé → **rouge 10 flashs** puis extinction, retour à l'étape 1 | `red_blink`, `blinks: 10` | tests `test_mot_de_passe_invalide_deux_fois_bloque`, `test_le_rouge_clignotant_a_une_duree_bornee` | **rouge 10 flashs** puis extinction |
| E16 | visage non reconnu → **rouge fixe 30 s** avant extinction de la caméra | `red_solid`, `seconds: 30` | test `test_visage_inconnu_vide_lidentifiant_et_eteint_la_camera` | **rouge fixe 30 s** puis extinction |
| E17 | **le PC hôte fournit le point d'accès `RESILIENCE-G<n>`** et héberge Docker/PostgreSQL | hotspot Windows, `WIFI_SSID`, `BO_HOST` = passerelle, Docker sur l'hôte | procédure §1 bis ; `preflight.py` ; `ipconfig` / `Get-NetIPConfiguration` | **association WiFi de l'ESP** et IP obtenue (même preuve que E11) |

**Comment lire les chiffres** : E1–E16 sont couvertes en logiciel ; 13 ont une
preuve automatisée ou une recette outillée. Les trois preuves matérielles
restantes portent sur six exigences — E10 (flash), E11 (WiFi) et E13–E16
(rendu physique de la LED) — et **s'ajoutent** à leur preuve logicielle : elles
ne remplacent pas les 13 tests. E17 est une exigence d'exploitation : elle se
prouve sur le réseau, pas dans un test unitaire.

---

## 1 bis. E17 — le PC hôte est le point d'accès

La consigne PDF impose que l'ESP soit « connecté en WiFi au point d'accès de
votre solution ». Ce point d'accès est le **PC hôte** : c'est aussi lui qui
héberge Docker, PostgreSQL et le back-office. L'ESP est derrière son NAT ; il
**interroge** le back-office toutes les 300 ms (`GET /led/state`), il ne reçoit
aucune connexion entrante. C'est ce qui évite une IP fixe, une réservation DHCP
et une redirection NAT ; il reste à laisser passer le port 8086 sur le profil
réseau privé (§1 bis, étape 5).

```
   PC hôte Windows (point d'accès RESILIENCE-G<n>, passerelle 192.168.137.1)
   ┌────────────────────────────────────────────────────────────────────┐
   │  Docker : backoffice:8086, PostgreSQL, payment-api, shop-api…      │
   │        ▲                                                           │
   │        │ GET /led/state (300 ms, WiFi)                             │
   │  ESP8266 + LED bicolore (192.168.137.x)                            │
   └────────────────────────────────────────────────────────────────────┘
```

### Procédure Windows (partage de connexion)

1. **Connexion Internet du PC hôte.** Ethernet de préférence. Si le PC est en
   Wi-Fi, vérifier que la carte supporte le mode point d'accès ; sinon utiliser
   une clé Wi-Fi USB dédiée au hotspot.
2. **Paramètres → Réseau et Internet → Partage de connexion mobile.**
   - « Partager ma connexion Internet à partir de » : **Ethernet** (ou la carte
     qui a Internet).
   - « Partager via » : **Wi-Fi**.
   - **Modifier** : nom `RESILIENCE-G<n>` (par exemple `RESILIENCE-G1`),
     mot de passe WPA2 de 8 caractères minimum, **bande 2,4 GHz** (l'ESP8266 ne
     voit pas le 5 GHz).
3. **Activer** le partage. Windows crée un réseau `192.168.137.0/24` ; la
   passerelle du PC hôte est en général **`192.168.137.1`**. La vérifier :

```powershell
ipconfig | Select-String -Pattern "Passerelle|Gateway"
Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } |
  Select-Object InterfaceAlias, IPv4Address, @{n='Gateway';e={$_.IPv4DefaultGateway.NextHop}}
```

4. **Renseigner `secrets.h`** (copié depuis `secrets.example.h`, jamais versionné) :

```cpp
#define WIFI_SSID "RESILIENCE-G1"
#define WIFI_PASS "<mot de passe du hotspot>"
#define BO_HOST   "192.168.137.1"   // passerelle du PC hôte
#define BO_PORT   8086
#define LED_TOKEN "<valeur de LED_DEVICE_TOKEN dans .env>"
```

5. **Autoriser le port 8086** sur le profil réseau privé, sinon l'ESP reçoit un
   timeout alors que le back-office est vivant. PowerShell **administrateur** :

```powershell
New-NetFirewallRule -DisplayName "Shadow backoffice LED" `
  -Direction Inbound -Protocol TCP -LocalPort 8086 -Action Allow -Profile Private
```

   Le réseau du hotspot doit être en profil **Privé** (`Get-NetConnectionProfile`).
   S'il est en `Public`, basculer le profil ou autoriser aussi `Public` le temps
   de la démonstration — jamais en production.

6. **Vérifier que l'ESP obtient une IP** :

```powershell
pio device monitor -b 115200
# attendu :
# [wifi] connexion a RESILIENCE-G1
# [wifi] connecte, ip=192.168.137.42
arp -a | Select-String "192.168.137."
```

7. **Vérifier le service depuis le réseau du hotspot** :

```powershell
curl.exe -s http://192.168.137.1:8086/healthz
$led = ((Select-String -Path .env -Pattern '^LED_DEVICE_TOKEN=').Line -split '=',2)[1]
curl.exe -s "http://192.168.137.1:8086/led/state?device=recette" -H "X-LED-Token: $led"
Remove-Variable led
python tools/preflight.py
```

`preflight.py` doit être en code 0 ; le bandeau doit passer au vert clignotant
lors d'un scan de visage, et s'éteindre après 30 s.

### Ce qui fait échouer le point d'accès

| Symptôme | Cause probable | Geste |
|---|---|---|
| « Le partage de connexion mobile n'est pas disponible » | carte Wi-Fi sans mode AP, ou 5 GHz seulement | clé Wi-Fi USB 2,4 GHz, ou routeur de poche — le point d'accès doit rester sur le PC hôte |
| L'ESP ne se connecte pas | SSID/mot de passe, ou bande 5 GHz | vérifier `secrets.h`, forcer 2,4 GHz, regarder `pio device monitor` |
| L'ESP a une IP mais la LED reste éteinte | `LED_DEVICE_TOKEN` différent, ou port 8086 bloqué | comparer avec `.env`, vérifier la règle de pare-feu, `curl` depuis un autre poste du hotspot |
| Le hotspot s'éteint seul | économie d'énergie « lorsque aucun appareil n'est connecté » | désactiver cette option, PC sur secteur |
| Le PC change d'IP de passerelle | autre adaptateur, réseau public | refaire `ipconfig`, remettre `BO_HOST` dans `secrets.h`, reflasher |

`docs/INSTALLATION-LOCAL.md` §8 documente le rôle du PC hôte pour la caméra et
le navigateur ; la procédure hotspot ci-dessus est la partie qui manquait pour
E17. Voir aussi `hardware/led_bandeau_esp8266/README.md` §3–4 pour le flash et la
recette en cinq tests.

---

## 2. Le parcours, en une image

```
 navigateur du PC hôte (localhost:5173)
 ┌──────────────────────────────────────────────────────────────┐
 │ 1. identifiant ──► POST /auth/identify/start                 │
 │ 2. caméra USB ───► getUserMedia → JPEG ──► POST /auth/identify│
 │      ├─ reconnu   → LED verte clignotante                     │
 │      └─ refusé    → LED rouge fixe 30 s, identifiant vidé,    │
 │                     caméra éteinte, 1 essai restant           │
 │ 3. mot de passe ─► POST /auth/password                        │
 │      ├─ valide    → session + LED verte fixe 30 s             │
 │      └─ refusé    → LED rouge ×10, retour à l'étape 1         │
 │ 2 échecs cumulés  → 429, blocage 5 min tenu par le serveur    │
 └──────────────────────────────────────────────────────────────┘
                               │
                     GET /led/state (300 ms)
                               ▼
                     ESP8266 + LED bicolore
```

Le bandeau LED **n'est pas** dans le chemin d'authentification : s'il est
débranché, le parcours aboutit quand même. C'est la même règle que pour l'objet
physique existant (`docs/hardware/CONCEPT-PHYSIQUE.md` §6).

---

## 3. Décisions techniques et ce qu'elles coûtent

| Décision | Alternative écartée | Raison |
|---|---|---|
| **YuNet + SFace** (OpenCV) | dlib/face_recognition, InsightFace, DeepFace | 38 Mo, CPU, pas de compilation, pas de PyTorch : l'image reste légère et le build reproductible |
| **SQLite** pour le back-office | PostgreSQL (déjà dans la stack) | quelques dizaines de lignes, aucune dépendance de démarrage ; le registre des outputs est, lui, persisté dans PostgreSQL (`registry.py`) |
| **sessions opaques** | JWT | révocation immédiate, aucun secret à distribuer au navigateur |
| **PBKDF2** | Argon2id, bcrypt | aucune compilation native dans l'image ; 600 000 itérations conformes aux recommandations |
| **l'ESP interroge** le back-office | le back-office pousse vers l'ESP | le PC hôte est le point d'accès : aucun NAT entrant, aucune IP fixe ; une seule règle de pare-feu pour le port 8086 |
| **caméra côté navigateur** | capture dans le conteneur | Docker Desktop/WSL2 n'a pas d'accès webcam fiable ; le navigateur du PC hôte, si |

Coût assumé de ce dernier choix : la page doit être ouverte en `localhost` (ou
en HTTPS). Une IP LAN n'est pas un « contexte sécurisé » et le navigateur
refuserait la caméra — message d'erreur explicite dans ce cas.

---

## 4. Ce qui n'est pas fait

| Point | État | Ce qu'on répond |
|---|---|---|
| **détection de vivacité** (anti-photo) | non implémentée, publiée `liveness: not_implemented` | « le visage est un facteur d'identité, pas un anti-spoofing ; le mot de passe reste le second facteur » |
| **HTTPS** sur le back-office | non terminé (démo en `localhost`) | « en production, TLS + HSTS ; ici le trafic ne quitte pas le PC hôte » |
| **multi-visages par compte** | un seul descripteur | « suffisant pour un back-office de démonstration » |
| **firmware non flashé** | à flasher sur la carte réelle | `hardware/led_bandeau_esp8266/README.md` §4 (recette en 5 tests) |
| **gestion des tiers / revue d'accès périodique** | hors périmètre technique | `docs/GOUVERNANCE.md` |

Ces limites sont dans `/healthz` (`liveness`) ou dans ce document. Une limite
écrite ne coûte pas de point ; une limite découverte en Q&A en coûte.

---

## 5. Recette de la consigne (5 minutes, avant la soutenance)

```powershell
# 1. la pile tourne
docker compose up -d backoffice front-dashboard
docker compose ps backoffice            # Up (healthy)

# 2. contrôle automatique
python tools/backoffice_check.py        # code de sortie 0 = prêt

# 3. parcours manuel dans le navigateur du PC hôte
#    http://localhost:5173
#    - identifiant au hasard          → "visage non reconnu", LED rouge fixe 30 s
#    - votre identifiant + votre visage → vert clignotant, puis mot de passe
#    - mauvais mot de passe           → LED rouge ×10, retour à l'identifiant
#    - 2e échec                       → blocage 5 min, message avec décompte

# 4. bandeau LED — le jeton est lu dans .env, jamais affiché
$led = ((Select-String -Path .env -Pattern '^LED_DEVICE_TOKEN=').Line -split '=',2)[1]
curl.exe -s "http://localhost:8086/led/state?device=recette" -H "X-LED-Token: $led"
Remove-Variable led

# 5. habilitations
#    bouton « Habilitations » dans le bandeau du dashboard :
#    créer un auditeur, lui enrôler un visage, vérifier qu'il ne peut pas administrer
```

Sous macOS/Linux, l'étape 4 s'écrit :

```bash
LED_TOKEN=$(grep '^LED_DEVICE_TOKEN=' .env | cut -d= -f2-)
curl -s "localhost:8086/led/state?device=recette" -H "X-LED-Token: $LED_TOKEN"
unset LED_TOKEN
```

L'ancien jeton de démonstration n'est **plus** une valeur de recette : le jeton
est généré par `tools/init_env.py` et peut être différent à chaque poste. Si la
réponse est `401`, le jeton lu n'est pas celui du conteneur :
`docker compose up -d --force-recreate backoffice` après avoir vérifié `.env`.

| # | Test | Attendu |
|---|---|---|
| 1 | `python tools/backoffice_check.py` | 0 échec |
| 2 | identifiant inconnu + scan | refus, LED rouge fixe 30 s, caméra éteinte |
| 3 | visage connu | vert clignotant, champ mot de passe affiché |
| 4 | mot de passe valide | vert fixe 30 s, dashboard accessible |
| 5 | mot de passe invalide ×2 | 10 flashs rouges puis blocage 5 min |
| 6 | déblocage par un administrateur | compte de nouveau utilisable |
| 7 | bandeau débranché | le parcours aboutit quand même |
| 8 | `docker compose restart backoffice` | comptes, visages et blocages conservés |

---

## 6. Ce que la consigne ne demandait pas, mais qui est là

| Ajout | Pourquoi |
|---|---|
| **PIN du terminal généré** (`TERMINAL_PIN`) | une valeur publique dans le code est une faille ; `tools/init_env.py` le tire au hasard, `preflight.py` vérifie qu'il arrive au conteneur |
| **verrouillage du PIN** après 5 refus (60 s) | un PIN à 4 chiffres tombe en quelques minutes sans limitation |
| **journal d'audit** du back-office | trace des créations, rôles, déblocages et sessions |
| **métriques Prometheus** dédiées | 5 alertes (comptes bloqués, moteur facial indisponible, etc.) |
| **pont hardware en lecture seule** | le bandeau ne peut pas déclencher une remédiation |

---

## 7. Les consignes du 20/10

Le jury du 20/10 a ajouté six exigences **N1–N6** : site vitrine connecté à une
base, front qui présente les articles, back d'administration, paiement réel avec
stockage et historique, SI sans faille volontaire, et IA (RAG + alertes SIEM +
remédiation autonome).

La réponse exigence par exigence — livré, test, preuve sur la pile Docker, ce
qui reste ouvert — est dans **[`docs/REGLES-JURY-20-10.md`](REGLES-JURY-20-10.md)**.
Les trois séquences à montrer en direct ce jour-là (assistant RAG, Loki/Grafana
Explore, alerte SIEM réelle) sont dans **[`docs/DEMO.md`](DEMO.md)** §2 ter.
