#include "wifi_profile_store.h"

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace {

constexpr uint32_t kWifiProfilesMagic = 0x32465743U;  // "CWF2"
constexpr uint16_t kWifiProfilesVersion = 2U;

// Existing single-profile format in network_uplink.cpp. Its natural padding
// is part of the V1 checksum and therefore deliberately preserved here.
constexpr uint32_t kLegacyWifiProfileMagic = 0x434F4153U;  // "COAS"
constexpr uint16_t kLegacyWifiProfileVersion = 1U;

struct EncodedWifiProfile {
  char ssid[kWifiSsidBytes];
  char password[kWifiPasswordBytes];
  uint8_t secured;
  uint8_t reserved;
};

struct EncodedWifiProfilesV2 {
  uint32_t magic;
  uint16_t version;
  uint16_t size;
  uint8_t count;
  uint8_t reserved[3];
  EncodedWifiProfile profiles[kWifiProfileStoreCapacity];
  uint32_t checksum;
};

struct LegacyWifiProfileV1 {
  uint32_t magic;
  uint16_t version;
  uint16_t size;
  char ssid[kWifiSsidBytes];
  char password[kWifiPasswordBytes];
  uint32_t checksum;
};

static_assert(offsetof(EncodedWifiProfilesV2, checksum) == 1612U,
              "V2 Wi-Fi envelope padding changed");
static_assert(sizeof(EncodedWifiProfilesV2) ==
                  WifiProfileStore::kEncodedSize,
              "V2 Wi-Fi envelope size mismatch");
static_assert(offsetof(LegacyWifiProfileV1, checksum) == 108U,
              "Legacy Wi-Fi profile padding changed");
static_assert(sizeof(LegacyWifiProfileV1) == 112U,
              "Legacy Wi-Fi profile size changed");

void secureZero(void *memory, size_t length) {
  if (memory == nullptr) {
    return;
  }
  volatile uint8_t *cursor = static_cast<volatile uint8_t *>(memory);
  while (length-- > 0U) {
    *cursor++ = 0U;
  }
}

size_t boundedTextLength(const char *text, size_t capacity) {
  if (text == nullptr) {
    return capacity;
  }
  size_t length = 0U;
  while (length < capacity && text[length] != '\0') {
    ++length;
  }
  return length;
}

bool validSsid(const char *ssid) {
  const size_t length = boundedTextLength(ssid, kWifiSsidBytes);
  return length > 0U && length < kWifiSsidBytes;
}

bool validPassword(const char *password, bool secured) {
  const size_t length = boundedTextLength(password, kWifiPasswordBytes);
  if (length >= kWifiPasswordBytes) {
    return false;
  }
  return secured ? wifiSecuredPasswordValid(password) : length == 0U;
}

void copyText(char *destination, size_t destination_size, const char *source) {
  if (destination == nullptr || destination_size == 0U) {
    return;
  }
  memset(destination, 0, destination_size);
  if (source == nullptr) {
    return;
  }
  const size_t length = boundedTextLength(source, destination_size - 1U);
  memcpy(destination, source, length);
}

bool equalText(const char *left, const char *right) {
  return left != nullptr && right != nullptr && strcmp(left, right) == 0;
}

bool canonicalText(const char *text, size_t capacity, bool require_nonempty) {
  const size_t length = boundedTextLength(text, capacity);
  if (length >= capacity || (require_nonempty && length == 0U)) {
    return false;
  }
  for (size_t index = length + 1U; index < capacity; ++index) {
    if (text[index] != '\0') {
      return false;
    }
  }
  return true;
}

bool allZero(const void *memory, size_t length) {
  const uint8_t *bytes = static_cast<const uint8_t *>(memory);
  for (size_t index = 0U; index < length; ++index) {
    if (bytes[index] != 0U) {
      return false;
    }
  }
  return true;
}

uint32_t fnv1a(const void *memory, size_t length) {
  const uint8_t *bytes = static_cast<const uint8_t *>(memory);
  uint32_t hash = 2166136261U;
  for (size_t index = 0U; index < length; ++index) {
    hash ^= bytes[index];
    hash *= 16777619U;
  }
  return hash;
}

bool validV2Envelope(const EncodedWifiProfilesV2 &envelope) {
  if (envelope.magic != kWifiProfilesMagic ||
      envelope.version != kWifiProfilesVersion ||
      envelope.size != sizeof(envelope) ||
      envelope.count > kWifiProfileStoreCapacity ||
      !allZero(envelope.reserved, sizeof(envelope.reserved)) ||
      envelope.checksum !=
          fnv1a(&envelope, offsetof(EncodedWifiProfilesV2, checksum))) {
    return false;
  }

  for (size_t index = 0U; index < envelope.count; ++index) {
    const EncodedWifiProfile &profile = envelope.profiles[index];
    if (!canonicalText(profile.ssid, sizeof(profile.ssid), true) ||
        !canonicalText(profile.password, sizeof(profile.password), false) ||
        profile.secured > 1U || profile.reserved != 0U ||
        !validPassword(profile.password, profile.secured != 0U)) {
      return false;
    }
    for (size_t prior = 0U; prior < index; ++prior) {
      if (strcmp(profile.ssid, envelope.profiles[prior].ssid) == 0) {
        return false;
      }
    }
  }

  for (size_t index = envelope.count; index < kWifiProfileStoreCapacity;
       ++index) {
    if (!allZero(&envelope.profiles[index], sizeof(envelope.profiles[index]))) {
      return false;
    }
  }
  return true;
}

bool validLegacyEnvelope(const LegacyWifiProfileV1 &profile) {
  const bool secured = profile.password[0] != '\0';
  return profile.magic == kLegacyWifiProfileMagic &&
         profile.version == kLegacyWifiProfileVersion &&
         profile.size == sizeof(profile) &&
         validPassword(profile.password, secured) &&
         profile.checksum ==
             fnv1a(&profile, offsetof(LegacyWifiProfileV1, checksum));
}

}  // namespace

