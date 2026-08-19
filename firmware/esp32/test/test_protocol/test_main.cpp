#include <unity.h>

#include <stdio.h>
#include <string.h>

#include "protocol.h"
#include "ring_buffer.h"

void setUp() {}
void tearDown() {}

void test_valid_tel_vector() {
  TelemetryFrame telemetry{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(TelParseResult::kOk),
      static_cast<int>(parseTelFrame(
          "$TEL,42,123456,815,126,21,1,3,7*63", &telemetry)));
  TEST_ASSERT_EQUAL_UINT32(42U, telemetry.seq);
  TEST_ASSERT_EQUAL_UINT32(123456U, telemetry.uptime_ms);
  TEST_ASSERT_EQUAL_UINT32(815U, telemetry.distance_mm);
  TEST_ASSERT_EQUAL_INT32(126, telemetry.water_rise_mm);
  TEST_ASSERT_EQUAL_INT32(21, telemetry.rise_rate_mm_s);
  TEST_ASSERT_TRUE(telemetry.person_detected);
  TEST_ASSERT_EQUAL_UINT8(3U, telemetry.alarm_level);
  TEST_ASSERT_EQUAL_UINT32(7U, telemetry.health_flags);
}

void test_signed_water_values() {
  const char *payload = "TEL,8,900,0,-12,-5,0,0,0";
  char frame[96]{};
  snprintf(frame, sizeof(frame), "$%s*%02X", payload,
           static_cast<unsigned int>(protocolXor(payload, strlen(payload))));

  TelemetryFrame telemetry{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(TelParseResult::kOk),
      static_cast<int>(parseTelFrame(frame, &telemetry)));
  TEST_ASSERT_EQUAL_INT32(-12, telemetry.water_rise_mm);
  TEST_ASSERT_EQUAL_INT32(-5, telemetry.rise_rate_mm_s);
}

void test_bad_checksum_is_rejected_without_overwrite() {
  TelemetryFrame telemetry{99U, 88U, 77U, 66, 55, true, 4U, 44U};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(TelParseResult::kBadChecksum),
      static_cast<int>(parseTelFrame(
          "$TEL,42,123456,815,126,21,1,3,7*00", &telemetry)));
  TEST_ASSERT_EQUAL_UINT32(99U, telemetry.seq);
  TEST_ASSERT_EQUAL_UINT8(4U, telemetry.alarm_level);
}

void test_invalid_person_and_alarm_are_rejected() {
  const char *payload = "TEL,1,2,3,4,5,2,9,7";
  char frame[96]{};
  snprintf(frame, sizeof(frame), "$%s*%02X", payload,
           static_cast<unsigned int>(protocolXor(payload, strlen(payload))));
  TelemetryFrame telemetry{};
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(TelParseResult::kInvalidNumber),
      static_cast<int>(parseTelFrame(frame, &telemetry)));
}

void test_openmv_vis_frame_is_strictly_parsed() {
  const char *payload = "VIS,17,1,90,123,77,1";
  char frame[96]{};
  snprintf(frame, sizeof(frame), "$%s*%02X", payload,
           static_cast<unsigned int>(protocolXor(payload, strlen(payload))));
  VisionFrame vision{};
  TEST_ASSERT_EQUAL_INT(static_cast<int>(TelParseResult::kOk),
                        static_cast<int>(parseVisionFrame(frame, &vision)));
  TEST_ASSERT_EQUAL_UINT32(17U, vision.seq);
  TEST_ASSERT_TRUE(vision.person_detected);
  TEST_ASSERT_EQUAL_UINT8(90U, vision.score);
  TEST_ASSERT_EQUAL_UINT16(123U, vision.center_x);
  TEST_ASSERT_EQUAL_UINT16(77U, vision.center_y);
  TEST_ASSERT_TRUE(vision.in_zone);

  const char *invalid_payload = "VIS,18,0,1,0,0,0";
  snprintf(frame, sizeof(frame), "$%s*%02X", invalid_payload,
           static_cast<unsigned int>(
               protocolXor(invalid_payload, strlen(invalid_payload))));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(TelParseResult::kOutOfRange),
      static_cast<int>(parseVisionFrame(frame, &vision)));
}

