#include "persistent_sequence.h"

#include <Preferences.h>

#include <cstddef>

namespace {

constexpr char kPreferencesNamespace[] = "coast-seq";
constexpr char kReservationKey[] = "reservation";
constexpr uint32_t kRecordMagic = 0x43534551U;  // "CSEQ"
constexpr uint16_t kRecordVersion = 1U;
constexpr uint64_t kSequenceSpaceSize = UINT64_C(1) << 32U;

struct StoredSequenceReservation {
  uint32_t magic;
  uint16_t version;
  uint16_t size;
  uint64_t reserved_until_exclusive;
  uint32_t block_size;
  uint32_t checksum;
};

static_assert(sizeof(StoredSequenceReservation) == 24U,
              "unexpected sequence reservation record layout");

uint32_t recordChecksum(const StoredSequenceReservation &record) {
  const uint8_t *bytes = reinterpret_cast<const uint8_t *>(&record);
  uint32_t hash = 2166136261U;
  for (size_t index = 0U;
       index < offsetof(StoredSequenceReservation, checksum); ++index) {
    hash ^= bytes[index];
    hash *= 16777619U;
  }
  return hash;
}

bool validRecord(const StoredSequenceReservation &record) {
  return record.magic == kRecordMagic &&
         record.version == kRecordVersion &&
         record.size == sizeof(StoredSequenceReservation) &&
         record.block_size == PersistentSequence::kReservationBlockSize &&
         record.reserved_until_exclusive >=
             PersistentSequence::kReservationBlockSize &&
         record.reserved_until_exclusive <= kSequenceSpaceSize &&
         record.reserved_until_exclusive %
                 PersistentSequence::kReservationBlockSize ==
             0U &&
         record.checksum == recordChecksum(record);
}

bool sameRecord(const StoredSequenceReservation &left,
                const StoredSequenceReservation &right) {
  return left.magic == right.magic && left.version == right.version &&
         left.size == right.size &&
         left.reserved_until_exclusive == right.reserved_until_exclusive &&
         left.block_size == right.block_size &&
         left.checksum == right.checksum;
}

}  // namespace

constexpr uint32_t PersistentSequence::kReservationBlockSize;

bool PersistentSequence::begin() {
  if (state_ == PersistentSequenceState::kReady) {
    return true;
  }
  if (state_ == PersistentSequenceState::kExhausted) {
    return false;
  }

  state_ = PersistentSequenceState::kUninitialized;
  next_value_ = 0U;
  reserved_until_exclusive_ = 0U;

  Preferences preferences;
  if (!preferences.begin(kPreferencesNamespace, false)) {
    failStorage();
    return false;
  }

  const bool has_reservation = preferences.isKey(kReservationKey);
  uint64_t first_value = 0U;
  if (has_reservation) {
    const size_t stored_size = preferences.getBytesLength(kReservationKey);
    StoredSequenceReservation stored{};
    const size_t read_size =
        stored_size == sizeof(stored)
            ? preferences.getBytes(kReservationKey, &stored, sizeof(stored))
            : 0U;
    preferences.end();
    if (read_size != sizeof(stored) || !validRecord(stored)) {
      failStorage();
      return false;
    }
    first_value = stored.reserved_until_exclusive;
  } else {
    preferences.end();
  }

  if (first_value >= kSequenceSpaceSize) {
    state_ = PersistentSequenceState::kExhausted;
    return false;
  }
  return reserveBlock(first_value);
}

bool PersistentSequence::ready() const {
  return state_ == PersistentSequenceState::kReady;
}

bool PersistentSequence::degraded() const {
  return state_ == PersistentSequenceState::kStorageFailure ||
         state_ == PersistentSequenceState::kExhausted;
}

PersistentSequenceState PersistentSequence::state() const { return state_; }

bool PersistentSequence::next(uint32_t *value) {
  if (value == nullptr || state_ != PersistentSequenceState::kReady) {
    return false;
  }

  if (next_value_ >= reserved_until_exclusive_) {
    if (!reserveBlock(reserved_until_exclusive_)) {
      return false;
    }
  }
  if (next_value_ >= kSequenceSpaceSize) {
    state_ = PersistentSequenceState::kExhausted;
    return false;
  }

  const uint32_t issued = static_cast<uint32_t>(next_value_);
  ++next_value_;
  *value = issued;

  if (next_value_ >= kSequenceSpaceSize) {
    state_ = PersistentSequenceState::kExhausted;
  }
  return true;
}

bool PersistentSequence::reserveBlock(uint64_t first_value) {
  if (first_value >= kSequenceSpaceSize) {
    state_ = PersistentSequenceState::kExhausted;
    return false;
  }

  uint64_t reserved_until =
      first_value + static_cast<uint64_t>(kReservationBlockSize);
  if (reserved_until > kSequenceSpaceSize) {
    reserved_until = kSequenceSpaceSize;
  }

  StoredSequenceReservation record{};
  record.magic = kRecordMagic;
  record.version = kRecordVersion;
  record.size = sizeof(record);
  record.reserved_until_exclusive = reserved_until;
  record.block_size = kReservationBlockSize;
  record.checksum = recordChecksum(record);

  Preferences preferences;
  if (!preferences.begin(kPreferencesNamespace, false)) {
    failStorage();
    return false;
  }
  const size_t written =
      preferences.putBytes(kReservationKey, &record, sizeof(record));
  StoredSequenceReservation verified{};
  const size_t read_size =
      written == sizeof(record)
          ? preferences.getBytes(kReservationKey, &verified, sizeof(verified))
          : 0U;
  preferences.end();

  if (written != sizeof(record) || read_size != sizeof(verified) ||
      !validRecord(verified) || !sameRecord(record, verified)) {
    failStorage();
    return false;
  }

  // Publish the in-memory range only after the next range boundary is durable.
  next_value_ = first_value;
  reserved_until_exclusive_ = reserved_until;
  state_ = PersistentSequenceState::kReady;
  return true;
}

void PersistentSequence::failStorage() {
  next_value_ = 0U;
  reserved_until_exclusive_ = 0U;
  state_ = PersistentSequenceState::kStorageFailure;
}
