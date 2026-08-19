#include "protocol.h"

#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace {

constexpr size_t kMaxProtocolFrameBytes = 160U;
constexpr size_t kTelTokenCount = 9U;
constexpr size_t kVisionTokenCount = 7U;

int hexValue(char value) {
  if (value >= '0' && value <= '9') {
    return value - '0';
  }
  if (value >= 'A' && value <= 'F') {
    return value - 'A' + 10;
  }
  if (value >= 'a' && value <= 'f') {
    return value - 'a' + 10;
  }
  return -1;
}

bool parseUnsigned(const char *text, uint32_t maximum, uint32_t *value) {
  if (text == nullptr || value == nullptr || *text == '\0' || *text == '-') {
    return false;
  }
  errno = 0;
  char *end = nullptr;
  const unsigned long parsed = strtoul(text, &end, 10);
  if (errno != 0 || end == text || *end != '\0' || parsed > maximum) {
    return false;
  }
  *value = static_cast<uint32_t>(parsed);
  return true;
}

bool parseSigned(const char *text, int32_t *value) {
  if (text == nullptr || value == nullptr || *text == '\0') {
    return false;
  }
  errno = 0;
  char *end = nullptr;
  const long parsed = strtol(text, &end, 10);
  if (errno != 0 || end == text || *end != '\0' || parsed < INT32_MIN ||
      parsed > INT32_MAX) {
    return false;
  }
  *value = static_cast<int32_t>(parsed);
  return true;
}

TelParseResult copyVerifiedPayload(const char *frame, char *payload,
                                   size_t payload_size,
                                   size_t *payload_length) {
  if (frame == nullptr || payload == nullptr || payload_length == nullptr) {
    return TelParseResult::kNullArgument;
  }
  const size_t frame_length = strlen(frame);
  if (frame_length < 7U || frame_length > kMaxProtocolFrameBytes ||
      frame[0] != '$') {
    return TelParseResult::kBadEnvelope;
  }
  const char *star = strrchr(frame, '*');
  if (star == nullptr || star <= frame + 1 || star[1] == '\0' ||
      star[2] == '\0' || star[3] != '\0') {
    return TelParseResult::kBadEnvelope;
  }
  const int high = hexValue(star[1]);
  const int low = hexValue(star[2]);
  if (high < 0 || low < 0) {
    return TelParseResult::kBadEnvelope;
  }
  const size_t length = static_cast<size_t>(star - (frame + 1));
  if (length + 1U > payload_size) {
    return TelParseResult::kBadEnvelope;
  }
  const uint8_t supplied_checksum = static_cast<uint8_t>((high << 4) | low);
  if (protocolXor(frame + 1, length) != supplied_checksum) {
    return TelParseResult::kBadChecksum;
  }
  memcpy(payload, frame + 1, length);
  payload[length] = '\0';
  *payload_length = length;
  return TelParseResult::kOk;
}

TelParseResult splitPayload(char *payload, size_t payload_length,
                            char **tokens, size_t token_capacity,
                            size_t *token_count) {
  if (payload == nullptr || tokens == nullptr || token_count == nullptr) {
    return TelParseResult::kNullArgument;
  }
  *token_count = 0U;
  char *token_start = payload;
  for (size_t index = 0U; index <= payload_length; ++index) {
    if (payload[index] == ',' || payload[index] == '\0') {
      if (*token_count >= token_capacity) {
        return TelParseResult::kWrongFieldCount;
      }
      payload[index] = '\0';
      tokens[(*token_count)++] = token_start;
      token_start = payload + index + 1U;
    }
  }
  return TelParseResult::kOk;
}

}  // namespace

uint8_t protocolXor(const char *data, size_t length) {
  uint8_t checksum = 0U;
  if (data == nullptr) {
    return checksum;
  }
  for (size_t index = 0U; index < length; ++index) {
    checksum ^= static_cast<uint8_t>(data[index]);
  }
  return checksum;
}

TelParseResult parseTelFrame(const char *frame, TelemetryFrame *telemetry) {
  if (frame == nullptr || telemetry == nullptr) {
    return TelParseResult::kNullArgument;
  }

  char payload[kMaxProtocolFrameBytes + 1U]{};
  size_t payload_length = 0U;
  TelParseResult result = copyVerifiedPayload(
      frame, payload, sizeof(payload), &payload_length);
  if (result != TelParseResult::kOk) {
    return result;
  }

  char *tokens[kTelTokenCount]{};
  size_t token_count = 0U;
  result = splitPayload(payload, payload_length, tokens, kTelTokenCount,
                        &token_count);
  if (result != TelParseResult::kOk) {
    return result;
  }

  if (token_count != kTelTokenCount) {
    return TelParseResult::kWrongFieldCount;
  }
  if (strcmp(tokens[0], "TEL") != 0) {
    return TelParseResult::kWrongMessageType;
  }

  TelemetryFrame parsed{};
  uint32_t person = 0U;
  uint32_t alarm = 0U;
  if (!parseUnsigned(tokens[1], UINT32_MAX, &parsed.seq) ||
      !parseUnsigned(tokens[2], UINT32_MAX, &parsed.uptime_ms) ||
      !parseUnsigned(tokens[3], UINT32_MAX, &parsed.distance_mm) ||
      !parseSigned(tokens[4], &parsed.water_rise_mm) ||
      !parseSigned(tokens[5], &parsed.rise_rate_mm_s) ||
      !parseUnsigned(tokens[6], 1U, &person) ||
      !parseUnsigned(tokens[7], 4U, &alarm) ||
      !parseUnsigned(tokens[8], UINT32_MAX, &parsed.health_flags)) {
    return TelParseResult::kInvalidNumber;
  }

  parsed.person_detected = person != 0U;
  parsed.alarm_level = static_cast<uint8_t>(alarm);
  *telemetry = parsed;
  return TelParseResult::kOk;
}

