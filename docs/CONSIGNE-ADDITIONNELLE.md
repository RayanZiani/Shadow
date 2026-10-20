# Consigne additionnelle — ce qui a été livré, et comment le prouver

> Réponse point par point à `WorkShop 2026-27 M2 - Consigne Additionnelle.pdf`
> (renforcement de l'authentification du back-office).
> Chaque exigence renvoie à un fichier, une route ou une commande de recette.
>
> **État** : 16 exigences sur 16 couvertes en logiciel · `services/backoffice`
> (port 8086, **25 tests verts**) · firmware ESP8266 **compilé** (35,4 % RAM,
> 28,0 % flash), **reste à flasher sur la carte** · service dédié, 12ᵉ cible
> Prometheus, 5 alertes dédiées.

---

## 1. La consigne, découpée en exigences vérifiables

| # | Exigence de la consigne | Livré | Preuve |
|---|---|---|---|
| E1 | page d'authentification identifiant / mot de passe déjà présente | page unique du back-office, en français, charte respectée | `front-dashboard/src/auth/LoginPage.jsx` |
| E2 | **activation d'une caméra USB juste après la saisie de l'identifiant** | la caméra s'ouvre à l'étape 2, pas avant | `CameraView.jsx`, `camera.js` |
| E3 | la caméra **confirme l'identificateur** | descripteur SFace comparé au visage enrôlé du compte | `faces.py`, `POST /auth/identify` |
| E4 | si reconnu → **saisie du mot de passe** | challenge `face_ok=1`, mot de passe exigé ensuite | `main.py` (`challenge_or_fail(require_face=True)`) |
| E5 | si non reconnu → **identifiant remis à vide, caméra éteinte** | challenge invalidé, `next: retry`, flux caméra stoppé | test `test_visage_inconnu_vide_lidentifiant_et_eteint_la_camera` |
| E6 | **1 nouvel essai puis blocage 5 minutes** | 2 échecs → 429 + `Retry-After` ; une image *sans visage* ne consomme pas l'essai (cadrage, pas usurpation) | tests `test_deux_echecs_puis_blocage_cinq_minutes`, `test_absence_de_visage_refusee` |
| E7 | caméra **obligatoirement sur le PC hôte** | `getUserMedia` dans le navigateur du PC hôte, seul à avoir la webcam | `docs/INSTALLATION-LOCAL.md` §8 |
| E8 | **IA légère de reconnaissance de visage intégrée au back-office** | YuNet (232 Ko) + SFace (37 Mo), CPU, ~30 ms | `faces.py`, `/healthz` → `face_engine: ready` |
| E9 | **gestion complète des habilitations** | créer, rôle, activer/désactiver, visage, débloquer, sessions, audit | `AdminPanel.jsx`, `/admin/*` |
| E10 | objet **ESP8266** (CLion + PlatformIO) avec **LED bicolore** | firmware PlatformIO, LED vert/rouge | `hardware/led_bandeau_esp8266/` |
| E11 | ESP **connecté en WiFi au point d'accès** de la solution | `WIFI_SSID = RESILIENCE-G<n>` dans `secrets.h` | `hardware/led_bandeau_esp8266/README.md` |
| E12 | LED **éteinte par défaut** | `off` au boot et après chaque séquence | firmware `setup()`, `/led/state` |
| E13 | identifiant saisi + personne reconnue → **vert clignotant** jusqu'au mot de passe | `green_blink` | test `test_la_led_passe_au_vert_clignotant_puis_fixe` |
| E14 | mot de passe validé → **vert fixe 30 s** puis extinction, page accessible | `green_solid`, `seconds: 30` | idem + firmware `serviceState()` |
| E15 | mot de passe refusé → **rouge 10 flashs** puis extinction, retour à l'étape 1 | `red_blink`, `blinks: 10` | test `test_mot_de_passe_invalide_deux_fois_bloque` |
| E16 | visage non reconnu → **rouge fixe 30 s** avant extinction de la caméra | `red_solid`, `seconds: 30` | test `test_visage_inconnu_...` |

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
| **l'ESP interroge** le back-office | le back-office pousse vers l'ESP | le PC hôte est le point d'accès : aucun NAT entrant, aucune IP fixe, aucune règle de pare-feu |
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

```bash
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

# 4. bandeau LED
curl -s "localhost:8086/led/state?device=recette" -H "X-LED-Token: led-demo-token"

# 5. habilitations
#    bouton « Habilitations » dans le bandeau du dashboard :
#    créer un auditeur, lui enrôler un visage, vérifier qu'il ne peut pas administrer
```

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
