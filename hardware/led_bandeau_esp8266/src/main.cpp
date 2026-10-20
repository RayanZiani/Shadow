// ---------------------------------------------------------------------------
// Bandeau LED ESP8266 — RESILIENCE-ALPHA
//
// Consigne additionnelle : une LED bicolore (rouge/vert) pilotée par un
// ESP8266, connecté en WiFi au point d'accès RESILIENCE-G<n>, qui reflète
// l'état de l'authentification du back-office.
//
//   état              | LED
//   ------------------+--------------------------------------------------
//   off               | éteinte (défaut au boot)
//   green_blink       | vert clignotant  — visage reconnu, mot de passe attendu
//   green_solid       | vert fixe 30 s   — mot de passe validé
//   red_blink         | rouge, 10 flashs — mot de passe refusé
//   red_solid         | rouge fixe 30 s  — visage non reconnu
//
// Câblage : LED bicolore (ou deux LEDs) sur D1 (GPIO5) = vert, D2 (GPIO4) = rouge,
// cathodes communes à la masse via 2 × 220 Ω. D4 (GPIO2) est la LED bleue de la
// carte : elle sert de témoin WiFi et n'est jamais utilisée pour l'état.
//
// Toolchain imposée : JetBrains CLion + PlatformIO (voir platformio.ini).
// ---------------------------------------------------------------------------
#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <WiFiClient.h>

// `secrets.h` n'est pas versionné : il contient le mot de passe WiFi du point
// d'accès RESILIENCE-G<n>. Copier `secrets.example.h`. Sans lui, le firmware
// COMPILE quand même (pour la CI et la relecture), mais se connecte à un SSID
// factice : le journal série le dit explicitement.
#if __has_include("secrets.h")
#include "secrets.h"
#else
#warning "secrets.h absent : copier src/secrets.example.h vers src/secrets.h"
#define WIFI_SSID "RESILIENCE-G1"
#define WIFI_PASS "changez-moi"
#define BO_HOST "192.168.137.1"
#define BO_PORT 8086
#define LED_TOKEN "led-demo-token"
#endif

// --- Broches ---------------------------------------------------------------
static const uint8_t PIN_GREEN = D1;  // GPIO5
static const uint8_t PIN_RED = D2;    // GPIO4
static const uint8_t PIN_STATUS = LED_BUILTIN;  // GPIO2, actif à l'état bas

// Nom envoyé au back-office : il apparaît dans `/healthz` (`led.last_device`)
// et dans les métriques, ce qui permet de distinguer deux cartes.
static const char *DEVICE_NAME = "resilience-led";

// --- Temporisations locales (le firmware reste maître du temps) ------------
static const uint32_t POLL_INTERVAL_MS = 300;   // interrogation du back-office
static const uint32_t BLINK_HALF_PERIOD_MS = 250;  // 2 Hz pour le vert
static const uint32_t RED_BLINK_HALF_PERIOD_MS = 180;  // ~2,8 Hz pour le rouge
static const uint32_t HTTP_TIMEOUT_MS = 900;
static const uint32_t WIFI_RETRY_MS = 5000;
static const uint32_t STALE_AFTER_MS = 5000;  // sans contact : on s'éteint

enum LedState : uint8_t {
  ST_OFF = 0,
  ST_GREEN_BLINK,
  ST_GREEN_SOLID,
  ST_RED_BLINK,
  ST_RED_SOLID,
};

struct Desired {
  LedState state = ST_OFF;
  uint32_t seconds = 0;
  uint8_t blinks = 0;
  long seq = -1;
};

static Desired desired;
static Desired applied;
static uint32_t stateStartedAt = 0;
static uint32_t lastPollAt = 0;
static uint32_t lastContactAt = 0;
static uint8_t blinksDone = 0;
static bool blinkPhase = false;

static WiFiClient wifiClient;
static bool wifiWasConnected = false;

// ---------------------------------------------------------------------------
// Sorties
// ---------------------------------------------------------------------------
static void setLeds(bool green, bool red) {
  digitalWrite(PIN_GREEN, green ? HIGH : LOW);
  digitalWrite(PIN_RED, red ? HIGH : LOW);
}

static void setStatusLed(bool on) { digitalWrite(PIN_STATUS, on ? LOW : HIGH); }

static LedState parseState(const char *name) {
  if (name == nullptr) return ST_OFF;
  if (strcmp(name, "green_blink") == 0) return ST_GREEN_BLINK;
  if (strcmp(name, "green_solid") == 0) return ST_GREEN_SOLID;
  if (strcmp(name, "red_blink") == 0) return ST_RED_BLINK;
  if (strcmp(name, "red_solid") == 0) return ST_RED_SOLID;
  return ST_OFF;
}

