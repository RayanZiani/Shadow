// ring_signer_hsm.ino — HSM Monero factice, pilier Hardware 3D.
// Reçoit des commandes texte sur le port série (USB) ou via un petit
// serveur HTTP Wi-Fi (voir handleHttpState ci-dessous, optionnel).
//
// Commandes attendues (une ligne par commande) :
//   STATE:NORMAL_TX       -> roulette bleue/violette puis flash vert
//   STATE:UNDER_ATTACK    -> points rouges clignotants + servo verrouille
//   STATE:REMEDIATING     -> balayage orange/blanc
//   STATE:RECOVERED_TX    -> vert émeraude + servo ouvre/jeton
//
// Dépendances (Bibliothèques Arduino) : Adafruit_NeoPixel, ESP32Servo
// IMPORTANT (priorité P2 du plan) : ce module ne doit JAMAIS bloquer la
// démo — s'il est en panne, le dashboard doit pouvoir montrer la même
// info seul. Gardez-le découplé du reste (purement cosmétique).

#include <Adafruit_NeoPixel.h>
#include <ESP32Servo.h>

#define LED_PIN 5
#define LED_COUNT 24
#define SERVO_PIN 18

Adafruit_NeoPixel ring(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);
Servo trapdoorServo;

enum State { NORMAL_TX, UNDER_ATTACK, REMEDIATING, RECOVERED_TX, IDLE };
State currentState = IDLE;

void setup() {
  Serial.begin(115200);
  ring.begin();
  ring.show();
  trapdoorServo.attach(SERVO_PIN);
  trapdoorServo.write(0); // trappe fermée au repos
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    handleCommand(line);
  }
  renderState();
}

void handleCommand(const String &cmd) {
  if (cmd == "STATE:NORMAL_TX") currentState = NORMAL_TX;
  else if (cmd == "STATE:UNDER_ATTACK") currentState = UNDER_ATTACK;
  else if (cmd == "STATE:REMEDIATING") currentState = REMEDIATING;
  else if (cmd == "STATE:RECOVERED_TX") currentState = RECOVERED_TX;
  Serial.print("ACK:");
  Serial.println(cmd);
}

void renderState() {
  static unsigned long lastFrame = 0;
  static uint16_t frame = 0;
  if (millis() - lastFrame < 30) return; // ~33 fps, assez pour un anneau de LED
  lastFrame = millis();
  frame++;

  switch (currentState) {
    case NORMAL_TX:
      rouletteEffect(frame, ring.Color(80, 0, 200), ring.Color(0, 60, 200));
      if (frame % 100 == 0) flashAll(ring.Color(0, 200, 80), 1);
      trapdoorServo.write(0);
      break;

    case UNDER_ATTACK:
      contaminationEffect(frame);
      trapdoorServo.write(0); // verrouillée
      break;

    case REMEDIATING:
      sweepEffect(frame, ring.Color(255, 140, 0), ring.Color(255, 255, 255));
      trapdoorServo.write(0);
      break;

    case RECOVERED_TX:
      flashAll(ring.Color(0, 200, 80), 0);
      trapdoorServo.write(90); // ouvre / libère le jeton
      break;

    case IDLE:
    default:
      flashAll(ring.Color(10, 10, 10), 0);
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

void contaminationEffect(uint16_t frame) {
  for (int i = 0; i < LED_COUNT; i++) {
    bool contaminated = (random(0, 100) < 25); // "points rouges" aléatoires
    if (contaminated) {
      ring.setPixelColor(i, ring.Color(220, 0, 0));
    } else {
      ring.setPixelColor(i, ring.Color(20, 0, 0));
    }
  }
}

void sweepEffect(uint16_t frame, uint32_t colorA, uint32_t colorB) {
  int head = frame % LED_COUNT;
  for (int i = 0; i < LED_COUNT; i++) {
    int dist = (i - head + LED_COUNT) % LED_COUNT;
    ring.setPixelColor(i, dist < 3 ? colorB : colorA);
  }
}

void flashAll(uint32_t color, int holdFrames) {
  for (int i = 0; i < LED_COUNT; i++) ring.setPixelColor(i, color);
}
