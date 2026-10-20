# Aspect physique — montage et intégration

Document opérationnel. Le raisonnement et le rôle de l'objet sont dans
[`docs/hardware/CONCEPT-PHYSIQUE.md`](../../docs/hardware/CONCEPT-PHYSIQUE.md).

## 1. Ce que tu as déjà, et comment l'utiliser

| Élément | Usage retenu |
|---|---|
| Terminal de paiement à clavier | **décor + saisie réelle** (voir §2) |
| ESP32 | pilotage des LEDs, rien d'autre |
| LEDs (bandeau / anneau WS2812) | miroir d'état, visible à distance |
| Imprimante 3D (volume inconnu) | 4 pièces, toutes < 180 mm |

### Ce que l'ESP32 ne fait PAS

Il ne lit pas le clavier du terminal et ne parle pas à `remediation-engine`.
Il reçoit une ligne série et allume des LEDs. C'est volontaire : moins il en
fait, moins il peut casser la démo.

## 2. Le clavier : deux options, une recommandée

### Option A (recommandée) — le terminal est un décor, la saisie passe par un pavé USB

Un pavé numérique USB à 10 € branché sur le PC hôte, et une petite page
`pinpad` (ou un simple `<input>` dans le dashboard) qui envoie les codes à
`payment-api`. Tu gardes :

- un vrai geste humain filmable (le présentateur tape sur le pavé posé sur le
  terminal) ;
- les features ML réelles (`pin_failure_ratio`, `inter_arrival_jitter`) ;
- **zéro démontage** du terminal, zéro risque de le casser la veille.

### Option B — lecture de la matrice du clavier par l'ESP32

Ouvrir le terminal, identifier les lignes/colonnes du clavier, les câbler sur
des GPIO. Ça marche, mais :

- le terminal peut être collé/vissé et ne pas se remonter ;
- les niveaux logiques ne sont pas garantis en 3,3 V ;
- c'est 3 à 5 h de travail pour un gain de démo nul.

**À ne faire que si le terminal est déjà démonté et que tu as du temps mort.**

## 3. Câblage

```
                    ESP32 Dev Module
                 ┌──────────────────┐
   WS2812 DIN ───┤ GPIO 5           │
   WS2812 5V  ───┤ (alim externe)   │
   WS2812 GND ───┤ GND ──── GND ────┤ commun obligatoire
                 │                  │
   (option) SDA ─┤ GPIO 21          │  OLED SSD1306
   (option) SCL ─┤ GPIO 22          │
                 │                  │
   (option) PWM ─┤ GPIO 18          │  servo trappe MG90S
                 │                  │
   USB ──────────┤                  │──► PC hôte (pont série)
                 └──────────────────┘
```

Trois règles qui évitent 90 % des problèmes :

1. **Alim 5 V séparée** (USB-C 5 V 3 A). 24 LEDs à blanc = ~1,4 A : l'USB du PC
   ne suit pas, et une chute de tension fait clignoter l'ESP32.
2. **GND commun** entre l'alim LEDs et l'ESP32. Sans ça, le signal DIN est
   flottant et l'anneau fait n'importe quoi.
3. **Résistance 330 Ω en série sur DIN** et **condensateur 1000 µF** sur le
   5 V. Ce sont les deux composants que tout le monde oublie et qui expliquent
   « ça marchait sur la table et plus dans la boîte ».

## 4. Impression

Modèle : [`models/socle_terminal.scad`](models/socle_terminal.scad) (paramétrique).

```bash
openscad models/socle_terminal.scad -D 'part="socle"'     -o socle.stl
openscad models/socle_terminal.scad -D 'part="bandeau"'   -o bandeau.stl
openscad models/socle_terminal.scad -D 'part="diffuseur"' -o diffuseur.stl
openscad models/socle_terminal.scad -D 'part="plaque"'    -o plaque.stl
```

| Pièce | Matière | Réglages | Temps |
|---|---|---|---|
| `socle` | PLA | 0.2 mm, 3 périmètres, 15 % | ~2 h 30 |
| `bandeau` | PLA | 0.2 mm, 3 périmètres | ~1 h |
| `diffuseur` | **PETG translucide naturel** | 0.2 mm, **2 périmètres, 0 %** | ~25 min |
| `plaque` | PLA | 0.2 mm, changement de couleur | ~20 min |

Avant d'imprimer : mesurer `terminal_l`, `terminal_w`, `terminal_h` et les
saisir en tête du `.scad`, puis regarder l'assemblage. La v1 du modèle n'a pas
été imprimée — c'est un point de départ à ajuster, pas un fichier validé.

Si tu n'as pas d'imprimante : le socle peut être remplacé par une cale en
contreplaqué ou un simple support de tablette. **L'impression ne doit jamais
bloquer la démo.**

## 5. Mise en route

```bash
# 1. flasher le firmware
#    Arduino IDE -> hardware/ring_signer_hsm/ring_signer_hsm.ino
#    carte "ESP32 Dev Module", bibliothèques Adafruit_NeoPixel + ESP32Servo

# 2. vérifier le port
python hardware/bridge/hardware_bridge.py --list-ports

# 3. tester sans matériel (aucun ESP32 requis)
python hardware/bridge/hardware_bridge.py --dry-run

# 4. répéter la séquence de démo, sans attaque ni Ansible
python hardware/bridge/hardware_bridge.py --port COM3 --replay

# 5. en conditions réelles
python hardware/bridge/hardware_bridge.py --port COM3 --api http://localhost:8084 --rich
```

`--replay` est l'outil le plus utile : il rejoue
`NORMAL_TX → UNDER_ATTACK → REMEDIATING → RECOVERED_TX` en boucle. Tu règles le
cadrage, la luminosité et le rythme sans toucher au PC attaquant.

## 6. Recette avant la soutenance

| # | Test | Attendu |
|---|---|---|
| 1 | pont lancé, aucune attaque | LEDs en rotation bleu/violet |
| 2 | `POST /incident` sur 8084 | rouge ~1 s, puis orange, puis vert |
| 3 | ESP32 débranché en cours de démo | le pont log et retente, le dashboard reste juste |
| 4 | pont arrêté puis relancé | resynchronisation via `/state`, pas de doublon |
| 5 | `--replay` pendant 2 min | aucune LED figée, aucun reboot ESP32 |
| 6 | écran éteint / LEDs à blanc | l'alim tient (pas de clignotement) |

Si le test 3 échoue, c'est un bug bloquant : l'objet ne doit **jamais** être
nécessaire à la démo.