void test_net_vector() {
  char frame[96]{};
  TEST_ASSERT_TRUE(buildNetFrame(frame, sizeof(frame), true, true, -55,
                                1785398400U));
  TEST_ASSERT_EQUAL_STRING("$NET,1,1,-55,1785398400*7F\n", frame);
}

void test_openmv_control_vector_and_safety_relationships() {
  char frame[64]{};
  TEST_ASSERT_TRUE(
      buildOpenMvControlFrame(frame, sizeof(frame), 42U, true, true, 3U));
  TEST_ASSERT_EQUAL_STRING("$CTL,42,1,1,3*6E\r\n", frame);

  TEST_ASSERT_TRUE(
      buildOpenMvControlFrame(frame, sizeof(frame), 17U, true, true, 2U));
  TEST_ASSERT_EQUAL_STRING("$CTL,17,1,1,2*6F\r\n", frame);
  TEST_ASSERT_TRUE(
      buildOpenMvControlFrame(frame, sizeof(frame), 18U, false, false, 0U));
  TEST_ASSERT_EQUAL_STRING("$CTL,18,0,0,0*62\r\n", frame);
  TEST_ASSERT_TRUE(
      buildOpenMvControlFrame(frame, sizeof(frame), 19U, false, true, 2U));
  TEST_ASSERT_EQUAL_STRING("$CTL,19,0,1,2*60\r\n", frame);
  // A healthy live local warning can assert danger while the model diagnostic
  // level remains safe.
  TEST_ASSERT_TRUE(
      buildOpenMvControlFrame(frame, sizeof(frame), 20U, true, true, 0U));
  TEST_ASSERT_EQUAL_STRING("$CTL,20,1,1,0*69\r\n", frame);

  // Fail-safe monitoring is intentionally legal without model danger.
  TEST_ASSERT_TRUE(
      buildOpenMvControlFrame(frame, sizeof(frame), 43U, false, true, 0U));
  // Any danger and any warning/critical diagnostic level require monitoring.
  TEST_ASSERT_FALSE(
      buildOpenMvControlFrame(frame, sizeof(frame), 44U, true, false, 3U));
  TEST_ASSERT_TRUE(
      buildOpenMvControlFrame(frame, sizeof(frame), 45U, true, true, 1U));
  TEST_ASSERT_FALSE(
      buildOpenMvControlFrame(frame, sizeof(frame), 46U, false, false, 4U));
  TEST_ASSERT_FALSE(
      buildOpenMvControlFrame(frame, sizeof(frame), 47U, false, false, 2U));
}

void test_ring_buffer_preserves_order_and_reports_full() {
  ByteRingBuffer<3> ring;
  TEST_ASSERT_TRUE(ring.push('A'));
  TEST_ASSERT_TRUE(ring.push('B'));
  TEST_ASSERT_TRUE(ring.push('C'));
  TEST_ASSERT_FALSE(ring.push('D'));
  uint8_t value = 0U;
  TEST_ASSERT_TRUE(ring.pop(&value));
  TEST_ASSERT_EQUAL_UINT8('A', value);
  TEST_ASSERT_TRUE(ring.pop(&value));
  TEST_ASSERT_EQUAL_UINT8('B', value);
  TEST_ASSERT_TRUE(ring.pop(&value));
  TEST_ASSERT_EQUAL_UINT8('C', value);
  TEST_ASSERT_FALSE(ring.pop(&value));
}

void test_line_reader_drops_oversize_line() {
  LineReader<4> reader;
  const char *line = nullptr;
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LineEvent::kNone),
                        static_cast<int>(reader.push('1', &line)));
  reader.push('2', &line);
  reader.push('3', &line);
  reader.push('4', &line);
  reader.push('5', &line);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LineEvent::kDroppedOversize),
                        static_cast<int>(reader.push('\n', &line)));
  reader.push('O', &line);
  reader.push('K', &line);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LineEvent::kReady),
                        static_cast<int>(reader.push('\n', &line)));
  TEST_ASSERT_EQUAL_STRING("OK", line);
}