bool wifiSecuredPasswordValid(const char *password) {
  const size_t length = boundedTextLength(password, kWifiPasswordBytes);
  if (length >= 8U && length <= 63U) {
    return true;
  }
  if (length != 64U) {
    return false;
  }
  for (size_t index = 0U; index < length; ++index) {
    const char character = password[index];
    const bool hexadecimal =
        (character >= '0' && character <= '9') ||
        (character >= 'a' && character <= 'f') ||
        (character >= 'A' && character <= 'F');
    if (!hexadecimal) {
      return false;
    }
  }
  return true;
}

WifiProfileStore::WifiProfileStore() { clear(); }

WifiProfileStore::~WifiProfileStore() { clear(); }

bool WifiProfileStore::cloneTo(WifiProfileStore *output) const {
  if (output == nullptr) {
    return false;
  }
  if (output == this) {
    return true;
  }
  output->clear();
  memcpy(output->profiles_, profiles_, sizeof(profiles_));
  output->count_ = count_;
  return true;
}

void WifiProfileStore::swap(WifiProfileStore *other) {
  if (other == nullptr || other == this) {
    return;
  }
  for (size_t index = 0U; index < kWifiProfileStoreCapacity; ++index) {
    WifiCredentials temporary{};
    memcpy(&temporary, &profiles_[index], sizeof(temporary));
    memcpy(&profiles_[index], &other->profiles_[index],
           sizeof(profiles_[index]));
    memcpy(&other->profiles_[index], &temporary,
           sizeof(other->profiles_[index]));
    secureZero(&temporary, sizeof(temporary));
  }
  const uint8_t temporary_count = count_;
  count_ = other->count_;
  other->count_ = temporary_count;
}

size_t WifiProfileStore::count() const { return count_; }

bool WifiProfileStore::empty() const { return count_ == 0U; }

size_t WifiProfileStore::findIndex(const char *ssid) const {
  if (!validSsid(ssid)) {
    return kWifiProfileStoreCapacity;
  }
  for (size_t index = 0U; index < count_; ++index) {
    if (equalText(profiles_[index].ssid, ssid)) {
      return index;
    }
  }
  return kWifiProfileStoreCapacity;
}

bool WifiProfileStore::contains(const char *ssid) const {
  return findIndex(ssid) < count_;
}

bool WifiProfileStore::copyAt(size_t most_recent_index,
                              WifiCredentials *output) const {
  clearCredentials(output);
  if (output == nullptr || most_recent_index >= count_) {
    return false;
  }
  memcpy(output, &profiles_[most_recent_index], sizeof(*output));
  return true;
}

bool WifiProfileStore::copyForSsid(const char *ssid,
                                   WifiCredentials *output) const {
  clearCredentials(output);
  if (output == nullptr) {
    return false;
  }
  const size_t index = findIndex(ssid);
  if (index >= count_) {
    return false;
  }
  memcpy(output, &profiles_[index], sizeof(*output));
  return true;
}

