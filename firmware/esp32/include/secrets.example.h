#pragma once

// Copy this file to include/secrets.h and edit the copy.
// include/secrets.h is ignored by Git.
// These Wi-Fi values are only the first-boot fallback. A network selected on
// the LCD is saved to NVS after it connects successfully and takes priority on
// later boots.
#define WIFI_SSID "YOUR_2G4_WIFI"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
#define SERVER_BASE_URL "http://192.168.1.100:8000"

// Required when SERVER_BASE_URL points at the public Tunnel gateway.
// Keep the real value in include/tunnel_secret.h, which is ignored by Git.
#define DEVICE_TOKEN "YOUR_RANDOM_DEVICE_TOKEN"
