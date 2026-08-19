#pragma once

#include <stddef.h>
#include <stdint.h>

#include "telemetry.h"

enum class TelParseResult : uint8_t {
  kOk = 0,
  kNullArgument,
  kBadEnvelope,
  kBadChecksum,
  kWrongMessageType,
  kWrongFieldCount,
  kInvalidNumber,
  kOutOfRange,
};

// OpenMV keeps its existing checksummed VIS wire format when connected
// directly to the ESP32.  The score is detector confidence (0..100), not a
// calibrated probability.
struct VisionFrame {
  uint32_t seq;
  bool person_detected;
  uint8_t score;
  uint16_t center_x;
  uint16_t center_y;
  bool in_zone;
};

uint8_t protocolXor(const char *data, size_t length);
TelParseResult parseTelFrame(const char *frame, TelemetryFrame *telemetry);
TelParseResult parseVisionFrame(const char *frame, VisionFrame *vision);
const char *telParseResultName(TelParseResult result);

bool buildNetFrame(char *output, size_t output_size, bool wifi_connected,
                   bool server_reachable, int32_t rssi,
                   uint32_t unix_time);

// ESP32 -> OpenMV camera-mode control. The strict wire format is:
//   $CTL,<seq>,<danger>,<person_enable>,<environmental_level>*<XOR>\r\n
// danger may be true for either a trusted fresh model warning/critical result
// or a live ESP32 water-rise/rate hazard with healthy ultrasonic input.
// person_enable may remain true with danger=false for fail-safe/advisory
// monitoring, and is mandatory for every warning/critical diagnostic level;
// OpenMV must use danger (plus stable person detection), never
// environmental_level alone, to drive its red LED.
bool buildOpenMvControlFrame(char *output, size_t output_size, uint16_t seq,
                             bool danger, bool person_enable,
                             uint8_t environmental_level);
