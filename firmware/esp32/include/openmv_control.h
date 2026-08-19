#pragma once

#include <stdint.h>

#include "risk_snapshot.h"

// A trusted server-model danger or healthy live ESP32 water/rise evidence may
// request full-rate person detection. The aggregate local alarm is not a water
// hazard input because person_in_zone can itself raise that alarm. The model
// never owns or replaces the deterministic local alarm.
struct OpenMvControlDecision {
  bool trusted_model_result;
  bool fail_safe;
  bool green_safe;
  bool model_danger;
  bool local_water_danger;
  bool danger;
  bool person_enable;
  uint8_t environmental_level;
};

// risk_ready is true only for RiskAvailability::kReady. A model result is
// trusted for camera mode control only while it is fresh, explicitly sourced
// from "model", and carries a valid environmental_level. Green authorization
// additionally requires the current ESP32 alarm and sensor health supplied by
// the caller; RiskSnapshot::local_alarm_level is only a delayed server echo.
// The server's delayed local_alarm_level echo is never consulted.
OpenMvControlDecision decideOpenMvControl(const RiskSnapshot &risk,
                                          bool risk_ready,
                                          uint8_t live_local_alarm_level,
                                          int32_t live_water_rise_mm,
                                          int32_t live_rise_rate_mm_s,
                                          bool live_ultrasonic_health_ok,
                                          bool live_openmv_health_ok,
                                          uint32_t now_ms,
                                          uint32_t maximum_age_ms);
