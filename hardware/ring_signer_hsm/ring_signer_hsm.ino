// ring_signer_hsm.ino — Terminal de paiement RESILIENCE-ALPHA.
//
// Rôle : MIROIR D'ÉTAT. Le firmware ne décide rien, ne signe rien et ne
// déclenche aucune remédiation. Il affiche ce que remediation-engine publie
// (cf. docs/hardware/CONCEPT-PHYSIQUE.md).
//
// Deux entrées possibles, une seule sortie visuelle :
//   1. USB série (primaire) : hardware/bridge/hardware_bridge.py envoie
//      une ligne ASCII par changement d'état :
//          STATE:UNDER_ATTACK
//          STATE:UNDER_ATTACK;SCORE:1.83;N:11
//   2. Clavier du terminal (local) : les touches ne parlent PAS au firmware,
//      elles passent par l'IHM (payment-api). Le firmware n'est pas un
//      contrôleur de remédiation.
//
// Le firmware répond "ACK:<trame>" pour que le pont puisse journaliser la sync.
//
// Dépendances Arduino : Adafruit_NeoPixel, ESP32Servo
// Carte : ESP32 Dev Module. Aucun Wi-Fi : le point d'accès mobile reste un
// secours, jamais le chemin primaire (voir doc, §4 règle 5).

#include <Adafruit_NeoPixel.h>
#include <ESP32Servo.h>

#define LED_PIN 5
#define LED_COUNT 24
#define SERVO_PIN 18
#define SERIAL_BAUD 115200

// Durée de maintien d'un flash, en frames (~33 fps => 6 frames ≈ 180 ms).
#define FLASH_HOLD_FRAMES 6

Adafruit_NeoPixel ring(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);
Servo trapdoorServo;

enum State { IDLE, NORMAL_TX, UNDER_ATTACK, REMEDIATING, RECOVERED_TX, REMEDIATION_FAILED };
State currentState = IDLE;

// Position servo mémorisée : on n'écrit que sur changement, sinon le servo
// vibre et bourdonne pendant toute la démo (33 écritures/seconde).
int servoPosition = -1;

// Petit état d'affichage, alimenté par la trame (optionnel).
float lastScore = 0.0f;
int lastRingSize = 0;
bool hasDetail = false;

static const char *stateName(State s) {
  switch (s) {
    case NORMAL_TX: return "NORMAL_TX";
    case UNDER_ATTACK: return "UNDER_ATTACK";
    case REMEDIATING: return "REMEDIATING";
    case RECOVERED_TX: return "RECOVERED_TX";
    case REMEDIATION_FAILED: return "REMEDIATION_FAILED";
    default: return "IDLE";
  }
}

void setServo(int angle) {
  if (angle == servoPosition) return;
  servoPosition = angle;
  trapdoorServo.write(angle);
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  ring.begin();
  ring.setBrightness(60);  // 24 LEDs à pleine puissance = 1,4 A, inutile ici
  ring.show();
  trapdoorServo.attach(SERVO_PIN);
  setServo(0);  // trappe fermée au repos

  // Sans seed, random() rejoue la même séquence à chaque boot : les "points
  // rouges" de UNDER_ATTACK seraient toujours identiques (visible sur le clip).
  randomSeed(esp_random());

  Serial.println("READY:terminal-resilience-alpha");
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) handleCommand(line);
  }
  renderState();
}

// Trame acceptée : STATE:<NOM>[;SCORE:<float>][;N:<int>]
void handleCommand(const String &cmd) {
  if (!cmd.startsWith("STATE:")) {
    Serial.print("ERR:unknown:");
    Serial.println(cmd);
    return;
  }

  String body = cmd.substring(6);
  int sep = body.indexOf(';');
  String name = (sep >= 0) ? body.substring(0, sep) : body;
  String detail = (sep >= 0) ? body.substring(sep + 1) : "";

  State parsed;
  if (!parseState(name, parsed)) {
    Serial.print("ERR:state:");
    Serial.println(name);
    return;
  }

  currentState = parsed;
  parseDetail(detail);

  Serial.print("ACK:");
  Serial.println(cmd);
}

