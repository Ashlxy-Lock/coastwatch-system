#pragma once

// Copy this file to include/secrets.h and edit the copy.
// include/secrets.h is ignored by Git.
// These Wi-Fi values are only the first-boot fallback. Up to 16 networks
// selected on the LCD are saved to NVS after successful connection; the most
// recently successful profile is tried first on later boots.
#define WIFI_SSID "YOUR_2G4_WIFI"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
#define SERVER_BASE_URL "http://192.168.1.100:8000"

// Required when SERVER_BASE_URL points at the public Tunnel gateway.
// Keep the real value in include/tunnel_secret.h, which is ignored by Git.
#define DEVICE_TOKEN "YOUR_RANDOM_DEVICE_TOKEN"

// Change to 1 only after HC-SR04 ECHO has a verified 5 V -> 3.3 V divider or
// level-shifter. ESP32-S3 has no 5 V-tolerant GPIO. The safe default leaves
// TRIG/ECHO disabled.
#define ULTRASONIC_ECHO_LEVEL_SHIFT_VERIFIED 0
