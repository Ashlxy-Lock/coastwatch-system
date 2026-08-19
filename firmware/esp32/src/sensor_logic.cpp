#include "sensor_logic.h"

#include <limits.h>

namespace sensor_logic {
namespace {

uint32_t elapsed(uint32_t now_ms, uint32_t then_ms) {
  return now_ms - then_ms;
}

uint32_t median(const uint32_t *values, size_t count) {
  uint32_t sorted[config::kMedianWindow]{};
  for (size_t index = 0U; index < count; ++index) {
    sorted[index] = values[index];
  }
  for (size_t index = 1U; index < count; ++index) {
    const uint32_t value = sorted[index];
    size_t position = index;
    while (position > 0U && sorted[position - 1U] > value) {
      sorted[position] = sorted[position - 1U];
      --position;
    }
    sorted[position] = value;
  }
  return sorted[count / 2U];
}

void clear_ultrasonic_reference(SensorState *state) {
  state->baseline_candidate_count = 0U;
  state->baseline_candidate_next = 0U;
  state->filter_count = 0U;
  state->filter_next = 0U;
  state->baseline_ready = false;
  state->baseline_distance_mm = 0U;
  state->filtered_ready = false;
  state->filtered_distance_q8 = 0;
  state->filtered_distance_mm = 0U;
  state->water_rise_mm = 0;
  state->rise_rate_mm_s = 0;
  state->previous_rise_mm = 0;
  state->previous_rise_ms = 0U;
  state->previous_rise_ready = false;
  state->ultrasonic_healthy = false;
}

void add_baseline_candidate(SensorState *state, uint32_t distance_mm) {
  state->baseline_candidates[state->baseline_candidate_next] = distance_mm;
  state->baseline_candidate_next =
      (state->baseline_candidate_next + 1U) % config::kBaselineWindow;
  if (state->baseline_candidate_count < config::kBaselineWindow) {
    ++state->baseline_candidate_count;
  }
}

bool baseline_candidates_are_stable(const SensorState &state) {
  if (state.baseline_candidate_count < config::kBaselineWindow) {
    return false;
  }
  uint32_t minimum = state.baseline_candidates[0];
  uint32_t maximum = state.baseline_candidates[0];
  for (size_t index = 1U; index < config::kBaselineWindow; ++index) {
    if (state.baseline_candidates[index] < minimum) {
      minimum = state.baseline_candidates[index];
    }
    if (state.baseline_candidates[index] > maximum) {
      maximum = state.baseline_candidates[index];
    }
  }
  return maximum - minimum <= config::kBaselineStableSpanMm;
}

void initialize_filter_from_baseline(SensorState *state, uint32_t now_ms) {
  state->baseline_distance_mm =
      median(state->baseline_candidates, config::kBaselineWindow);
  state->baseline_ready = true;
  state->filter_count = config::kBaselineWindow;
  state->filter_next = config::kBaselineWindow;
  for (size_t index = 0U; index < config::kBaselineWindow; ++index) {
    state->filter_samples[index] = state->baseline_candidates[index];
  }
  const uint32_t initial_distance =
      median(state->filter_samples, state->filter_count);
  state->filtered_distance_q8 = static_cast<int64_t>(initial_distance) << 8U;
  state->filtered_distance_mm = initial_distance;
  state->filtered_ready = true;
  state->water_rise_mm = 0;
  state->rise_rate_mm_s = 0;
  state->previous_rise_mm = 0;
  state->previous_rise_ms = now_ms;
  state->previous_rise_ready = true;
  state->ultrasonic_healthy = true;
}

void add_filter_sample(SensorState *state, uint32_t distance_mm) {
  state->filter_samples[state->filter_next] = distance_mm;
  state->filter_next =
      (state->filter_next + 1U) % config::kMedianWindow;
  if (state->filter_count < config::kMedianWindow) {
    ++state->filter_count;
  }
}

int32_t saturating_rate(int32_t delta_mm, uint32_t delta_ms) {
  if (delta_ms == 0U) {
    return 0;
  }
  const int64_t rate = static_cast<int64_t>(delta_mm) * 1000LL /
                       static_cast<int64_t>(delta_ms);
  if (rate > INT32_MAX) {
    return INT32_MAX;
  }
  if (rate < INT32_MIN) {
    return INT32_MIN;
  }
  return static_cast<int32_t>(rate);
}

}  // namespace

void reset(SensorState *state) {
  if (state != nullptr) {
    *state = SensorState{};
  }
}

bool accept_distance(SensorState *state, uint32_t now_ms,
                     uint32_t distance_mm) {
  if (state == nullptr) {
    return false;
  }
  if (distance_mm < config::kDistanceMinMm ||
      distance_mm > config::kDistanceMaxMm) {
    note_timeout(state, now_ms);
    return false;
  }

  state->ultrasonic_seen = true;
  state->last_ultrasonic_ms = now_ms;

  if (!state->baseline_ready) {
    add_baseline_candidate(state, distance_mm);
    if (!baseline_candidates_are_stable(*state)) {
      state->ultrasonic_healthy = false;
      return true;
    }
    initialize_filter_from_baseline(state, now_ms);
    return true;
  }

  add_filter_sample(state, distance_mm);
  const uint32_t median_distance =
      median(state->filter_samples, state->filter_count);
  // Integer Q8 implementation of 0.8 * previous + 0.2 * median.
  state->filtered_distance_q8 =
      (state->filtered_distance_q8 * 4LL +
       (static_cast<int64_t>(median_distance) << 8U) + 2LL) /
      5LL;
  state->filtered_distance_mm = static_cast<uint32_t>(
      (state->filtered_distance_q8 + 128LL) >> 8U);
  state->filtered_ready = true;

  const int64_t rise = static_cast<int64_t>(state->baseline_distance_mm) -
                       static_cast<int64_t>(state->filtered_distance_mm);
  if (rise > INT32_MAX) {
    state->water_rise_mm = INT32_MAX;
  } else if (rise < INT32_MIN) {
    state->water_rise_mm = INT32_MIN;
  } else {
    state->water_rise_mm = static_cast<int32_t>(rise);
  }

  if (state->previous_rise_ready) {
    state->rise_rate_mm_s = saturating_rate(
        state->water_rise_mm - state->previous_rise_mm,
        elapsed(now_ms, state->previous_rise_ms));
  } else {
    state->rise_rate_mm_s = 0;
  }
  state->previous_rise_mm = state->water_rise_mm;
  state->previous_rise_ms = now_ms;
  state->previous_rise_ready = true;
  state->ultrasonic_healthy = true;
  return true;
}

void note_timeout(SensorState *state, uint32_t now_ms) {
  if (state == nullptr) {
    return;
  }
  // Isolated missed echoes are acoustic noise. Freshness invalidates the
  // published value after one second; three seconds discards the reference.
  if (state->ultrasonic_seen &&
      elapsed(now_ms, state->last_ultrasonic_ms) >
          config::kUltrasonicBaselineResetMs) {
    clear_ultrasonic_reference(state);
  }
}

void note_hardware_fault(SensorState *state) {
  if (state == nullptr) {
    return;
  }
  clear_ultrasonic_reference(state);
  state->ultrasonic_seen = false;
  state->last_ultrasonic_ms = 0U;
}

void accept_vision(SensorState *state, uint32_t now_ms,
                   bool person_detected, bool person_in_zone) {
  if (state == nullptr) {
    return;
  }
  state->vision_seen = true;
  state->last_vision_ms = now_ms;
  state->person_detected = person_detected;
  state->person_in_zone = person_detected && person_in_zone;
}

void accept_network(SensorState *state, uint32_t now_ms,
                    bool wifi_connected, bool server_reachable) {
  if (state == nullptr) {
    return;
  }
  state->network_seen = true;
  state->last_network_ms = now_ms;
  state->wifi_connected = wifi_connected;
  state->server_reachable = wifi_connected && server_reachable;
}

void tick(SensorState *state, uint32_t now_ms) {
  if (state == nullptr || !state->ultrasonic_seen) {
    return;
  }
  const uint32_t age = elapsed(now_ms, state->last_ultrasonic_ms);
  if (age > config::kUltrasonicFreshMs) {
    state->ultrasonic_healthy = false;
  }
  if (age > config::kUltrasonicBaselineResetMs) {
    clear_ultrasonic_reference(state);
  }
}

TelemetryFrame snapshot(const SensorState &state, uint32_t now_ms,
                        uint32_t sequence) {
  TelemetryFrame result{};
  result.seq = sequence;
  result.uptime_ms = now_ms;

  const bool ultrasonic_ok =
      state.baseline_ready && state.filtered_ready &&
      state.ultrasonic_healthy && state.ultrasonic_seen &&
      elapsed(now_ms, state.last_ultrasonic_ms) <=
          config::kUltrasonicFreshMs;
  const bool vision_ok =
      state.vision_seen &&
      elapsed(now_ms, state.last_vision_ms) <= config::kVisionFreshMs;
  const bool network_ok =
      state.network_seen && state.wifi_connected && state.server_reachable &&
      elapsed(now_ms, state.last_network_ms) <= config::kNetworkFreshMs;

  if (ultrasonic_ok) {
    result.distance_mm = state.filtered_distance_mm;
    result.water_rise_mm = state.water_rise_mm;
    result.rise_rate_mm_s = state.rise_rate_mm_s;
    result.health_flags |= kHealthUltrasonicOk;
  }
  if (vision_ok) {
    result.person_detected = state.person_detected;
    result.health_flags |= kHealthOpenMvOk;
  }
  if (network_ok) {
    result.health_flags |= kHealthNetworkOk;
  }

  // No voltage monitor exists, so kHealthPowerOk deliberately remains clear.
  // Missing safety sensors fail closed; network availability never gates the
  // local rule alarm.
  if (!ultrasonic_ok || !vision_ok) {
    result.alarm_level = static_cast<uint8_t>(AlarmLevel::kFault);
    return result;
  }

  const bool water_danger =
      result.water_rise_mm >= config::kWaterDangerMm;
  const bool water_warning =
      result.water_rise_mm >= config::kWaterWarningMm;
  const bool water_advisory =
      result.water_rise_mm >= config::kWaterAdvisoryMm;
  const bool rising_fast =
      result.rise_rate_mm_s >= config::kRiseRateWarningMmS;

  AlarmLevel alarm = AlarmLevel::kSafe;
  if (state.person_in_zone && water_danger) {
    alarm = AlarmLevel::kCritical;
  } else if (state.person_in_zone || water_warning || rising_fast) {
    alarm = AlarmLevel::kWarning;
  } else if (result.person_detected || water_advisory) {
    alarm = AlarmLevel::kAdvisory;
  }
  result.alarm_level = static_cast<uint8_t>(alarm);
  return result;
}

}  // namespace sensor_logic