bool parseState(const String &name, State &out) {
  if (name == "IDLE") out = IDLE;
  else if (name == "NORMAL_TX") out = NORMAL_TX;
  else if (name == "UNDER_ATTACK") out = UNDER_ATTACK;
  else if (name == "REMEDIATING") out = REMEDIATING;
  else if (name == "RECOVERED_TX") out = RECOVERED_TX;
  else if (name == "REMEDIATION_FAILED") out = REMEDIATION_FAILED;
  else return false;
  return true;
}

// Parse "SCORE:1.83;N:11" sans dépendance externe.
void parseDetail(const String &detail) {
  hasDetail = false;
  int start = 0;
  while (start < (int)detail.length()) {
    int end = detail.indexOf(';', start);
    if (end < 0) end = detail.length();
    String field = detail.substring(start, end);
    int colon = field.indexOf(':');
    if (colon > 0) {
      String key = field.substring(0, colon);
      String value = field.substring(colon + 1);
      if (key == "SCORE") { lastScore = value.toFloat(); hasDetail = true; }
      else if (key == "N") { lastRingSize = value.toInt(); hasDetail = true; }
    }
    start = end + 1;
  }
}

void renderState() {
  static unsigned long lastFrame = 0;
  static uint16_t frame = 0;
  static uint8_t flashLeft = 0;  // frames de maintien restantes

  if (millis() - lastFrame < 30) return;  // ~33 fps
  lastFrame = millis();
  frame++;

  switch (currentState) {
    case NORMAL_TX:
      rouletteEffect(frame, ring.Color(80, 0, 200), ring.Color(0, 60, 200));
      // Flash de confirmation : on réserve des frames pour que l'effet ne soit
      // pas écrasé au frame suivant par la roulette (bug de la v1).
      if (frame % 100 == 0) flashLeft = FLASH_HOLD_FRAMES;
      if (flashLeft > 0) { flashAll(ring.Color(0, 200, 80)); flashLeft--; }
      setServo(0);
      break;

    case UNDER_ATTACK:
      contaminationEffect(frame);
      setServo(0);  // trappe verrouillée
      break;

    case REMEDIATING:
      sweepEffect(frame, ring.Color(255, 140, 0), ring.Color(255, 255, 255));
      setServo(0);
      break;

    case RECOVERED_TX:
      flashAll(ring.Color(0, 200, 80));
      setServo(90);  // ouvre la trappe : le jeton de preuve est libéré
      break;

    case REMEDIATION_FAILED:
      // Rouge fixe, sans animation : lisible à distance et sans ambiguïté.
      flashAll((frame / 15) % 2 ? ring.Color(180, 0, 0) : ring.Color(40, 0, 0));
      setServo(0);
      break;

    case IDLE:
    default:
      flashAll(ring.Color(0, 0, 0));
      setServo(0);
      break;
  }
  ring.show();
}

void rouletteEffect(uint16_t frame, uint32_t colorA, uint32_t colorB) {
  for (int i = 0; i < LED_COUNT; i++) {
    uint32_t c = ((i + frame / 2) % 4 == 0) ? colorA : colorB;
    ring.setPixelColor(i, c);
  }
}

// Les positions sont tirées une fois par frame (pas par LED) : sans ça, les
// points changent 24 fois par frame et l'effet devient du bruit blanc.
void contaminationEffect(uint16_t frame) {
  static uint32_t seed = 0;
  for (int i = 0; i < LED_COUNT; i++) {
    uint32_t h = (i * 2654435761u) ^ (frame / 4) ^ seed;
    bool contaminated = (h % 100) < 25;
    ring.setPixelColor(i, contaminated ? ring.Color(220, 0, 0) : ring.Color(20, 0, 0));
  }
  seed++;
}

void sweepEffect(uint16_t frame, uint32_t colorA, uint32_t colorB) {
  int head = frame % LED_COUNT;
  for (int i = 0; i < LED_COUNT; i++) {
    int dist = (i - head + LED_COUNT) % LED_COUNT;
    ring.setPixelColor(i, dist < 3 ? colorB : colorA);
  }
}

void flashAll(uint32_t color) {
  for (int i = 0; i < LED_COUNT; i++) ring.setPixelColor(i, color);
}
