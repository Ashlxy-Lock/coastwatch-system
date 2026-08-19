#include <unity.h>

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "wifi_profile_store.h"

namespace {

constexpr uint32_t kV2Magic = 0x32465743U;
constexpr uint32_t kLegacyMagic = 0x434F4153U;

struct TestEncodedProfile {
  char ssid[kWifiSsidBytes];
  char password[kWifiPasswordBytes];
  uint8_t secured;
  uint8_t reserved;
};

struct TestEnvelopeV2 {
  uint32_t magic;
  uint16_t version;
  uint16_t size;
  uint8_t count;
  uint8_t reserved[3];
  TestEncodedProfile profiles[kWifiProfileStoreCapacity];
  uint32_t checksum;
};

struct TestLegacyV1 {
  uint32_t magic;
  uint16_t version;
  uint16_t size;
  char ssid[kWifiSsidBytes];
  char password[kWifiPasswordBytes];
  uint32_t checksum;
};

static_assert(sizeof(TestEnvelopeV2) == WifiProfileStore::kEncodedSize,
              "test V2 layout mismatch");
static_assert(sizeof(TestLegacyV1) == 112U, "test V1 layout mismatch");

uint32_t checksum(const void *memory, size_t length) {
  const uint8_t *bytes = static_cast<const uint8_t *>(memory);
  uint32_t hash = 2166136261U;
  for (size_t index = 0U; index < length; ++index) {
    hash ^= bytes[index];
    hash *= 16777619U;
  }
  return hash;
}

void assertProfile(const WifiProfileStore &store, size_t index,
                   const char *ssid, const char *password, bool secured) {
  WifiCredentials credentials{};
  TEST_ASSERT_TRUE(store.copyAt(index, &credentials));
  TEST_ASSERT_EQUAL_STRING(ssid, credentials.ssid);
  TEST_ASSERT_EQUAL_STRING(password, credentials.password);
  TEST_ASSERT_EQUAL(secured, credentials.secured);
  WifiProfileStore::clearCredentials(&credentials);
}

void fillProfiles(WifiProfileStore *store, size_t count) {
  char ssid[20]{};
  char password[20]{};
  for (size_t index = 0U; index < count; ++index) {
    snprintf(ssid, sizeof(ssid), "coast-%02u", static_cast<unsigned>(index));
    snprintf(password, sizeof(password), "password-%02u",
             static_cast<unsigned>(index));
    TEST_ASSERT_EQUAL_INT(
        static_cast<int>(WifiProfileMutation::kInserted),
        static_cast<int>(store->recordSuccessful(ssid, password, true)));
  }
}

}  // namespace

void setUp() {}
void tearDown() {}

void test_empty_v2_round_trip_is_distinct_from_corruption() {
  WifiProfileStore source;
  uint8_t blob[WifiProfileStore::kEncodedSize]{};
  TEST_ASSERT_TRUE(source.encode(blob, sizeof(blob)));

  WifiProfileStore decoded;
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileDecodeResult::kEmpty),
      static_cast<int>(WifiProfileStore::decode(blob, sizeof(blob), &decoded)));
  TEST_ASSERT_TRUE(decoded.empty());

  blob[40] ^= 0x01U;
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileDecodeResult::kCorrupt),
      static_cast<int>(WifiProfileStore::decode(blob, sizeof(blob), &decoded)));
}

void test_record_deduplicates_updates_and_orders_by_recent_success() {
  WifiProfileStore store;
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kInserted),
      static_cast<int>(store.recordSuccessful("first", "password1", true)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kInserted),
      static_cast<int>(store.recordSuccessful("second", "password2", true)));
  TEST_ASSERT_EQUAL_UINT32(2U, store.count());
  assertProfile(store, 0U, "second", "password2", true);
  assertProfile(store, 1U, "first", "password1", true);

  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kUpdated),
      static_cast<int>(
          store.recordSuccessful("first", "new-password", true)));
  TEST_ASSERT_EQUAL_UINT32(2U, store.count());
  assertProfile(store, 0U, "first", "new-password", true);
  assertProfile(store, 1U, "second", "password2", true);

  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kUnchanged),
      static_cast<int>(
          store.recordSuccessful("first", "new-password", true)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kReordered),
      static_cast<int>(store.markSuccessful("second")));
  assertProfile(store, 0U, "second", "password2", true);
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kNotFound),
      static_cast<int>(store.markSuccessful("missing")));
}

