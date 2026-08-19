#include "protocol.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace {

constexpr size_t kMaxProtocolFrameBytes = 160U;
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

FrameParseResult copyVerifiedPayload(const char *frame, char *payload,
                                     size_t payload_size,
                                     size_t *payload_length) {
  if (frame == nullptr || payload == nullptr || payload_length == nullptr) {
    return FrameParseResult::kNullArgument;
  }
  const size_t frame_length = strlen(frame);
  if (frame_length < 7U || frame_length > kMaxProtocolFrameBytes ||
      frame[0] != '$') {
    return FrameParseResult::kBadEnvelope;
  }
  const char *star = strrchr(frame, '*');
  if (star == nullptr || star <= frame + 1 || star[1] == '\0' ||
      star[2] == '\0' || star[3] != '\0') {
    return FrameParseResult::kBadEnvelope;
  }
  const int high = hexValue(star[1]);
  const int low = hexValue(star[2]);
  if (high < 0 || low < 0) {
    return FrameParseResult::kBadEnvelope;
  }
  const size_t length = static_cast<size_t>(star - (frame + 1));
  if (length + 1U > payload_size) {
    return FrameParseResult::kBadEnvelope;
  }
  const uint8_t supplied_checksum = static_cast<uint8_t>((high << 4) | low);
  if (protocolXor(frame + 1, length) != supplied_checksum) {
    return FrameParseResult::kBadChecksum;
  }
  memcpy(payload, frame + 1, length);
  payload[length] = '\0';
  *payload_length = length;
  return FrameParseResult::kOk;
}

FrameParseResult splitPayload(char *payload, size_t payload_length,
                              char **tokens, size_t token_capacity,
                              size_t *token_count) {
  if (payload == nullptr || tokens == nullptr || token_count == nullptr) {
    return FrameParseResult::kNullArgument;
  }
  *token_count = 0U;
  char *token_start = payload;
  for (size_t index = 0U; index <= payload_length; ++index) {
    if (payload[index] == ',' || payload[index] == '\0') {
      if (*token_count >= token_capacity) {
        return FrameParseResult::kWrongFieldCount;
      }
      payload[index] = '\0';
      tokens[(*token_count)++] = token_start;
      token_start = payload + index + 1U;
    }
  }
  return FrameParseResult::kOk;
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

FrameParseResult parseVisionFrame(const char *frame, VisionFrame *vision) {
  if (frame == nullptr || vision == nullptr) {
    return FrameParseResult::kNullArgument;
  }
  char payload[kMaxProtocolFrameBytes + 1U]{};
  size_t payload_length = 0U;
  FrameParseResult result = copyVerifiedPayload(
      frame, payload, sizeof(payload), &payload_length);
  if (result != FrameParseResult::kOk) {
    return result;
  }

  char *tokens[kVisionTokenCount]{};
  size_t token_count = 0U;
  result = splitPayload(payload, payload_length, tokens, kVisionTokenCount,
                        &token_count);
  if (result != FrameParseResult::kOk) {
    return result;
  }
  if (token_count != kVisionTokenCount) {
    return FrameParseResult::kWrongFieldCount;
  }
  if (strcmp(tokens[0], "VIS") != 0) {
    return FrameParseResult::kWrongMessageType;
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
    return FrameParseResult::kInvalidNumber;
  }
  parsed.person_detected = person != 0U;
  parsed.score = static_cast<uint8_t>(score);
  parsed.center_x = static_cast<uint16_t>(center_x);
  parsed.center_y = static_cast<uint16_t>(center_y);
  parsed.in_zone = in_zone != 0U;
  if (!parsed.person_detected &&
      (parsed.score != 0U || parsed.center_x != 0U ||
       parsed.center_y != 0U || parsed.in_zone)) {
    return FrameParseResult::kOutOfRange;
  }
  *vision = parsed;
  return FrameParseResult::kOk;
}

const char *frameParseResultName(FrameParseResult result) {
  switch (result) {
    case FrameParseResult::kOk:
      return "ok";
    case FrameParseResult::kNullArgument:
      return "null_argument";
    case FrameParseResult::kBadEnvelope:
      return "bad_envelope";
    case FrameParseResult::kBadChecksum:
      return "bad_checksum";
    case FrameParseResult::kWrongMessageType:
      return "wrong_message_type";
    case FrameParseResult::kWrongFieldCount:
      return "wrong_field_count";
    case FrameParseResult::kInvalidNumber:
      return "invalid_number";
    case FrameParseResult::kOutOfRange:
      return "out_of_range";
  }
  return "unknown";
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