// ---------------------------------------------------------------------------
// WiFi
// ---------------------------------------------------------------------------
static void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("[wifi] connexion a %s\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

// ---------------------------------------------------------------------------
// Récupération de l'état désiré
// ---------------------------------------------------------------------------
static bool fetchState() {
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setReuse(false);
  String url = String("http://") + BO_HOST + ":" + String(BO_PORT) +
               "/led/state?device=" + String(DEVICE_NAME);
  if (!http.begin(wifiClient, url)) return false;
  http.addHeader("X-LED-Token", LED_TOKEN);
  int code = http.GET();
  if (code != 200) {
    http.end();
    Serial.printf("[http] %d\n", code);
    return false;
  }

  // Filtre JSON : on ne garde que les champs utiles, la réponse fait < 200 o.
  StaticJsonDocument<384> filter;
  filter["state"] = true;
  filter["seconds"] = true;
  filter["blinks"] = true;
  filter["seq"] = true;
  filter["enabled"] = true;

  StaticJsonDocument<384> doc;
  DeserializationError error = deserializeJson(doc, http.getStream(), DeserializationOption::Filter(filter));
  http.end();
  if (error) {
    Serial.printf("[json] %s\n", error.c_str());
    return false;
  }

  long seq = doc["seq"] | -1;
  bool enabled = doc["enabled"] | true;
  if (seq < 0) return false;

  lastContactAt = millis();
  if (!enabled) {
    desired.state = ST_OFF;
  } else {
    desired.state = parseState(doc["state"] | "off");
  }
  desired.seconds = doc["seconds"] | 0;
  desired.blinks = doc["blinks"] | 0;
  desired.seq = seq;
  return true;
}

// ---------------------------------------------------------------------------
// Application de l'état
// ---------------------------------------------------------------------------
static void applyState() {
  applied = desired;
  applied.seq = desired.seq;
  stateStartedAt = millis();
  blinksDone = 0;
  blinkPhase = false;

  switch (applied.state) {
    case ST_GREEN_SOLID:
      setLeds(true, false);
      break;
    case ST_RED_SOLID:
      setLeds(false, true);
      break;
    case ST_GREEN_BLINK:
      setLeds(true, false);
      blinkPhase = true;
      break;
    case ST_RED_BLINK:
      setLeds(false, true);
      blinkPhase = true;
      break;
    default:
      setLeds(false, false);
      break;
  }
  Serial.printf("[etat] seq=%ld state=%u sec=%lu blink=%u\n", applied.seq,
                (unsigned)applied.state, (unsigned long)applied.seconds,
                (unsigned)applied.blinks);
}

static void serviceState() {
  uint32_t now = millis();
  uint32_t elapsed = now - stateStartedAt;

  // Sécurité : si le back-office ne répond plus, on s'éteint au bout de 5 s.
  // Un bandeau qui reste vert alors que le service est tombé serait un mensonge.
  if (lastContactAt != 0 && (now - lastContactAt) > STALE_AFTER_MS &&
      applied.state != ST_OFF) {
    desired.state = ST_OFF;
    desired.seq = desired.seq;  // pas de nouvelle séquence : simple sécurité
    applyState();
    return;
  }

  switch (applied.state) {
    case ST_GREEN_BLINK:
      if (now - stateStartedAt >= BLINK_HALF_PERIOD_MS) {
        stateStartedAt = now;
        blinkPhase = !blinkPhase;
        setLeds(blinkPhase, false);
      }
      break;

    case ST_RED_BLINK: {
      // Exactement `blinks` flashs rouges, puis extinction.
      uint8_t target = applied.blinks == 0 ? 10 : applied.blinks;
      if (now - stateStartedAt >= RED_BLINK_HALF_PERIOD_MS) {
        stateStartedAt = now;
        blinkPhase = !blinkPhase;
        setLeds(false, blinkPhase);
        if (!blinkPhase) {
          blinksDone++;
          if (blinksDone >= target) {
            setLeds(false, false);
            applied.state = ST_OFF;
            desired.state = ST_OFF;
          }
        }
      }
      break;
    }

    case ST_GREEN_SOLID:
    case ST_RED_SOLID: {
      uint32_t duration = (applied.seconds == 0 ? 30 : applied.seconds) * 1000UL;
      if (elapsed >= duration) {
        setLeds(false, false);
        applied.state = ST_OFF;
        desired.state = ST_OFF;
      }
      break;
    }

    default:
      setLeds(false, false);
      break;
  }
}

// ---------------------------------------------------------------------------
// Arduino
// ---------------------------------------------------------------------------
void setup() {
  pinMode(PIN_GREEN, OUTPUT);
  pinMode(PIN_RED, OUTPUT);
  pinMode(PIN_STATUS, OUTPUT);
  setLeds(false, false);
  setStatusLed(false);

  Serial.begin(115200);
  Serial.println();
  Serial.println("[boot] bandeau LED RESILIENCE-ALPHA");
  Serial.printf("[boot] hostname=%s\n", DEVICE_NAME);

  WiFi.hostname(DEVICE_NAME);
  connectWifi();

  // Défaut imposé par la consigne : LED éteinte tant que personne ne s'authentifie.
  applied.state = ST_OFF;
  desired.state = ST_OFF;
  stateStartedAt = millis();
}

void loop() {
  uint32_t now = millis();

  if (WiFi.status() != WL_CONNECTED) {
    if (wifiWasConnected) {
      Serial.println("[wifi] perdu");
      wifiWasConnected = false;
      setStatusLed(false);
      // Pas de mensonge : sans réseau, la LED d'état s'éteint.
      if (applied.state != ST_OFF) {
        desired.state = ST_OFF;
        applyState();
      }
    }
    setStatusLed((now / 200) % 2 == 0);  // clignotement lent = recherche WiFi
    if (now - lastPollAt > WIFI_RETRY_MS) {
      lastPollAt = now;
      connectWifi();
    }
    return;
  }

  if (!wifiWasConnected) {
    wifiWasConnected = true;
    setStatusLed(true);
    Serial.printf("[wifi] connecte, ip=%s\n", WiFi.localIP().toString().c_str());
  }

  if (now - lastPollAt >= POLL_INTERVAL_MS) {
    lastPollAt = now;
    fetchState();
  }

  // On n'applique que si la séquence a changé (l'ESP ne rejoue pas une trame).
  if (desired.seq != applied.seq || desired.state != applied.state) {
    applyState();
  }

  serviceState();
  yield();
}
