#pragma once

#include <cstddef>
#include <cstdint>

#include "coastwatch_logic.h"

namespace coastwatch::protocol {

constexpr std::size_t kMaxFrameBytes = 160U;

enum class ParseResult : std::uint8_t {
  kOk = 0U,
  kNullArgument,
  kBadEnvelope,
  kBadChecksum,
  kWrongMessageType,
  kWrongFieldCount,
  kInvalidNumber,
  kOutOfRange,
};

struct VisionFrame {
  std::uint32_t sequence{};
  bool person_detected{};
  std::uint32_t score{};
  std::uint32_t center_x{};
  std::uint32_t center_y{};
  bool in_zone{};
};

struct NetworkFrame {
  bool wifi_connected{};
  bool server_reachable{};
  std::int32_t rssi{};
  std::uint32_t unix_time{};
};

enum class FeedResult : std::uint8_t {
  kNone = 0U,
  kFrameReady,
  kDropped,
};

struct LineAccumulator {
  char bytes[kMaxFrameBytes + 1U]{};
  std::size_t length{};
  bool collecting{};
};

std::uint8_t xor_checksum(const char* data, std::size_t length);
ParseResult parse_vision(const char* frame, VisionFrame* output);
ParseResult parse_network(const char* frame, NetworkFrame* output);
bool build_telemetry(char* output, std::size_t output_size,
                     std::uint32_t sequence, std::uint32_t uptime_ms,
                     const TelemetrySnapshot& telemetry);
FeedResult feed(LineAccumulator* accumulator, char byte, char* output,
                std::size_t output_size);

}  // namespace coastwatch::protocol

