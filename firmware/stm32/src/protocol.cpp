#include "protocol.h"

#include <cerrno>
#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace coastwatch::protocol {
namespace {

int hex_value(char value) {
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

bool parse_unsigned(const char* text, std::uint32_t maximum,
                    std::uint32_t* output) {
  if (text == nullptr || output == nullptr || text[0] == '\0' ||
      text[0] == '-') {
    return false;
  }
  errno = 0;
  char* end = nullptr;
  const unsigned long long value = std::strtoull(text, &end, 10);
  if (errno != 0 || end == text || *end != '\0' || value > maximum) {
    return false;
  }
  *output = static_cast<std::uint32_t>(value);
  return true;
}

bool parse_signed(const char* text, std::int32_t minimum,
                  std::int32_t maximum, std::int32_t* output) {
  if (text == nullptr || output == nullptr || text[0] == '\0') {
    return false;
  }
  errno = 0;
  char* end = nullptr;
  const long long value = std::strtoll(text, &end, 10);
  if (errno != 0 || end == text || *end != '\0' || value < minimum ||
      value > maximum) {
    return false;
  }
  *output = static_cast<std::int32_t>(value);
  return true;
}

ParseResult tokenize(const char* frame, char* payload, std::size_t payload_size,
                     char** tokens, std::size_t token_capacity,
                     std::size_t* token_count) {
  if (frame == nullptr || payload == nullptr || tokens == nullptr ||
      token_count == nullptr) {
    return ParseResult::kNullArgument;
  }

  std::size_t frame_length = std::strlen(frame);
  while (frame_length > 0U &&
         (frame[frame_length - 1U] == '\r' ||
          frame[frame_length - 1U] == '\n')) {
    --frame_length;
  }
  if (frame_length < 7U || frame_length > kMaxFrameBytes || frame[0] != '$') {
    return ParseResult::kBadEnvelope;
  }

  std::size_t star_index = frame_length;
  for (std::size_t index = frame_length; index > 0U; --index) {
    if (frame[index - 1U] == '*') {
      star_index = index - 1U;
      break;
    }
  }
  if (star_index <= 1U || star_index + 3U != frame_length) {
    return ParseResult::kBadEnvelope;
  }
  const int high = hex_value(frame[star_index + 1U]);
  const int low = hex_value(frame[star_index + 2U]);
  if (high < 0 || low < 0) {
    return ParseResult::kBadEnvelope;
  }

  const std::size_t payload_length = star_index - 1U;
  if (payload_length + 1U > payload_size) {
    return ParseResult::kBadEnvelope;
  }
  const std::uint8_t supplied =
      static_cast<std::uint8_t>((high << 4) | low);
  if (xor_checksum(frame + 1U, payload_length) != supplied) {
    return ParseResult::kBadChecksum;
  }

  std::memcpy(payload, frame + 1U, payload_length);
  payload[payload_length] = '\0';
  *token_count = 0U;
  char* start = payload;
  for (std::size_t index = 0U; index <= payload_length; ++index) {
    if (payload[index] == ',' || payload[index] == '\0') {
      if (*token_count >= token_capacity) {
        return ParseResult::kWrongFieldCount;
      }
      payload[index] = '\0';
      tokens[(*token_count)++] = start;
      start = payload + index + 1U;
    }
  }
  return ParseResult::kOk;
}

}  // namespace

std::uint8_t xor_checksum(const char* data, std::size_t length) {
  std::uint8_t checksum = 0U;
  if (data == nullptr) {
    return checksum;
  }
  for (std::size_t index = 0U; index < length; ++index) {
    checksum ^= static_cast<std::uint8_t>(data[index]);
  }
  return checksum;
}

ParseResult parse_vision(const char* frame, VisionFrame* output) {
  if (output == nullptr) {
    return ParseResult::kNullArgument;
  }
  char payload[kMaxFrameBytes + 1U]{};
  char* tokens[7U]{};
  std::size_t token_count = 0U;
  const ParseResult envelope = tokenize(frame, payload, sizeof(payload), tokens,
                                        7U, &token_count);
  if (envelope != ParseResult::kOk) {
    return envelope;
  }
  if (token_count != 7U) {
    return ParseResult::kWrongFieldCount;
  }
  if (std::strcmp(tokens[0], "VIS") != 0) {
    return ParseResult::kWrongMessageType;
  }

  VisionFrame parsed{};
  std::uint32_t person = 0U;
  std::uint32_t zone = 0U;
  if (!parse_unsigned(tokens[1], 65535U, &parsed.sequence) ||
      !parse_unsigned(tokens[2], 1U, &person) ||
      !parse_unsigned(tokens[3], 100U, &parsed.score) ||
      !parse_unsigned(tokens[4], 4095U, &parsed.center_x) ||
      !parse_unsigned(tokens[5], 4095U, &parsed.center_y) ||
      !parse_unsigned(tokens[6], 1U, &zone)) {
    return ParseResult::kInvalidNumber;
  }
  parsed.person_detected = person != 0U;
  parsed.in_zone = zone != 0U;
  if (!parsed.person_detected &&
      (parsed.score != 0U || parsed.center_x != 0U ||
       parsed.center_y != 0U || parsed.in_zone)) {
    return ParseResult::kOutOfRange;
  }
  *output = parsed;
  return ParseResult::kOk;
}

ParseResult parse_network(const char* frame, NetworkFrame* output) {
  if (output == nullptr) {
    return ParseResult::kNullArgument;
  }
  char payload[kMaxFrameBytes + 1U]{};
  char* tokens[5U]{};
  std::size_t token_count = 0U;
  const ParseResult envelope = tokenize(frame, payload, sizeof(payload), tokens,
                                        5U, &token_count);
  if (envelope != ParseResult::kOk) {
    return envelope;
  }
  if (token_count != 5U) {
    return ParseResult::kWrongFieldCount;
  }
  if (std::strcmp(tokens[0], "NET") != 0) {
    return ParseResult::kWrongMessageType;
  }

  NetworkFrame parsed{};
  std::uint32_t wifi = 0U;
  std::uint32_t server = 0U;
  if (!parse_unsigned(tokens[1], 1U, &wifi) ||
      !parse_unsigned(tokens[2], 1U, &server) ||
      !parse_signed(tokens[3], -127, 0, &parsed.rssi) ||
      !parse_unsigned(tokens[4], UINT32_MAX, &parsed.unix_time)) {
    return ParseResult::kInvalidNumber;
  }
  parsed.wifi_connected = wifi != 0U;
  parsed.server_reachable = server != 0U;
  if (parsed.server_reachable && !parsed.wifi_connected) {
    return ParseResult::kOutOfRange;
  }
  *output = parsed;
  return ParseResult::kOk;
}

bool build_telemetry(char* output, std::size_t output_size,
                     std::uint32_t sequence, std::uint32_t uptime_ms,
                     const TelemetrySnapshot& telemetry) {
  if (output == nullptr || output_size == 0U) {
    return false;
  }
  char payload[144U]{};
  const int payload_length = std::snprintf(
      payload, sizeof(payload),
      "TEL,%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRId32 ",%" PRId32
      ",%u,%u,%" PRIu32,
      sequence, uptime_ms, telemetry.distance_mm, telemetry.water_rise_mm,
      telemetry.rise_rate_mm_s, telemetry.person_detected ? 1U : 0U,
      static_cast<unsigned int>(telemetry.alarm), telemetry.health_flags);
  if (payload_length <= 0 ||
      static_cast<std::size_t>(payload_length) >= sizeof(payload)) {
    return false;
  }
  const std::uint8_t checksum =
      xor_checksum(payload, static_cast<std::size_t>(payload_length));
  const int output_length = std::snprintf(
      output, output_size, "$%s*%02X\n", payload,
      static_cast<unsigned int>(checksum));
  return output_length > 0 &&
         static_cast<std::size_t>(output_length) < output_size;
}

FeedResult feed(LineAccumulator* accumulator, char byte, char* output,
                std::size_t output_size) {
  if (accumulator == nullptr || output == nullptr || output_size == 0U) {
    return FeedResult::kDropped;
  }
  if (byte == '$') {
    accumulator->length = 0U;
    accumulator->collecting = true;
    accumulator->bytes[accumulator->length++] = byte;
    return FeedResult::kNone;
  }
  if (!accumulator->collecting) {
    return FeedResult::kNone;
  }
  if (byte == '\n') {
    std::size_t length = accumulator->length;
    if (length > 0U && accumulator->bytes[length - 1U] == '\r') {
      --length;
    }
    accumulator->collecting = false;
    accumulator->length = 0U;
    if (length == 0U || length + 1U > output_size) {
      return FeedResult::kDropped;
    }
    std::memcpy(output, accumulator->bytes, length);
    output[length] = '\0';
    return FeedResult::kFrameReady;
  }
  if (accumulator->length >= kMaxFrameBytes) {
    accumulator->length = 0U;
    accumulator->collecting = false;
    return FeedResult::kDropped;
  }
  accumulator->bytes[accumulator->length++] = byte;
  return FeedResult::kNone;
}

}  // namespace coastwatch::protocol