void test_ultrasonic_snapshot_requires_fresh_healthy_tel() {
  TelemetryFrame telemetry{};
  telemetry.distance_mm = 1995U;
  telemetry.water_rise_mm = -12;
  telemetry.alarm_level = 4U;
  telemetry.health_flags = kTelemetryHealthUltrasonicOk;

  TelemetrySnapshot snapshot =
      makeTelemetrySnapshot(telemetry, true, 1000U, 3500U, 2500U);
  TEST_ASSERT_TRUE(snapshot.telemetry_fresh);
  TEST_ASSERT_TRUE(snapshot.ultrasonic_available);
  TEST_ASSERT_EQUAL_UINT32(1995U, snapshot.latest.distance_mm);
  TEST_ASSERT_EQUAL_INT32(-12, snapshot.latest.water_rise_mm);

  snapshot = makeTelemetrySnapshot(telemetry, true, 1000U, 3501U, 2500U);
  TEST_ASSERT_FALSE(snapshot.telemetry_fresh);
  TEST_ASSERT_FALSE(snapshot.ultrasonic_available);

  telemetry.health_flags = 0U;
  snapshot = makeTelemetrySnapshot(telemetry, true, 1000U, 1100U, 2500U);
  TEST_ASSERT_TRUE(snapshot.telemetry_fresh);
  TEST_ASSERT_FALSE(snapshot.ultrasonic_available);

  telemetry.health_flags = kTelemetryHealthUltrasonicOk;
  snapshot = makeTelemetrySnapshot(telemetry, false, 1000U, 1100U, 2500U);
  TEST_ASSERT_FALSE(snapshot.telemetry_fresh);
  TEST_ASSERT_FALSE(snapshot.ultrasonic_available);

  snapshot.latest.distance_mm = 0U;
  snapshot = makeTelemetrySnapshot(snapshot.latest, true, 1000U, 1100U,
                                   2500U);
  TEST_ASSERT_TRUE(snapshot.telemetry_fresh);
  TEST_ASSERT_FALSE(snapshot.ultrasonic_available);

  snapshot.latest.distance_mm = 4001U;
  snapshot = makeTelemetrySnapshot(snapshot.latest, true, 1000U, 1100U,
                                   2500U);
  TEST_ASSERT_TRUE(snapshot.telemetry_fresh);
  TEST_ASSERT_FALSE(snapshot.ultrasonic_available);
}

void test_ultrasonic_snapshot_age_handles_millis_wraparound() {
  TelemetryFrame telemetry{};
  telemetry.distance_mm = kTelemetryUltrasonicMinimumMm;
  telemetry.health_flags = kTelemetryHealthUltrasonicOk;
  const TelemetrySnapshot snapshot = makeTelemetrySnapshot(
      telemetry, true, UINT32_MAX - 100U, 99U, 250U);
  TEST_ASSERT_TRUE(snapshot.telemetry_fresh);
  TEST_ASSERT_TRUE(snapshot.ultrasonic_available);
}

int runProtocolTests() {
  UNITY_BEGIN();
  RUN_TEST(test_valid_tel_vector);
  RUN_TEST(test_signed_water_values);
  RUN_TEST(test_bad_checksum_is_rejected_without_overwrite);
  RUN_TEST(test_invalid_person_and_alarm_are_rejected);
  RUN_TEST(test_openmv_vis_frame_is_strictly_parsed);
  RUN_TEST(test_net_vector);
  RUN_TEST(test_openmv_control_vector_and_safety_relationships);
  RUN_TEST(test_ring_buffer_preserves_order_and_reports_full);
  RUN_TEST(test_line_reader_drops_oversize_line);
  RUN_TEST(test_ultrasonic_snapshot_requires_fresh_healthy_tel);
  RUN_TEST(test_ultrasonic_snapshot_age_handles_millis_wraparound);
  return UNITY_END();
}

#if defined(ARDUINO)
void setup() { runProtocolTests(); }
void loop() {}
#else
int main(int, char **) { return runProtocolTests(); }
#endif