void test_full_store_evicts_only_the_least_recent_profile() {
  WifiProfileStore store;
  fillProfiles(&store, kWifiProfileStoreCapacity);
  TEST_ASSERT_EQUAL_UINT32(kWifiProfileStoreCapacity, store.count());
  TEST_ASSERT_TRUE(store.contains("coast-00"));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kEvictedAndInserted),
      static_cast<int>(
          store.recordSuccessful("coast-16", "password-16", true)));
  TEST_ASSERT_EQUAL_UINT32(kWifiProfileStoreCapacity, store.count());
  TEST_ASSERT_FALSE(store.contains("coast-00"));
  TEST_ASSERT_TRUE(store.contains("coast-01"));
  assertProfile(store, 0U, "coast-16", "password-16", true);
}

void test_same_ssid_open_and_secured_transitions_replace_old_credentials() {
  WifiProfileStore store;
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kInserted),
      static_cast<int>(
          store.recordSuccessful("changing-network", "password1", true)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kUpdated),
      static_cast<int>(
          store.recordSuccessful("changing-network", "", false)));
  TEST_ASSERT_EQUAL_UINT32(1U, store.count());
  assertProfile(store, 0U, "changing-network", "", false);

  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kUpdated),
      static_cast<int>(
          store.recordSuccessful("changing-network", "password2", true)));
  TEST_ASSERT_EQUAL_UINT32(1U, store.count());
  assertProfile(store, 0U, "changing-network", "password2", true);
}

void test_forget_compacts_and_secure_clear_zeros_outputs() {
  WifiProfileStore store;
  store.recordSuccessful("one", "password1", true);
  store.recordSuccessful("two", "password2", true);
  store.recordSuccessful("three", "password3", true);
  TEST_ASSERT_TRUE(store.forget("two"));
  TEST_ASSERT_FALSE(store.forget("two"));
  TEST_ASSERT_EQUAL_UINT32(2U, store.count());
  assertProfile(store, 0U, "three", "password3", true);
  assertProfile(store, 1U, "one", "password1", true);

  WifiCredentials copied{};
  TEST_ASSERT_TRUE(store.copyForSsid("one", &copied));
  WifiProfileStore::clearCredentials(&copied);
  const uint8_t zeros[sizeof(copied)]{};
  TEST_ASSERT_EQUAL_UINT8_ARRAY(zeros, &copied, sizeof(copied));
  store.clearSecrets();
  TEST_ASSERT_TRUE(store.empty());
}

void test_explicit_clone_and_swap_support_transactional_updates() {
  WifiProfileStore live;
  live.recordSuccessful("live", "live-pass", true);
  WifiProfileStore candidate;
  TEST_ASSERT_TRUE(live.cloneTo(&candidate));
  TEST_ASSERT_FALSE(live.cloneTo(nullptr));
  TEST_ASSERT_TRUE(live.cloneTo(&live));
  candidate.recordSuccessful("candidate", "candidate-pass", true);

  TEST_ASSERT_EQUAL_UINT32(1U, live.count());
  TEST_ASSERT_TRUE(live.contains("live"));
  TEST_ASSERT_FALSE(live.contains("candidate"));
  TEST_ASSERT_EQUAL_UINT32(2U, candidate.count());

  live.swap(&candidate);
  TEST_ASSERT_EQUAL_UINT32(2U, live.count());
  TEST_ASSERT_TRUE(live.contains("candidate"));
  TEST_ASSERT_EQUAL_UINT32(1U, candidate.count());
  TEST_ASSERT_TRUE(candidate.contains("live"));

  live.swap(nullptr);
  live.swap(&live);
  TEST_ASSERT_EQUAL_UINT32(2U, live.count());
}

void test_invalid_inputs_do_not_modify_store() {
  WifiProfileStore store;
  store.recordSuccessful("valid", "password", true);
  char unterminated_ssid[kWifiSsidBytes];
  memset(unterminated_ssid, 'S', sizeof(unterminated_ssid));
  char too_long_password[kWifiPasswordBytes];
  memset(too_long_password, 'P', sizeof(too_long_password));
  too_long_password[64] = '\0';

  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kInvalidInput),
      static_cast<int>(store.recordSuccessful("", "password", true)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kInvalidInput),
      static_cast<int>(
          store.recordSuccessful(unterminated_ssid, "password", true)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kInvalidInput),
      static_cast<int>(
          store.recordSuccessful("long", too_long_password, true)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kInvalidInput),
      static_cast<int>(store.recordSuccessful("null", nullptr, true)));
  TEST_ASSERT_EQUAL_UINT32(1U, store.count());

  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kInserted),
      static_cast<int>(store.recordSuccessful("open-network", "", false)));

  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kInvalidInput),
      static_cast<int>(store.recordSuccessful("bad-open", "password", false)));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kInvalidInput),
      static_cast<int>(store.recordSuccessful("bad-secure", "", true)));
}

