#include "openmv_control.h"

#include <cstring>

#include "sensor_logic.h"

OpenMvControlDecision decideOpenMvControl(const RiskSnapshot &risk,
                                          bool risk_ready,
                                          uint8_t live_local_alarm_level,
                                          int32_t live_water_rise_mm,
                                          int32_t live_rise_rate_mm_s,
                                          bool live_ultrasonic_health_ok,
                                          bool live_openmv_health_ok,
                                          uint32_t now_ms,
                                          uint32_t maximum_age_ms) {
  OpenMvControlDecision decision{};
  decision.environmental_level =
      risk.environmental_level <= 3U ? risk.environmental_level : 0U;

  const bool has_result = risk.fetched_at_ms != 0U;
  const bool fresh =
      has_result &&
      static_cast<uint32_t>(now_ms - risk.fetched_at_ms) <= maximum_age_ms;
  const bool model_source = std::strcmp(risk.model_source, "model") == 0;
  const bool valid_level = risk.environmental_level <= 3U;

  decision.trusted_model_result =
      risk_ready && !risk.stale && fresh && model_source && valid_level;

  // A green/safe indication needs substantially stronger evidence than merely
  // receiving a model class of zero. Any local alarm, degraded result, or
  // non-ok data quality keeps OpenMV in full-rate monitoring and prevents its
  // green LED from being authorized.
  decision.green_safe =
      decision.trusted_model_result && decision.environmental_level == 0U &&
      std::strcmp(risk.data_quality, "ok") == 0 && !risk.degraded &&
      live_ultrasonic_health_ok && live_openmv_health_ok &&
      live_local_alarm_level == 0U;
  decision.fail_safe =
      !decision.trusted_model_result ||
      std::strcmp(risk.data_quality, "ok") != 0 || risk.degraded ||
      !live_ultrasonic_health_ok || !live_openmv_health_ok ||
      live_local_alarm_level == 4U || live_local_alarm_level > 4U;

  decision.model_danger =
      decision.trusted_model_result && decision.environmental_level >= 2U;
  // Never derive water hazard from the aggregate alarm: person_in_zone also
  // raises that alarm to warning and would create a person -> danger -> person
  // feedback loop. Only fresh ultrasonic water/rate evidence can assert the
  // local hazard. Advisory values and fault level 4 remain non-danger states.
  decision.local_water_danger =
      live_ultrasonic_health_ok && live_local_alarm_level < 4U &&
      (live_water_rise_mm >= sensor_logic::config::kWaterWarningMm ||
       live_rise_rate_mm_s >= sensor_logic::config::kRiseRateWarningMmS);
  decision.danger = decision.model_danger || decision.local_water_danger;

  // The combined server risk_level and delayed local_alarm_level echo are
  // deliberately ignored, so a remote response cannot relabel or suppress
  // the current device-local state.

  // Only a clean level-zero result may select baseline sampling and authorize
  // OpenMV's green indication. Advisory is deliberately monitored at full
  // rate. Unknown/fallback/quality/fault states also fail safe to full rate;
  // only model_danger or a healthy live local water/rate hazard can authorize
  // the red danger gate.
  decision.person_enable = !decision.green_safe;
  return decision;
}
