// Copier en `secrets.h` — ce fichier-ci est versionné (aucun secret dedans).
//
// Le PC hôte partage son point d'accès « RESILIENCE-G<n> » : son adresse sur
// ce réseau est celle de la passerelle. Sous Windows, la trouver avec :
//
//   ipconfig | findstr /i "Passerelle"
//
// Ne PAS mettre l'IP du réseau domestique : l'ESP doit être sur le point
// d'accès de la solution, comme l'exige la consigne.
#pragma once

#define WIFI_SSID "RESILIENCE-G1"
#define WIFI_PASS "changez-moi"

// PC hôte = passerelle du point d'accès, sur le port du back-office.
#define BO_HOST "192.168.137.1"
#define BO_PORT 8086

// Doit correspondre à LED_DEVICE_TOKEN dans `.env`.
#define LED_TOKEN "led-demo-token"