void WifiProfileStore::moveToFront(size_t index) {
  if (index == 0U || index >= count_) {
    return;
  }
  WifiCredentials selected{};
  memcpy(&selected, &profiles_[index], sizeof(selected));
  for (size_t cursor = index; cursor > 0U; --cursor) {
    memcpy(&profiles_[cursor], &profiles_[cursor - 1U],
           sizeof(profiles_[cursor]));
  }
  memcpy(&profiles_[0], &selected, sizeof(profiles_[0]));
  secureZero(&selected, sizeof(selected));
}

WifiProfileMutation WifiProfileStore::recordSuccessful(const char *ssid,
                                                       const char *password,
                                                       bool secured) {
  if (!validSsid(ssid) || !validPassword(password, secured)) {
    return WifiProfileMutation::kInvalidInput;
  }

  const size_t existing = findIndex(ssid);
  if (existing < count_) {
    const bool credentials_changed =
        !equalText(profiles_[existing].password, password) ||
        profiles_[existing].secured != secured;
    if (!credentials_changed && existing == 0U) {
      return WifiProfileMutation::kUnchanged;
    }
    if (credentials_changed) {
      copyText(profiles_[existing].password,
               sizeof(profiles_[existing].password), password);
      profiles_[existing].secured = secured;
    }
    moveToFront(existing);
    return credentials_changed ? WifiProfileMutation::kUpdated
                               : WifiProfileMutation::kReordered;
  }

  WifiCredentials inserted{};
  copyText(inserted.ssid, sizeof(inserted.ssid), ssid);
  copyText(inserted.password, sizeof(inserted.password), password);
  inserted.secured = secured;
  const bool evicting = count_ == kWifiProfileStoreCapacity;
  const size_t destination_count =
      evicting ? kWifiProfileStoreCapacity : count_ + 1U;
  for (size_t cursor = destination_count - 1U; cursor > 0U; --cursor) {
    memcpy(&profiles_[cursor], &profiles_[cursor - 1U],
           sizeof(profiles_[cursor]));
  }
  memcpy(&profiles_[0], &inserted, sizeof(profiles_[0]));
  count_ = static_cast<uint8_t>(destination_count);
  secureZero(&inserted, sizeof(inserted));
  return evicting ? WifiProfileMutation::kEvictedAndInserted
                  : WifiProfileMutation::kInserted;
}

WifiProfileMutation WifiProfileStore::markSuccessful(const char *ssid) {
  if (!validSsid(ssid)) {
    return WifiProfileMutation::kInvalidInput;
  }
  const size_t index = findIndex(ssid);
  if (index >= count_) {
    return WifiProfileMutation::kNotFound;
  }
  if (index == 0U) {
    return WifiProfileMutation::kUnchanged;
  }
  moveToFront(index);
  return WifiProfileMutation::kReordered;
}

bool WifiProfileStore::forget(const char *ssid) {
  const size_t index = findIndex(ssid);
  if (index >= count_) {
    return false;
  }
  for (size_t cursor = index; cursor + 1U < count_; ++cursor) {
    memcpy(&profiles_[cursor], &profiles_[cursor + 1U],
           sizeof(profiles_[cursor]));
  }
  secureZero(&profiles_[count_ - 1U], sizeof(profiles_[0]));
  --count_;
  return true;
}

bool WifiProfileStore::encode(void *destination, size_t capacity) const {
  if (destination == nullptr || capacity < sizeof(EncodedWifiProfilesV2)) {
    return false;
  }

  EncodedWifiProfilesV2 envelope{};
  envelope.magic = kWifiProfilesMagic;
  envelope.version = kWifiProfilesVersion;
  envelope.size = sizeof(envelope);
  envelope.count = count_;
  for (size_t index = 0U; index < count_; ++index) {
    copyText(envelope.profiles[index].ssid,
             sizeof(envelope.profiles[index].ssid), profiles_[index].ssid);
    copyText(envelope.profiles[index].password,
             sizeof(envelope.profiles[index].password),
             profiles_[index].password);
    envelope.profiles[index].secured = profiles_[index].secured ? 1U : 0U;
  }
  envelope.checksum =
      fnv1a(&envelope, offsetof(EncodedWifiProfilesV2, checksum));
  memcpy(destination, &envelope, sizeof(envelope));
  secureZero(&envelope, sizeof(envelope));
  return true;
}