TelParseResult parseVisionFrame(const char *frame, VisionFrame *vision) {
  if (frame == nullptr || vision == nullptr) {
    return TelParseResult::kNullArgument;
  }
  char payload[kMaxProtocolFrameBytes + 1U]{};
  size_t payload_length = 0U;
  TelParseResult result = copyVerifiedPayload(
      frame, payload, sizeof(payload), &payload_length);
  if (result != TelParseResult::kOk) {
    return result;
  }

  char *tokens[kVisionTokenCount]{};
  size_t token_count = 0U;
  result = splitPayload(payload, payload_length, tokens, kVisionTokenCount,
                        &token_count);
  if (result != TelParseResult::kOk) {
    return result;
  }
  if (token_count != kVisionTokenCount) {
    return TelParseResult::kWrongFieldCount;
  }
  if (strcmp(tokens[0], "VIS") != 0) {
    return TelParseResult::kWrongMessageType;
  }

  VisionFrame parsed{};
  uint32_t person = 0U;
  uint32_t score = 0U;
  uint32_t center_x = 0U;
  uint32_t center_y = 0U;
  uint32_t in_zone = 0U;
  if (!parseUnsigned(tokens[1], 65535U, &parsed.seq) ||
      !parseUnsigned(tokens[2], 1U, &person) ||
      !parseUnsigned(tokens[3], 100U, &score) ||
      !parseUnsigned(tokens[4], 4095U, &center_x) ||
      !parseUnsigned(tokens[5], 4095U, &center_y) ||
      !parseUnsigned(tokens[6], 1U, &in_zone)) {
    return TelParseResult::kInvalidNumber;
  }
  parsed.person_detected = person != 0U;
  parsed.score = static_cast<uint8_t>(score);
  parsed.center_x = static_cast<uint16_t>(center_x);
  parsed.center_y = static_cast<uint16_t>(center_y);
  parsed.in_zone = in_zone != 0U;
  if (!parsed.person_detected &&
      (parsed.score != 0U || parsed.center_x != 0U ||
       parsed.center_y != 0U || parsed.in_zone)) {
    return TelParseResult::kOutOfRange;
  }
  *vision = parsed;
  return TelParseResult::kOk;
}

const char *telParseResultName(TelParseResult result) {
  switch (result) {
    case TelParseResult::kOk:
      return "ok";
    case TelParseResult::kNullArgument:
      return "null_argument";
    case TelParseResult::kBadEnvelope:
      return "bad_envelope";
    case TelParseResult::kBadChecksum:
      return "bad_checksum";
    case TelParseResult::kWrongMessageType:
      return "wrong_message_type";
    case TelParseResult::kWrongFieldCount:
      return "wrong_field_count";
    case TelParseResult::kInvalidNumber:
      return "invalid_number";
    case TelParseResult::kOutOfRange:
      return "out_of_range";
  }
  return "unknown";
}

bool buildNetFrame(char *output, size_t output_size, bool wifi_connected,
                   bool server_reachable, int32_t rssi,
                   uint32_t unix_time) {
  if (output == nullptr || output_size == 0U) {
    return false;
  }

  char payload[96]{};
  const int payload_length =
      snprintf(payload, sizeof(payload), "NET,%u,%u,%ld,%lu",
               wifi_connected ? 1U : 0U, server_reachable ? 1U : 0U,
               static_cast<long>(rssi), static_cast<unsigned long>(unix_time));
  if (payload_length <= 0 || static_cast<size_t>(payload_length) >= sizeof(payload)) {
    return false;
  }

  const uint8_t checksum = protocolXor(payload, static_cast<size_t>(payload_length));
  const int output_length = snprintf(output, output_size, "$%s*%02X\n", payload,
                                     static_cast<unsigned int>(checksum));
  return output_length > 0 && static_cast<size_t>(output_length) < output_size;
}

bool buildOpenMvControlFrame(char *output, size_t output_size, uint16_t seq,
                             bool danger, bool person_enable,
                             uint8_t environmental_level) {
  if (output == nullptr || output_size == 0U || environmental_level > 3U) {
    return false;
  }
  // A danger command without person monitoring is contradictory. A local
  // warning may legitimately assert danger while the diagnostic model level
  // remains safe/advisory, so danger must not be inferred from this field.
  if ((danger || environmental_level >= 2U) && !person_enable) {
    return false;
  }

  char payload[48]{};
  const int payload_length =
      snprintf(payload, sizeof(payload), "CTL,%u,%u,%u,%u",
               static_cast<unsigned int>(seq), danger ? 1U : 0U,
               person_enable ? 1U : 0U,
               static_cast<unsigned int>(environmental_level));
  if (payload_length <= 0 ||
      static_cast<size_t>(payload_length) >= sizeof(payload)) {
    return false;
  }

  const uint8_t checksum =
      protocolXor(payload, static_cast<size_t>(payload_length));
  const int output_length =
      snprintf(output, output_size, "$%s*%02X\r\n", payload,
               static_cast<unsigned int>(checksum));
  return output_length > 0 &&
         static_cast<size_t>(output_length) < output_size;
}
