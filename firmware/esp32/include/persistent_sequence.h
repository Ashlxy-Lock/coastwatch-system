#pragma once

#include <stdint.h>

enum class PersistentSequenceState : uint8_t {
  kUninitialized = 0,
  kReady,
  kStorageFailure,
  kExhausted,
};

// Allocates uint32 telemetry sequence numbers from NVS-backed reservations.
//
// A block is made durable before any number in that block is returned. After
// an unexpected reset the unused tail is deliberately skipped, so a sequence
// number that may already have been uploaded is never issued again.
//
// This class is intentionally single-owner and is not thread-safe. Create one
// instance and call it from the task that constructs telemetry frames.
class PersistentSequence {
 public:
  static constexpr uint32_t kReservationBlockSize = 4096U;

  // Opens NVS and durably reserves the first block for this runtime. No value
  // may be requested until begin() succeeds.
  bool begin();

  bool ready() const;

  // True after an NVS failure, invalid persisted record, or uint32 exhaustion.
  // In every degraded state next() fails closed.
  bool degraded() const;

  PersistentSequenceState state() const;

  // Writes the next sequence number to |value|. On failure |value| is left
  // unchanged. Issued numbers are never reused, including when later upload
  // fails.
  bool next(uint32_t *value);

 private:
  bool reserveBlock(uint64_t first_value);
  void failStorage();

  PersistentSequenceState state_{PersistentSequenceState::kUninitialized};
  uint64_t next_value_{0U};
  uint64_t reserved_until_exclusive_{0U};
};