WifiProfileDecodeResult WifiProfileStore::decode(
    const void *source, size_t length, WifiProfileStore *output) {
  if (source == nullptr || output == nullptr) {
    return WifiProfileDecodeResult::kInvalidArgument;
  }

  if (length == sizeof(EncodedWifiProfilesV2)) {
    EncodedWifiProfilesV2 envelope{};
    memcpy(&envelope, source, sizeof(envelope));
    if (envelope.magic != kWifiProfilesMagic) {
      secureZero(&envelope, sizeof(envelope));
      return WifiProfileDecodeResult::kCorrupt;
    }
    if (envelope.version != kWifiProfilesVersion) {
      secureZero(&envelope, sizeof(envelope));
      return WifiProfileDecodeResult::kUnsupportedVersion;
    }
    if (!validV2Envelope(envelope)) {
      secureZero(&envelope, sizeof(envelope));
      return WifiProfileDecodeResult::kCorrupt;
    }

    output->clear();
    output->count_ = envelope.count;
    for (size_t index = 0U; index < envelope.count; ++index) {
      copyText(output->profiles_[index].ssid,
               sizeof(output->profiles_[index].ssid),
               envelope.profiles[index].ssid);
      copyText(output->profiles_[index].password,
               sizeof(output->profiles_[index].password),
               envelope.profiles[index].password);
      output->profiles_[index].secured =
          envelope.profiles[index].secured != 0U;
    }
    const WifiProfileDecodeResult result =
        envelope.count == 0U ? WifiProfileDecodeResult::kEmpty
                             : WifiProfileDecodeResult::kLoaded;
    secureZero(&envelope, sizeof(envelope));
    return result;
  }

  if (length == sizeof(LegacyWifiProfileV1)) {
    LegacyWifiProfileV1 legacy{};
    memcpy(&legacy, source, sizeof(legacy));
    if (legacy.magic != kLegacyWifiProfileMagic) {
      secureZero(&legacy, sizeof(legacy));
      return WifiProfileDecodeResult::kCorrupt;
    }
    if (legacy.version != kLegacyWifiProfileVersion) {
      secureZero(&legacy, sizeof(legacy));
      return WifiProfileDecodeResult::kUnsupportedVersion;
    }
    if (!validLegacyEnvelope(legacy)) {
      secureZero(&legacy, sizeof(legacy));
      return WifiProfileDecodeResult::kCorrupt;
    }

    if (legacy.ssid[0] == '\0') {
      // The old implementation wrote an all-zero valid record as a tombstone
      // so forgetting a network would not resurrect build-time credentials.
      if (!allZero(legacy.ssid, sizeof(legacy.ssid)) ||
          !allZero(legacy.password, sizeof(legacy.password))) {
        secureZero(&legacy, sizeof(legacy));
        return WifiProfileDecodeResult::kCorrupt;
      }
      output->clear();
      secureZero(&legacy, sizeof(legacy));
      return WifiProfileDecodeResult::kMigratedLegacyV1Empty;
    }
    if (!validSsid(legacy.ssid)) {
      secureZero(&legacy, sizeof(legacy));
      return WifiProfileDecodeResult::kCorrupt;
    }

    output->clear();
    output->count_ = 1U;
    copyText(output->profiles_[0].ssid, sizeof(output->profiles_[0].ssid),
             legacy.ssid);
    copyText(output->profiles_[0].password,
             sizeof(output->profiles_[0].password), legacy.password);
    output->profiles_[0].secured = legacy.password[0] != '\0';
    secureZero(&legacy, sizeof(legacy));
    return WifiProfileDecodeResult::kMigratedLegacyV1;
  }

  // Recognize a future version of either known envelope even when its size
  // differs, while treating arbitrary or truncated bytes as corruption.
  if (length >= 8U) {
    uint32_t magic = 0U;
    uint16_t version = 0U;
    memcpy(&magic, source, sizeof(magic));
    memcpy(&version, static_cast<const uint8_t *>(source) + sizeof(magic),
           sizeof(version));
    if ((magic == kWifiProfilesMagic && version != kWifiProfilesVersion) ||
        (magic == kLegacyWifiProfileMagic &&
         version != kLegacyWifiProfileVersion)) {
      return WifiProfileDecodeResult::kUnsupportedVersion;
    }
  }
  return WifiProfileDecodeResult::kCorrupt;
}

void WifiProfileStore::clear() {
  secureZero(profiles_, sizeof(profiles_));
  count_ = 0U;
}

void WifiProfileStore::clearCredentials(WifiCredentials *credentials) {
  if (credentials != nullptr) {
    secureZero(credentials, sizeof(*credentials));
  }
}
