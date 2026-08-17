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

uint8_t protocolXor(const char *data, size_t length);
TelParseResult parseTelFrame(const char *frame, TelemetryFrame *telemetry);
const char *telParseResultName(TelParseResult result);

bool buildNetFrame(char *output, size_t output_size, bool wifi_connected,
                   bool server_reachable, int32_t rssi,
                   uint32_t unix_time);