void test_v2_round_trip_preserves_order_and_secrets() {
  WifiProfileStore source;
  source.recordSuccessful("one", "password1", true);
  source.recordSuccessful("open", "", false);
  source.recordSuccessful("three", "password3", true);
  uint8_t blob[WifiProfileStore::kEncodedSize]{};
  TEST_ASSERT_TRUE(source.encode(blob, sizeof(blob)));
  TEST_ASSERT_FALSE(source.encode(nullptr, sizeof(blob)));
  TEST_ASSERT_FALSE(source.encode(blob, sizeof(blob) - 1U));

  WifiProfileStore decoded;
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileDecodeResult::kLoaded),
      static_cast<int>(WifiProfileStore::decode(blob, sizeof(blob), &decoded)));
  TEST_ASSERT_EQUAL_UINT32(3U, decoded.count());
  assertProfile(decoded, 0U, "three", "password3", true);
  assertProfile(decoded, 1U, "open", "", false);
  assertProfile(decoded, 2U, "one", "password1", true);
}

void test_exact_64_hex_raw_psk_is_supported_but_other_64_bytes_are_not() {
  constexpr char kRawPsk[] =
      "0123456789abcdef0123456789ABCDEF0123456789abcdef0123456789ABCDEF";
  constexpr char kNotHex[] =
      "g123456789abcdef0123456789ABCDEF0123456789abcdef0123456789ABCDEF";
  static_assert(sizeof(kRawPsk) == kWifiPasswordBytes,
                "raw PSK fixture must be exactly 64 characters");
  static_assert(sizeof(kNotHex) == kWifiPasswordBytes,
                "invalid PSK fixture must be exactly 64 characters");

  TEST_ASSERT_TRUE(wifiSecuredPasswordValid(kRawPsk));
  TEST_ASSERT_FALSE(wifiSecuredPasswordValid(kNotHex));

  WifiProfileStore source;
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kInserted),
      static_cast<int>(source.recordSuccessful("raw-psk", kRawPsk, true)));
  uint8_t blob[WifiProfileStore::kEncodedSize]{};
  TEST_ASSERT_TRUE(source.encode(blob, sizeof(blob)));
  WifiProfileStore decoded;
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileDecodeResult::kLoaded),
      static_cast<int>(WifiProfileStore::decode(blob, sizeof(blob), &decoded)));
  assertProfile(decoded, 0U, "raw-psk", kRawPsk, true);

  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileMutation::kInvalidInput),
      static_cast<int>(source.recordSuccessful("bad-raw-psk", kNotHex, true)));
}

void test_semantically_invalid_even_with_recomputed_checksum_is_rejected() {
  WifiProfileStore source;
  source.recordSuccessful("one", "password1", true);
  source.recordSuccessful("two", "password2", true);
  TestEnvelopeV2 envelope{};
  TEST_ASSERT_TRUE(source.encode(&envelope, sizeof(envelope)));
  TEST_ASSERT_EQUAL_HEX32(kV2Magic, envelope.magic);

  memcpy(envelope.profiles[1].ssid, envelope.profiles[0].ssid,
         sizeof(envelope.profiles[1].ssid));
  envelope.checksum = checksum(&envelope, offsetof(TestEnvelopeV2, checksum));

  WifiProfileStore unchanged;
  unchanged.recordSuccessful("keep", "keep-pass", true);
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileDecodeResult::kCorrupt),
      static_cast<int>(
          WifiProfileStore::decode(&envelope, sizeof(envelope), &unchanged)));
  TEST_ASSERT_TRUE(unchanged.contains("keep"));
  TEST_ASSERT_EQUAL_UINT32(1U, unchanged.count());

  memset(&envelope, 0, sizeof(envelope));
  TEST_ASSERT_TRUE(source.encode(&envelope, sizeof(envelope)));
  envelope.count = static_cast<uint8_t>(kWifiProfileStoreCapacity + 1U);
  envelope.checksum = checksum(&envelope, offsetof(TestEnvelopeV2, checksum));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileDecodeResult::kCorrupt),
      static_cast<int>(
          WifiProfileStore::decode(&envelope, sizeof(envelope), &unchanged)));

  memset(&envelope, 0, sizeof(envelope));
  TEST_ASSERT_TRUE(source.encode(&envelope, sizeof(envelope)));
  envelope.profiles[0].secured = 0U;
  envelope.checksum = checksum(&envelope, offsetof(TestEnvelopeV2, checksum));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileDecodeResult::kCorrupt),
      static_cast<int>(
          WifiProfileStore::decode(&envelope, sizeof(envelope), &unchanged)));
}

void test_unsupported_v2_version_is_not_reported_as_corruption() {
  WifiProfileStore store;
  TestEnvelopeV2 envelope{};
  TEST_ASSERT_TRUE(store.encode(&envelope, sizeof(envelope)));
  envelope.version = 99U;
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileDecodeResult::kUnsupportedVersion),
      static_cast<int>(WifiProfileStore::decode(&envelope, sizeof(envelope),
                                                &store)));
}

