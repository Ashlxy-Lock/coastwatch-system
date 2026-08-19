#pragma once

#include <stddef.h>
#include <stdint.h>

#include "wifi_setup.h"

// A bounded, allocation-free Wi-Fi credential store. Entries are kept in
// most-recent-success order: index 0 is tried first and the final entry is the
// eviction candidate when the store is full.
constexpr size_t kWifiProfileStoreCapacity = 16U;
static_assert(kWifiProfileStoreCapacity == kWifiSavedProfileCapacity,
              "Wi-Fi store and UI capacities must stay aligned");

struct WifiCredentials {
  char ssid[kWifiSsidBytes]{};
  char password[kWifiPasswordBytes]{};
  bool secured{true};
};

// WPA/WPA2 passphrases are 8..63 bytes. A 64-byte value is also accepted
// when it is the hexadecimal raw PSK supported by the ESP32 Wi-Fi stack.
bool wifiSecuredPasswordValid(const char *password);

enum class WifiProfileMutation : uint8_t {
  kInvalidInput = 0,
  kNotFound,
  kUnchanged,
  kInserted,
  kUpdated,
  kReordered,
  kEvictedAndInserted,
};

constexpr bool wifiProfileMutationChanged(WifiProfileMutation mutation) {
  return mutation == WifiProfileMutation::kInserted ||
         mutation == WifiProfileMutation::kUpdated ||
         mutation == WifiProfileMutation::kReordered ||
         mutation == WifiProfileMutation::kEvictedAndInserted;
}

enum class WifiProfileDecodeResult : uint8_t {
  kInvalidArgument = 0,
  kLoaded,
  kEmpty,
  kMigratedLegacyV1,
  kMigratedLegacyV1Empty,
  kCorrupt,
  kUnsupportedVersion,
};

class WifiProfileStore final {
 public:
  // V2 is one fixed-size NVS blob: a 12-byte header, sixteen fixed records,
  // and a 32-bit checksum. Keeping this public lets callers use a fixed buffer
  // without duplicating the persistence layout.
  static constexpr size_t kEncodedSize =
      12U + kWifiProfileStoreCapacity *
                (kWifiSsidBytes + kWifiPasswordBytes + 2U) +
      4U;

  WifiProfileStore();
  ~WifiProfileStore();

  WifiProfileStore(const WifiProfileStore &) = delete;
  WifiProfileStore &operator=(const WifiProfileStore &) = delete;

  // Explicit copy/swap operations support transactional persistence: clone
  // the live store, mutate/encode/write the clone, then swap only after NVS
  // confirms the complete blob write.
  bool cloneTo(WifiProfileStore *output) const;
  void swap(WifiProfileStore *other);

  size_t count() const;
  bool empty() const;
  bool contains(const char *ssid) const;

  // Copies credentials out so callers never receive a pointer into the
  // store's secret-bearing memory. The output is zeroed on failure.
  bool copyAt(size_t most_recent_index, WifiCredentials *output) const;
  bool copyForSsid(const char *ssid, WifiCredentials *output) const;

  // Call only after a connection succeeds. The SSID is de-duplicated, a new
  // password/security class replaces the old one, and the profile becomes
  // most recent. Open networks require an empty password; secured networks
  // require 8..63 bytes or an exact 64-character hexadecimal raw PSK.
  WifiProfileMutation recordSuccessful(const char *ssid,
                                       const char *password, bool secured);

  // Moves an already-saved profile to the front without changing its secret.
  WifiProfileMutation markSuccessful(const char *ssid);

  // Removes one matching profile and securely clears the vacated slot.
  bool forget(const char *ssid);

  // Produces/consumes the fixed V2 envelope suitable for one Preferences NVS
  // blob. decode() also accepts the exact legacy V1 single-profile envelope.
  // On corrupt/unsupported input, decode() leaves output unchanged.
  bool encode(void *destination, size_t capacity) const;
  static WifiProfileDecodeResult decode(const void *source, size_t length,
                                        WifiProfileStore *output);

  // Securely removes all credentials from RAM. clearSecrets() is an explicit
  // alias for call sites where the security intent is more readable.
  void clear();
  void clearSecrets() { clear(); }
  static void clearCredentials(WifiCredentials *credentials);

 private:
  size_t findIndex(const char *ssid) const;
  void moveToFront(size_t index);

  WifiCredentials profiles_[kWifiProfileStoreCapacity]{};
  uint8_t count_{0U};
};

static_assert(WifiProfileStore::kEncodedSize == 1616U,
              "Wi-Fi profile envelope size changed unexpectedly");
