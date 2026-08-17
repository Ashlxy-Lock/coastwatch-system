#pragma once

#include <stddef.h>
#include <stdint.h>

constexpr size_t kWifiCatalogCapacity = 12U;
constexpr size_t kWifiSsidBytes = 33U;
constexpr size_t kWifiPasswordBytes = 65U;

enum class WifiSetupState : uint8_t {
  kIdle = 0,
  kScanning,
  kReady,
  kConnecting,
  kForgetting,
  kConnected,
  kError,
};

enum class WifiSetupError : uint8_t {
  kNone = 0,
  kScanFailed,
  kInvalidSelection,
  kInvalidPassword,
  kUnsupportedSecurity,
  kNetworkNotFound,
  kAuthenticationFailed,
  kAssociationFailed,
  kDhcpFailed,
  kOldLinkBusy,
  kConnectionFailed,
  kConnectionTimeout,
  kStorageFailed,
  kForgetFailed,
};

enum class WifiKeyboardMode : uint8_t {
  kLower = 0,
  kUpper,
  kSymbols,
};

struct WifiNetworkOption {
  char ssid[kWifiSsidBytes]{};
  int32_t rssi{-127};
  uint8_t auth_mode{0U};
  bool secured{true};
  bool supported{false};
};

struct WifiCatalog {
  WifiNetworkOption options[kWifiCatalogCapacity]{};
  size_t count{0U};
  WifiSetupState state{WifiSetupState::kIdle};
  uint32_t revision{0U};
  WifiSetupError error{WifiSetupError::kNone};
  char active_ssid[kWifiSsidBytes]{};
};

// Shared 800x480 Wi-Fi-list geometry. Display rendering and touch handling
// must use these exact rectangles so the visible controls and hit boxes stay
// aligned.
namespace wifi_setup_ui {

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
constexpr Rect kRescanButton{176, 408, 140, 52};
constexpr Rect kForgetButton{328, 408, 140, 52};
constexpr Rect kPreviousButton{480, 408, 140, 52};
constexpr Rect kNextButton{632, 408, 144, 52};

constexpr Rect kForgetCancelButton{80, 392, 280, 60};
constexpr Rect kForgetConfirmButton{440, 392, 280, 60};

}  // namespace wifi_setup_ui

// Shared password-keyboard geometry and character mapping. The password is
// never stored in this model; UI code owns a short-lived fixed buffer and the
// network task receives a copy only after CONNECT is pressed.
namespace wifi_keyboard_ui {

using Rect = wifi_setup_ui::Rect;

constexpr size_t kRows = 4U;
constexpr size_t kColumns = 10U;
constexpr int16_t kKeyLeft = 23;
constexpr int16_t kKeyTop = 126;
constexpr int16_t kKeyWidth = 70;
constexpr int16_t kKeyHeight = 48;
constexpr int16_t kKeyHorizontalGap = 6;
constexpr int16_t kKeyVerticalGap = 6;

constexpr Rect keyRect(size_t row, size_t column) {
  return Rect{
      static_cast<int16_t>(kKeyLeft +
                           column * (kKeyWidth + kKeyHorizontalGap)),
      static_cast<int16_t>(kKeyTop +
                           row * (kKeyHeight + kKeyVerticalGap)),
      kKeyWidth,
      kKeyHeight,
  };
}

constexpr Rect kCancelButton{24, 408, 140, 52};
constexpr Rect kSpaceButton{180, 408, 296, 52};
constexpr Rect kConnectButton{492, 408, 284, 52};

constexpr bool isBackspaceCell(size_t row, size_t column) {
  return row == 2U && column == 9U;
}

constexpr bool isCaseCell(size_t row, size_t column) {
  return row == 3U && column == 7U;
}

constexpr bool isClearCell(size_t row, size_t column) {
  return row == 3U && column == 8U;
}

constexpr bool isModeCell(size_t row, size_t column) {
  return row == 3U && column == 9U;
}

inline char keyCharacter(WifiKeyboardMode mode, size_t row, size_t column) {
  if (row >= kRows || column >= kColumns ||
      isBackspaceCell(row, column) || isCaseCell(row, column) ||
      isClearCell(row, column) || isModeCell(row, column)) {
    return '\0';
  }

  static constexpr char kDigits[] = "1234567890";
  static constexpr char kLowerRow1[] = "qwertyuiop";
  static constexpr char kLowerRow2[] = "asdfghjkl";
  static constexpr char kLowerRow3[] = "zxcvbnm";
  static constexpr char kUpperRow1[] = "QWERTYUIOP";
  static constexpr char kUpperRow2[] = "ASDFGHJKL";
  static constexpr char kUpperRow3[] = "ZXCVBNM";
  static constexpr char kSymbolRow0[] = "!@#$%^&*()";
  static constexpr char kSymbolRow1[] = "_-+=[]{}\\|";
  static constexpr char kSymbolRow2[] = ";:'\",.<>?";
  static constexpr char kSymbolRow3[] = "/`~";

  if (mode == WifiKeyboardMode::kSymbols) {
    const char *rows[] = {kSymbolRow0, kSymbolRow1, kSymbolRow2,
                          kSymbolRow3};
    const size_t lengths[] = {10U, 10U, 9U, 3U};
    return column < lengths[row] ? rows[row][column] : '\0';
  }
  if (row == 0U) {
    return kDigits[column];
  }
  if (mode == WifiKeyboardMode::kUpper) {
    const char *rows[] = {nullptr, kUpperRow1, kUpperRow2, kUpperRow3};
    const size_t lengths[] = {0U, 10U, 9U, 7U};
    return column < lengths[row] ? rows[row][column] : '\0';
  }
  const char *rows[] = {nullptr, kLowerRow1, kLowerRow2, kLowerRow3};
  const size_t lengths[] = {0U, 10U, 9U, 7U};
  return column < lengths[row] ? rows[row][column] : '\0';
}

}  // namespace wifi_keyboard_ui