void test_exact_legacy_v1_profile_and_tombstone_migrate() {
  TestLegacyV1 legacy{};
  legacy.magic = kLegacyMagic;
  legacy.version = 1U;
  legacy.size = sizeof(legacy);
  strcpy(legacy.ssid, "legacy-coast");
  strcpy(legacy.password, "legacy-password");
  legacy.checksum = checksum(&legacy, offsetof(TestLegacyV1, checksum));

  WifiProfileStore migrated;
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileDecodeResult::kMigratedLegacyV1),
      static_cast<int>(
          WifiProfileStore::decode(&legacy, sizeof(legacy), &migrated)));
  TEST_ASSERT_EQUAL_UINT32(1U, migrated.count());
  assertProfile(migrated, 0U, "legacy-coast", "legacy-password", true);

  memset(&legacy, 0, sizeof(legacy));
  legacy.magic = kLegacyMagic;
  legacy.version = 1U;
  legacy.size = sizeof(legacy);
  strcpy(legacy.ssid, "legacy-open");
  legacy.checksum = checksum(&legacy, offsetof(TestLegacyV1, checksum));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileDecodeResult::kMigratedLegacyV1),
      static_cast<int>(
          WifiProfileStore::decode(&legacy, sizeof(legacy), &migrated)));
  TEST_ASSERT_EQUAL_UINT32(1U, migrated.count());
  assertProfile(migrated, 0U, "legacy-open", "", false);

  memset(&legacy, 0, sizeof(legacy));
  legacy.magic = kLegacyMagic;
  legacy.version = 1U;
  legacy.size = sizeof(legacy);
  strcpy(legacy.ssid, "legacy-open");
  legacy.checksum = checksum(&legacy, offsetof(TestLegacyV1, checksum));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileDecodeResult::kMigratedLegacyV1),
      static_cast<int>(
          WifiProfileStore::decode(&legacy, sizeof(legacy), &migrated)));
  assertProfile(migrated, 0U, "legacy-open", "", false);

  memset(&legacy, 0, sizeof(legacy));
  legacy.magic = kLegacyMagic;
  legacy.version = 1U;
  legacy.size = sizeof(legacy);
  legacy.checksum = checksum(&legacy, offsetof(TestLegacyV1, checksum));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileDecodeResult::kMigratedLegacyV1Empty),
      static_cast<int>(
          WifiProfileStore::decode(&legacy, sizeof(legacy), &migrated)));
  TEST_ASSERT_TRUE(migrated.empty());

  memset(&legacy, 0, sizeof(legacy));
  legacy.magic = kLegacyMagic;
  legacy.version = 1U;
  legacy.size = sizeof(legacy);
  strcpy(legacy.ssid, "legacy-raw-psk");
  strcpy(legacy.password,
         "0123456789abcdef0123456789ABCDEF0123456789abcdef0123456789ABCDEF");
  legacy.checksum = checksum(&legacy, offsetof(TestLegacyV1, checksum));
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileDecodeResult::kMigratedLegacyV1),
      static_cast<int>(
          WifiProfileStore::decode(&legacy, sizeof(legacy), &migrated)));
  TEST_ASSERT_EQUAL_UINT32(1U, migrated.count());

  legacy.checksum ^= 0x1U;
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(WifiProfileDecodeResult::kCorrupt),
      static_cast<int>(
          WifiProfileStore::decode(&legacy, sizeof(legacy), &migrated)));
}

int runWifiProfileStoreTests() {
  UNITY_BEGIN();
  RUN_TEST(test_empty_v2_round_trip_is_distinct_from_corruption);
  RUN_TEST(test_record_deduplicates_updates_and_orders_by_recent_success);
  RUN_TEST(test_full_store_evicts_only_the_least_recent_profile);
  RUN_TEST(
      test_same_ssid_open_and_secured_transitions_replace_old_credentials);
  RUN_TEST(test_forget_compacts_and_secure_clear_zeros_outputs);
  RUN_TEST(test_explicit_clone_and_swap_support_transactional_updates);
  RUN_TEST(test_invalid_inputs_do_not_modify_store);
  RUN_TEST(test_v2_round_trip_preserves_order_and_secrets);
  RUN_TEST(
      test_exact_64_hex_raw_psk_is_supported_but_other_64_bytes_are_not);
  RUN_TEST(test_semantically_invalid_even_with_recomputed_checksum_is_rejected);
  RUN_TEST(test_unsupported_v2_version_is_not_reported_as_corruption);
  RUN_TEST(test_exact_legacy_v1_profile_and_tombstone_migrate);
  return UNITY_END();
}

#if defined(ARDUINO)
void setup() { runWifiProfileStoreTests(); }
void loop() {}
#else
int main(int, char **) { return runWifiProfileStoreTests(); }
#endif
