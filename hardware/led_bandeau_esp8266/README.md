# Bandeau LED ESP8266 — contrat et recette

Consigne additionnelle : « un objet connecté ESP8266 … équipé d'une LED
bicolore (rouge/vert) … connecté en WiFi au point d'accès de votre solution ».

| Élément | Choix |
|---|---|
| Carte | ESP8266 (NodeMCU / Wemos D1 mini) |
| IDE / toolchain | **JetBrains CLion + PlatformIO** (`platformio.ini`) |
| Framework | Arduino (bibliothèque WiFi uniquement) |
| Bibliothèque | ArduinoJson 6 (filtre : la trame utile fait < 200 octets) |
| Rôle | **miroir d'état** — il ne déclenche rien, ne signe rien |

## 1. Contrat d'état

Le back-office publie l'état ; **l'ESP8266 l'interroge** toutes les 300 ms
(`GET /led/state`). Pourquoi dans ce sens : le PC hôte est le point d'accès,
l'ESP est derrière son NAT. Un POST serveur → ESP demanderait une IP fixe, une
réservation DHCP et une règle de pare-feu entrante — trois causes de panne le
jour de la démo, pour 300 ms de latence en moins.

```
GET http://<PC hôte>:8086/led/state?device=resilience-led
X-LED-Token: <LED_DEVICE_TOKEN>

{"enabled":true,"seq":7,"state":"green_blink","seconds":0,"blinks":0,"ts":...}
```

| `state` | LED | Déclencheur | Temporisation |
|---|---|---|---|
| `off` | éteinte | démarrage, déconnexion, inactivité | — |
| `green_blink` | vert clignotant 2 Hz | visage reconnu, mot de passe attendu | jusqu'à la saisie |
| `green_solid` | vert fixe | mot de passe validé | **30 s** puis extinction |
| `red_blink` | rouge, **10 flashs** | mot de passe refusé | puis extinction |
| `red_solid` | rouge fixe | visage non reconnu | **30 s** puis extinction |

Deux garanties de sûreté :

1. **Le firmware est maître du temps.** Les 30 s et les 10 flashs sont comptés
   localement : si le back-office disparaît, la séquence en cours se termine
   correctement.
2. **Pas de vert mensonger.** Sans contact HTTP depuis 5 s, la LED s'éteint.
   Un bandeau vert alors que le service est tombé serait pire que pas de
   bandeau.

## 2. Câblage

```
        ESP8266 (NodeMCU / D1 mini)
        ┌───────────────────────────┐
   ─────┤ GND                   D1  ├──── 220 Ω ──── anode verte
        │                       D2  ├──── 220 Ω ──── anode rouge
   USB ─┤ 5 V                       │                (cathodes → GND)
        │                       D4  ├── LED bleue de la carte (témoin WiFi)
        └───────────────────────────┘
```

LED bicolore à deux broches : la plus longue est la cathode commune. Une
bicolore à trois broches a une cathode par couleur — les deux vont à GND.

## 3. Compilation et flashage (CLion + PlatformIO)

```bash
# 1. secrets : SSID du point d'accès + IP de la passerelle
cp src/secrets.example.h src/secrets.h   # puis éditer

# 2. dans CLion (plugin PlatformIO) : Build, puis Upload
#    ou en ligne de commande, si PlatformIO Core est installé :
pio run -e esp8266            # compilation
pio run -e esp8266 -t upload  # flashage
pio device monitor -b 115200  # journal série
```

**Compilation vérifiée** (PlatformIO Core, `espressif8266@4.2.1`,
ArduinoJson 6) : `SUCCESS`, **35,4 % de RAM** (29 028 / 81 920 octets) et
**28,0 % de flash** (292 523 / 1 044 464 octets). Sans `secrets.h`, le firmware
compile quand même et se connecte à un SSID factice : le compilateur émet un
`#warning` explicite, jamais un échec silencieux.

Journal attendu au démarrage :

```
[boot] bandeau LED RESILIENCE-ALPHA
[wifi] connexion a RESILIENCE-G1
[wifi] connecte, ip=192.168.137.42
```

## 4. Recette (5 tests, sans back-office puis avec)

| # | Test | Attendu |
|---|---|---|
| 1 | alimentation seule | LED **éteinte**, témoin WiFi clignotant lentement |
| 2 | WiFi + back-office démarrés | témoin fixe, LED d'état éteinte, `GET /led/state` vu dans les métriques (`bo_led_polls_total` monte) |
| 3 | visage reconnu dans le navigateur | **vert clignotant** en moins de 400 ms |
| 4 | mot de passe validé | **vert fixe 30 s** puis extinction automatique |
| 5 | mot de passe refusé / visage inconnu | **rouge 10 flashs** / **rouge fixe 30 s** |

Test de résilience : couper le back-office pendant un vert fixe → la LED
termine ses 30 s puis s'éteint (elle ne reste pas verte indéfiniment).

## 5. Réponse Q&A — « et si le bandeau tombe ? »

| Panne | Comportement | Ce qu'on répond |
|---|---|---|
| ESP débranché | aucune erreur côté back-office | « c'est un miroir, l'authentification ne dépend pas de lui » |
| WiFi perdu | LED éteinte, reconnexion toutes les 5 s | « il ne ment jamais sur l'état » |
| Back-office arrêté | LED éteinte au bout de 5 s | « pas de feu vert sans service » |
| Trame inconnue | état `off` | « le contrat est fermé : un état inconnu n'allume rien » |
