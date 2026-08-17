#pragma once

#include <stddef.h>
#include <stdint.h>

// Leave room for the server-managed favourites list to grow without making a
// harmless eleventh entry invalidate the complete catalogue.
constexpr size_t kLocationCatalogCapacity = 16U;
// English on-device search text, including the trailing NUL. Keeping this
// fixed avoids heap churn while the HTTPS request and LCD framebuffer coexist.
constexpr size_t kLocationSearchQueryBytes = 49U;

enum class LocationCatalogState : uint8_t {
  kIdle = 0,
  kLoading,
  kReady,
  kSaving,
  kSaved,
  kError,
};

struct LocationOption {
  // Stable server-side identifier used by PUT /api/v1/location.
  char id[24]{};
  // Original human-readable name returned by the server (UTF-8 allowed).
  char location[80]{};
  // ASCII-only label intended for the ESP32's compact built-in font.
  char display_location[33]{};
  double lat{0.0};
  double lon{0.0};
  bool is_coastal{false};
};

struct LocationCatalog {
  LocationOption options[kLocationCatalogCapacity]{};
  size_t count{0U};
  LocationCatalogState state{LocationCatalogState::kIdle};
  uint32_t revision{0U};
  int http_status{0};
};

// Shared 800x480 picker geometry. Touch handling should use these exact values
// so its hit boxes always match CoastalDisplay::showLocationPicker().
namespace location_picker_ui {

struct Rect {
  int16_t x;
  int16_t y;
  int16_t width;
  int16_t height;

  constexpr bool contains(int16_t point_x, int16_t point_y) const {
    return point_x >= x && point_x < x + width && point_y >= y &&
           point_y < y + height;
  }
};

constexpr size_t kColumns = 2U;
constexpr size_t kRows = 4U;
constexpr size_t kPageSize = kColumns * kRows;

constexpr int16_t kCardLeft = 24;
constexpr int16_t kCardTop = 76;
constexpr int16_t kCardWidth = 364;
constexpr int16_t kCardHeight = 68;
constexpr int16_t kCardHorizontalGap = 24;
constexpr int16_t kCardVerticalGap = 8;

constexpr Rect cardRect(size_t slot) {
  return Rect{
      static_cast<int16_t>(kCardLeft +
                           (slot % kColumns) *
                               (kCardWidth + kCardHorizontalGap)),
      static_cast<int16_t>(kCardTop +
                           (slot / kColumns) *
                               (kCardHeight + kCardVerticalGap)),
      kCardWidth,
      kCardHeight,
  };
}

constexpr Rect kBackButton{24, 408, 140, 52};
constexpr Rect kPreviousButton{180, 408, 140, 52};
constexpr Rect kNextButton{336, 408, 140, 52};
constexpr Rect kApplyButton{492, 408, 284, 52};
// Header action shared by rendering and touch handling. It sits between the
// 312-pixel title and the page/state indicators without overlapping either.
constexpr Rect kSearchButton{365, 11, 166, 42};

}  // namespace location_picker_ui
