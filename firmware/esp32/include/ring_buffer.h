#pragma once

#include <stddef.h>
#include <stdint.h>

template <size_t Capacity>
class ByteRingBuffer {
 public:
  static_assert(Capacity > 1, "ring buffer capacity must be greater than one");

  bool push(uint8_t value) {
    if (size_ == Capacity) {
      return false;
    }
    data_[head_] = value;
    head_ = (head_ + 1U) % Capacity;
    ++size_;
    return true;
  }

  bool pop(uint8_t *value) {
    if (value == nullptr || size_ == 0U) {
      return false;
    }
    *value = data_[tail_];
    tail_ = (tail_ + 1U) % Capacity;
    --size_;
    return true;
  }

  void clear() {
    head_ = 0U;
    tail_ = 0U;
    size_ = 0U;
  }

  size_t size() const { return size_; }
  constexpr size_t capacity() const { return Capacity; }

 private:
  uint8_t data_[Capacity]{};
  size_t head_{0U};
  size_t tail_{0U};
  size_t size_{0U};
};

enum class LineEvent : uint8_t {
  kNone = 0,
  kReady,
  kDroppedOversize,
};

template <size_t MaxFrameBytes>
class LineReader {
 public:
  LineEvent push(char value, const char **line) {
    if (line != nullptr) {
      *line = nullptr;
    }

    if (value == '\r') {
      return LineEvent::kNone;
    }

    if (value == '\n') {
      if (discarding_) {
        reset();
        return LineEvent::kDroppedOversize;
      }
      if (length_ == 0U) {
        return LineEvent::kNone;
      }
      buffer_[length_] = '\0';
      length_ = 0U;
      if (line != nullptr) {
        *line = buffer_;
      }
      return LineEvent::kReady;
    }

    if (discarding_) {
      return LineEvent::kNone;
    }

    if (length_ >= MaxFrameBytes) {
      length_ = 0U;
      discarding_ = true;
      return LineEvent::kNone;
    }

    buffer_[length_++] = value;
    return LineEvent::kNone;
  }

  void discardUntilNewline() {
    length_ = 0U;
    discarding_ = true;
  }

  void reset() {
    length_ = 0U;
    discarding_ = false;
  }

 private:
  char buffer_[MaxFrameBytes + 1U]{};
  size_t length_{0U};
  bool discarding_{false};
};

