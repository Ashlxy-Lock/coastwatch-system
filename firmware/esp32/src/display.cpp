#include "display.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "driver/gpio.h"
#include "esp_heap_caps.h"

namespace {

template <typename T, size_t N>
constexpr size_t arraySize(const T (&)[N]) {
  return N;
}

constexpr int kWidth = 800;
constexpr int kHeight = 480;
constexpr int kTransferRows = 10;
constexpr size_t kFramebufferBytes =
    static_cast<size_t>(kWidth) * kHeight * sizeof(uint16_t);
constexpr size_t kTransferBytes =
    static_cast<size_t>(kWidth) * kTransferRows * sizeof(uint16_t);

constexpr gpio_num_t kDataPins[] = {
    GPIO_NUM_4,  GPIO_NUM_5,  GPIO_NUM_6,  GPIO_NUM_7,
    GPIO_NUM_15, GPIO_NUM_16, GPIO_NUM_17, GPIO_NUM_18,
};
constexpr gpio_num_t kWritePin = GPIO_NUM_2;
constexpr gpio_num_t kChipSelectPin = GPIO_NUM_1;
constexpr gpio_num_t kDataCommandPin = GPIO_NUM_42;
constexpr gpio_num_t kReadPin = GPIO_NUM_41;
constexpr gpio_num_t kResetPin = GPIO_NUM_46;
constexpr uint32_t kPixelClockHz = 5U * 1000U * 1000U;

constexpr uint16_t rgb565(uint8_t red, uint8_t green, uint8_t blue) {
  return static_cast<uint16_t>(((red & 0xF8U) << 8U) |
                               ((green & 0xFCU) << 3U) | (blue >> 3U));
}

constexpr uint16_t kBackground = rgb565(5, 17, 31);
constexpr uint16_t kHeader = rgb565(10, 35, 61);
constexpr uint16_t kCard = rgb565(13, 42, 70);
constexpr uint16_t kCardAlt = rgb565(16, 49, 80);
constexpr uint16_t kCyan = rgb565(35, 205, 225);
constexpr uint16_t kWhite = rgb565(239, 247, 255);
constexpr uint16_t kMuted = rgb565(147, 174, 197);
constexpr uint16_t kYellow = rgb565(255, 193, 79);
constexpr uint16_t kGreen = rgb565(65, 214, 147);
constexpr uint16_t kRed = rgb565(238, 76, 89);
constexpr int kLoadingSpinnerCenterX = 101;
constexpr int kLoadingSpinnerCenterY = 224;
constexpr int kLoadingSpinnerRegionX = 53;
constexpr int kLoadingSpinnerRegionY = 176;
constexpr int kLoadingSpinnerRegionWidth = 96;
constexpr int kLoadingSpinnerRegionHeight = 96;
constexpr int kWifiKeyFeedbackX = 370;
constexpr int kWifiKeyFeedbackY = 15;
constexpr int kWifiKeyFeedbackWidth = 225;
constexpr int kWifiKeyFeedbackHeight = 34;
constexpr uint32_t kCollectionTelemetryMaximumAgeMs = 2500U;
constexpr uint32_t kCollectionUploadFreshnessMs = 2500U;

struct Glyph {
  char character;
  uint8_t columns[5];
};

constexpr Glyph kGlyphs[] = {
    {' ', {0x00, 0x00, 0x00, 0x00, 0x00}},
    {'!', {0x00, 0x00, 0x5F, 0x00, 0x00}},
    {'%', {0x62, 0x64, 0x08, 0x13, 0x23}},
    {'+', {0x08, 0x08, 0x3E, 0x08, 0x08}},
    {'-', {0x08, 0x08, 0x08, 0x08, 0x08}},
    {'.', {0x00, 0x60, 0x60, 0x00, 0x00}},
    {'/', {0x20, 0x10, 0x08, 0x04, 0x02}},
    {'0', {0x3E, 0x51, 0x49, 0x45, 0x3E}},
    {'1', {0x00, 0x42, 0x7F, 0x40, 0x00}},
    {'2', {0x42, 0x61, 0x51, 0x49, 0x46}},
    {'3', {0x21, 0x41, 0x45, 0x4B, 0x31}},
    {'4', {0x18, 0x14, 0x12, 0x7F, 0x10}},
    {'5', {0x27, 0x45, 0x45, 0x45, 0x39}},
    {'6', {0x3C, 0x4A, 0x49, 0x49, 0x30}},
    {'7', {0x01, 0x71, 0x09, 0x05, 0x03}},
    {'8', {0x36, 0x49, 0x49, 0x49, 0x36}},
    {'9', {0x06, 0x49, 0x49, 0x29, 0x1E}},
    {':', {0x00, 0x36, 0x36, 0x00, 0x00}},
    {'?', {0x02, 0x01, 0x51, 0x09, 0x06}},
    {'"', {0x00, 0x07, 0x00, 0x07, 0x00}},
    {'#', {0x14, 0x7F, 0x14, 0x7F, 0x14}},
    {'$', {0x24, 0x2A, 0x7F, 0x2A, 0x12}},
    {'&', {0x36, 0x49, 0x55, 0x22, 0x50}},
    {'\'', {0x00, 0x05, 0x03, 0x00, 0x00}},
    {'(', {0x00, 0x1C, 0x22, 0x41, 0x00}},
    {')', {0x00, 0x41, 0x22, 0x1C, 0x00}},
    {'*', {0x14, 0x08, 0x3E, 0x08, 0x14}},
    {',', {0x00, 0x50, 0x30, 0x00, 0x00}},
    {';', {0x00, 0x56, 0x36, 0x00, 0x00}},
    {'<', {0x08, 0x14, 0x22, 0x41, 0x00}},
    {'=', {0x14, 0x14, 0x14, 0x14, 0x14}},
    {'>', {0x41, 0x22, 0x14, 0x08, 0x00}},
    {'@', {0x3E, 0x41, 0x5D, 0x55, 0x1E}},
    {'[', {0x00, 0x7F, 0x41, 0x41, 0x00}},
    {'\\', {0x02, 0x04, 0x08, 0x10, 0x20}},
    {']', {0x00, 0x41, 0x41, 0x7F, 0x00}},
    {'^', {0x04, 0x02, 0x01, 0x02, 0x04}},
    {'_', {0x40, 0x40, 0x40, 0x40, 0x40}},
    {'`', {0x00, 0x03, 0x05, 0x00, 0x00}},
    {'{', {0x08, 0x36, 0x41, 0x41, 0x00}},
    {'|', {0x00, 0x00, 0x7F, 0x00, 0x00}},
    {'}', {0x00, 0x41, 0x41, 0x36, 0x08}},
    {'~', {0x08, 0x04, 0x08, 0x10, 0x08}},
    {'A', {0x7E, 0x11, 0x11, 0x11, 0x7E}},
    {'B', {0x7F, 0x49, 0x49, 0x49, 0x36}},
    {'C', {0x3E, 0x41, 0x41, 0x41, 0x22}},
    {'D', {0x7F, 0x41, 0x41, 0x22, 0x1C}},
    {'E', {0x7F, 0x49, 0x49, 0x49, 0x41}},
    {'F', {0x7F, 0x09, 0x09, 0x09, 0x01}},
    {'G', {0x3E, 0x41, 0x49, 0x49, 0x7A}},
    {'H', {0x7F, 0x08, 0x08, 0x08, 0x7F}},
    {'I', {0x00, 0x41, 0x7F, 0x41, 0x00}},
    {'J', {0x20, 0x40, 0x41, 0x3F, 0x01}},
    {'K', {0x7F, 0x08, 0x14, 0x22, 0x41}},
    {'L', {0x7F, 0x40, 0x40, 0x40, 0x40}},
    {'M', {0x7F, 0x02, 0x0C, 0x02, 0x7F}},
    {'N', {0x7F, 0x04, 0x08, 0x10, 0x7F}},
    {'O', {0x3E, 0x41, 0x41, 0x41, 0x3E}},
    {'P', {0x7F, 0x09, 0x09, 0x09, 0x06}},
    {'Q', {0x3E, 0x41, 0x51, 0x21, 0x5E}},
    {'R', {0x7F, 0x09, 0x19, 0x29, 0x46}},
    {'S', {0x46, 0x49, 0x49, 0x49, 0x31}},
    {'T', {0x01, 0x01, 0x7F, 0x01, 0x01}},
    {'U', {0x3F, 0x40, 0x40, 0x40, 0x3F}},
    {'V', {0x1F, 0x20, 0x40, 0x20, 0x1F}},
    {'W', {0x3F, 0x40, 0x38, 0x40, 0x3F}},
    {'X', {0x63, 0x14, 0x08, 0x14, 0x63}},
    {'Y', {0x07, 0x08, 0x70, 0x08, 0x07}},
    {'Z', {0x61, 0x51, 0x49, 0x45, 0x43}},
    {'a', {0x20, 0x54, 0x54, 0x54, 0x78}},
    {'b', {0x7F, 0x48, 0x44, 0x44, 0x38}},
    {'c', {0x38, 0x44, 0x44, 0x44, 0x20}},
    {'d', {0x38, 0x44, 0x44, 0x48, 0x7F}},
    {'e', {0x38, 0x54, 0x54, 0x54, 0x18}},
    {'f', {0x08, 0x7E, 0x09, 0x01, 0x02}},
    {'g', {0x0C, 0x52, 0x52, 0x52, 0x3E}},
    {'h', {0x7F, 0x08, 0x04, 0x04, 0x78}},
    {'i', {0x00, 0x44, 0x7D, 0x40, 0x00}},
    {'j', {0x20, 0x40, 0x44, 0x3D, 0x00}},
    {'k', {0x7F, 0x10, 0x28, 0x44, 0x00}},
    {'l', {0x00, 0x41, 0x7F, 0x40, 0x00}},
    {'m', {0x7C, 0x04, 0x18, 0x04, 0x78}},
    {'n', {0x7C, 0x08, 0x04, 0x04, 0x78}},
    {'o', {0x38, 0x44, 0x44, 0x44, 0x38}},
    {'p', {0x7C, 0x14, 0x14, 0x14, 0x08}},
    {'q', {0x08, 0x14, 0x14, 0x18, 0x7C}},
    {'r', {0x7C, 0x08, 0x04, 0x04, 0x08}},
    {'s', {0x48, 0x54, 0x54, 0x54, 0x20}},
    {'t', {0x04, 0x3F, 0x44, 0x40, 0x20}},
    {'u', {0x3C, 0x40, 0x40, 0x20, 0x7C}},
    {'v', {0x1C, 0x20, 0x40, 0x20, 0x1C}},
    {'w', {0x3C, 0x40, 0x30, 0x40, 0x3C}},
    {'x', {0x44, 0x28, 0x10, 0x28, 0x44}},
    {'y', {0x0C, 0x50, 0x50, 0x50, 0x3C}},
    {'z', {0x44, 0x64, 0x54, 0x4C, 0x44}},
};

const uint8_t *glyphFor(char character) {
  for (const Glyph &glyph : kGlyphs) {
    if (glyph.character == character) {
      return glyph.columns;
    }
  }
  return kGlyphs[18].columns;  // '?'
}

void fillRect(uint16_t *buffer, int x, int y, int width, int height,
              uint16_t color) {
  const int x0 = std::max(0, x);
  const int y0 = std::max(0, y);
  const int x1 = std::min(kWidth, x + width);
  const int y1 = std::min(kHeight, y + height);
  if (x0 >= x1 || y0 >= y1) {
    return;
  }
  for (int row = y0; row < y1; ++row) {
    std::fill_n(buffer + static_cast<size_t>(row) * kWidth + x0, x1 - x0,
                color);
  }
}

void drawRect(uint16_t *buffer, int x, int y, int width, int height,
              int thickness, uint16_t color) {
  fillRect(buffer, x, y, width, thickness, color);
  fillRect(buffer, x, y + height - thickness, width, thickness, color);
  fillRect(buffer, x, y, thickness, height, color);
  fillRect(buffer, x + width - thickness, y, thickness, height, color);
}

void setPixel(uint16_t *buffer, int x, int y, uint16_t color) {
  if (x >= 0 && x < kWidth && y >= 0 && y < kHeight) {
    buffer[static_cast<size_t>(y) * kWidth + x] = color;
  }
}

void drawLine(uint16_t *buffer, int x0, int y0, int x1, int y1,
              uint16_t color) {
  const int dx = std::abs(x1 - x0);
  const int sx = x0 < x1 ? 1 : -1;
  const int dy = -std::abs(y1 - y0);
  const int sy = y0 < y1 ? 1 : -1;
  int error = dx + dy;
  while (true) {
    setPixel(buffer, x0, y0, color);
    if (x0 == x1 && y0 == y1) {
      break;
    }
    const int doubled = 2 * error;
    if (doubled >= dy) {
      error += dy;
      x0 += sx;
    }
    if (doubled <= dx) {
      error += dx;
      y0 += sy;
    }
  }
}

void fillCircle(uint16_t *buffer, int center_x, int center_y, int radius,
                uint16_t color) {
  const int radius_squared = radius * radius;
  for (int y = -radius; y <= radius; ++y) {
    for (int x = -radius; x <= radius; ++x) {
      if (x * x + y * y <= radius_squared) {
        setPixel(buffer, center_x + x, center_y + y, color);
      }
    }
  }
}

void drawCharacter(uint16_t *buffer, int x, int y, char character, int scale,
                   uint16_t color) {
  const uint8_t *glyph = glyphFor(character);
  for (int column = 0; column < 5; ++column) {
    for (int row = 0; row < 7; ++row) {
      if ((glyph[column] & (1U << row)) != 0U) {
        fillRect(buffer, x + column * scale, y + row * scale, scale, scale,
                 color);
      }
    }
  }
}

void drawText(uint16_t *buffer, int x, int y, const char *text, int scale,
              uint16_t color) {
  if (text == nullptr || scale <= 0) {
    return;
  }
  int cursor = x;
  while (*text != '\0') {
    drawCharacter(buffer, cursor, y, *text, scale, color);
    cursor += 6 * scale;
    ++text;
  }
}

void drawCenteredText(uint16_t *buffer, int x, int y, int width, int height,
                      const char *text, int scale, uint16_t color) {
  if (text == nullptr || text[0] == '\0' || scale <= 0) {
    return;
  }
  const int text_width =
      (static_cast<int>(std::strlen(text)) * 6 - 1) * scale;
  const int text_height = 7 * scale;
  drawText(buffer, x + (width - text_width) / 2,
           y + (height - text_height) / 2, text, scale, color);
}

void drawWifiKeyFeedback(uint16_t *buffer, const char *key_feedback) {
  fillRect(buffer, kWifiKeyFeedbackX, kWifiKeyFeedbackY,
           kWifiKeyFeedbackWidth, kWifiKeyFeedbackHeight, kHeader);
  if (key_feedback == nullptr || key_feedback[0] == '\0') {
    return;
  }

  fillRect(buffer, kWifiKeyFeedbackX, kWifiKeyFeedbackY,
           kWifiKeyFeedbackWidth, kWifiKeyFeedbackHeight,
           rgb565(13, 82, 75));
  drawRect(buffer, kWifiKeyFeedbackX, kWifiKeyFeedbackY,
           kWifiKeyFeedbackWidth, kWifiKeyFeedbackHeight, 2, kGreen);
  char label[20]{};
  std::snprintf(label, sizeof(label), "KEY: %s", key_feedback);
  drawCenteredText(buffer, kWifiKeyFeedbackX, kWifiKeyFeedbackY,
                   kWifiKeyFeedbackWidth, kWifiKeyFeedbackHeight, label, 2,
                   kWhite);
}

void drawCard(uint16_t *buffer, int x, int y, int width, int height,
              uint16_t fill) {
  fillRect(buffer, x, y, width, height, fill);
  drawRect(buffer, x, y, width, height, 2, rgb565(31, 75, 105));
}

void drawPickerButton(uint16_t *buffer,
                      const location_picker_ui::Rect &bounds,
                      const char *label, bool enabled, bool emphasized) {
  const uint16_t fill = !enabled
                            ? rgb565(15, 31, 45)
                            : (emphasized ? rgb565(13, 82, 75) : kCardAlt);
  const uint16_t border = !enabled ? rgb565(52, 70, 84)
                                   : (emphasized ? kGreen : kCyan);
  const uint16_t text_color = enabled ? kWhite : rgb565(91, 111, 128);
  fillRect(buffer, bounds.x, bounds.y, bounds.width, bounds.height, fill);
  drawRect(buffer, bounds.x, bounds.y, bounds.width, bounds.height, 2,
           border);
  drawCenteredText(buffer, bounds.x, bounds.y, bounds.width, bounds.height,
                   label, 2, text_color);
}

void drawModelButton(uint16_t *buffer, const model_ui::Rect &bounds,
                     const char *label, bool enabled, bool emphasized,
                     bool danger = false) {
  const uint16_t fill = !enabled
                            ? rgb565(15, 31, 45)
                            : (danger ? rgb565(92, 24, 30)
                                      : (emphasized ? rgb565(13, 82, 75)
                                                    : kCardAlt));
  const uint16_t border = !enabled ? rgb565(52, 70, 84)
                                   : (danger ? kRed
                                             : (emphasized ? kGreen : kCyan));
  fillRect(buffer, bounds.x, bounds.y, bounds.width, bounds.height, fill);
  drawRect(buffer, bounds.x, bounds.y, bounds.width, bounds.height, 2,
           border);
  drawCenteredText(buffer, bounds.x, bounds.y, bounds.width, bounds.height,
                   label, 2, enabled ? kWhite : rgb565(91, 111, 128));
}

void drawWifiButton(uint16_t *buffer, const wifi_setup_ui::Rect &bounds,
                    const char *label, bool enabled, bool emphasized,
                    bool danger = false) {
  const uint16_t fill = !enabled
                            ? rgb565(15, 31, 45)
                            : (danger ? rgb565(92, 24, 30)
                                      : (emphasized ? rgb565(13, 82, 75)
                                                    : kCardAlt));
  const uint16_t border = !enabled ? rgb565(52, 70, 84)
                                   : (danger ? kRed
                                             : (emphasized ? kGreen : kCyan));
  const uint16_t text_color = enabled ? kWhite : rgb565(91, 111, 128);
  fillRect(buffer, bounds.x, bounds.y, bounds.width, bounds.height, fill);
  drawRect(buffer, bounds.x, bounds.y, bounds.width, bounds.height, 2,
           border);
  drawCenteredText(buffer, bounds.x, bounds.y, bounds.width, bounds.height,
                   label, 2, text_color);
}

const char *wifiStateLabel(WifiSetupState state) {
  switch (state) {
    case WifiSetupState::kScanning:
      return "SCANNING";
    case WifiSetupState::kReady:
      return "SELECT";
    case WifiSetupState::kConnecting:
      return "CONNECTING";
    case WifiSetupState::kForgetting:
      return "FORGETTING";
    case WifiSetupState::kConnected:
      return "CONNECTED";
    case WifiSetupState::kError:
      return "ERROR";
    case WifiSetupState::kIdle:
    default:
      return "IDLE";
  }
}

uint16_t wifiStateColor(WifiSetupState state) {
  switch (state) {
    case WifiSetupState::kConnected:
      return kGreen;
    case WifiSetupState::kScanning:
    case WifiSetupState::kConnecting:
    case WifiSetupState::kForgetting:
      return kYellow;
    case WifiSetupState::kError:
      return kRed;
    case WifiSetupState::kReady:
      return kCyan;
    case WifiSetupState::kIdle:
    default:
      return kMuted;
  }
}

const char *wifiErrorLabel(WifiSetupError error) {
  switch (error) {
    case WifiSetupError::kScanFailed:
      return "SCAN FAILED - TAP RESCAN";
    case WifiSetupError::kInvalidSelection:
      return "NETWORK IS NO LONGER AVAILABLE";
    case WifiSetupError::kInvalidPassword:
      return "PASSWORD MUST BE 8 TO 63 CHARACTERS";
    case WifiSetupError::kUnsupportedSecurity:
      return "THIS WIFI SECURITY TYPE IS NOT SUPPORTED";
    case WifiSetupError::kNetworkNotFound:
      return "NETWORK NOT FOUND - ESP32 NEEDS 2.4 GHZ";
    case WifiSetupError::kAuthenticationFailed:
      return "AUTH FAILED - CHECK PASSWORD OR WPA MODE";
    case WifiSetupError::kAssociationFailed:
      return "ROUTER REJECTED CONNECTION";
    case WifiSetupError::kDhcpFailed:
      return "CONNECTED BUT ROUTER DID NOT PROVIDE AN IP";
    case WifiSetupError::kOldLinkBusy:
      return "OLD WIFI DID NOT DISCONNECT - RETRY";
    case WifiSetupError::kConnectionTimeout:
      return "CONNECTION TIMED OUT - CHECK 2.4 GHZ AND SIGNAL";
    case WifiSetupError::kStorageFailed:
      return "CONNECTED - SAVE FAILED";
    case WifiSetupError::kForgetFailed:
      return "FORGET FAILED - RETRY";
    case WifiSetupError::kConnectionFailed:
      return "CONNECTION FAILED - CHECK ROUTER SETTINGS";
    case WifiSetupError::kNone:
    default:
      return "";
  }
}

void makeWifiDisplayText(const char *input, char *destination,
                         size_t destination_size,
                         size_t maximum_characters) {
  if (destination == nullptr || destination_size == 0U) {
    return;
  }
  destination[0] = '\0';
  size_t output = 0U;
  bool saw_non_ascii = false;
  if (input != nullptr) {
    for (size_t index = 0U; input[index] != '\0' &&
                            output < maximum_characters &&
                            output + 1U < destination_size;
         ++index) {
      const unsigned char byte = static_cast<unsigned char>(input[index]);
      if (byte < 0x20U || byte > 0x7EU) {
        saw_non_ascii = true;
        continue;
      }
      destination[output++] = static_cast<char>(std::toupper(byte));
    }
  }
  while (output > 0U && destination[output - 1U] == ' ') {
    --output;
  }
  destination[output] = '\0';
  if (output == 0U) {
    std::snprintf(destination, destination_size, "%s",
                  saw_non_ascii ? "NON ASCII WIFI" : "HIDDEN WIFI");
  }
}

const char *catalogStateLabel(LocationCatalogState state) {
  switch (state) {
    case LocationCatalogState::kLoading:
      return "LOADING";
    case LocationCatalogState::kReady:
      return "READY";
    case LocationCatalogState::kSaving:
      return "SAVING";
    case LocationCatalogState::kSaved:
      return "SAVED";
    case LocationCatalogState::kError:
      return "ERROR";
    case LocationCatalogState::kIdle:
    default:
      return "IDLE";
  }
}

uint16_t catalogStateColor(LocationCatalogState state) {
  switch (state) {
    case LocationCatalogState::kReady:
    case LocationCatalogState::kSaved:
      return kGreen;
    case LocationCatalogState::kLoading:
    case LocationCatalogState::kSaving:
      return kYellow;
    case LocationCatalogState::kError:
      return kRed;
    case LocationCatalogState::kIdle:
    default:
      return kMuted;
  }
}

void splitPickerLabel(const char *source, char *first, size_t first_size,
                      char *second, size_t second_size) {
  constexpr size_t kCharactersPerLine = 24U;
  if (first_size == 0U || second_size == 0U) {
    return;
  }
  first[0] = '\0';
  second[0] = '\0';
  if (source == nullptr) {
    return;
  }

  const size_t length = std::strlen(source);
  if (length <= kCharactersPerLine) {
    std::snprintf(first, first_size, "%s", source);
    return;
  }

  size_t split = kCharactersPerLine;
  while (split > 8U && source[split] != ' ') {
    --split;
  }
  if (split <= 8U) {
    split = kCharactersPerLine;
  }
  std::snprintf(first, first_size, "%.*s", static_cast<int>(split), source);
  size_t remainder = split;
  while (source[remainder] == ' ') {
    ++remainder;
  }
  std::snprintf(second, second_size, "%.*s",
                static_cast<int>(kCharactersPerLine), source + remainder);
}

bool isDrawableAscii(char character) {
  const unsigned char value = static_cast<unsigned char>(character);
  return value >= 0x20U && value <= 0x7EU;
}

struct LocationAlias {
  const char *utf8_name;
  const char *display_name;
};

// The compact built-in font is ASCII-only. These aliases cover the common
// coastal selections while the server remains free to return an ASCII name.
constexpr LocationAlias kLocationAliases[] = {
    {"未配置", "DEMO COAST"},   {"青岛", "QINGDAO"},
    {"上海", "SHANGHAI"},      {"深圳", "SHENZHEN"},
    {"广州", "GUANGZHOU"},     {"厦门", "XIAMEN"},
    {"三亚", "SANYA"},         {"大连", "DALIAN"},
    {"烟台", "YANTAI"},        {"威海", "WEIHAI"},
    {"天津", "TIANJIN"},       {"宁波", "NINGBO"},
    {"福州", "FUZHOU"},        {"海口", "HAIKOU"},
    {"珠海", "ZHUHAI"},        {"北海", "BEIHAI"},
    {"秦皇岛", "QINHUANGDAO"}, {"连云港", "LIANYUNGANG"},
    {"温州", "WENZHOU"},       {"汕头", "SHANTOU"},
};

const char *locationAlias(const char *input) {
  if (input == nullptr) {
    return nullptr;
  }
  for (const LocationAlias &alias : kLocationAliases) {
    if (std::strstr(input, alias.utf8_name) != nullptr) {
      return alias.display_name;
    }
  }
  return nullptr;
}

void makeDisplayText(const char *input, const char *fallback,
                     char *destination, size_t destination_size,
                     size_t maximum_characters) {
  if (destination == nullptr || destination_size == 0U) {
    return;
  }
  destination[0] = '\0';

  const char *source = locationAlias(input);
  if (source == nullptr) {
    source = input;
  }

  size_t output = 0U;
  bool saw_non_ascii = false;
  if (source != nullptr) {
    for (size_t index = 0U; source[index] != '\0' &&
                            output < maximum_characters &&
                            output + 1U < destination_size;
         ++index) {
      const unsigned char byte = static_cast<unsigned char>(source[index]);
      if (byte >= 0x80U) {
        saw_non_ascii = true;
        continue;
      }
      char character = static_cast<char>(byte);
      if (character == '_') {
        character = ' ';
      }
      if (!isDrawableAscii(character)) {
        continue;
      }
      character = static_cast<char>(std::toupper(
          static_cast<unsigned char>(character)));
      if (character == ' ' &&
          (output == 0U || destination[output - 1U] == ' ')) {
        continue;
      }
      destination[output++] = character;
    }
  }
  while (output > 0U && destination[output - 1U] == ' ') {
    --output;
  }
  destination[output] = '\0';

  if (output == 0U || (saw_non_ascii && locationAlias(input) == nullptr)) {
    std::snprintf(destination, destination_size, "%.*s",
                  static_cast<int>(maximum_characters), fallback);
  }
}

const char *weatherLabel(const EnvironmentSnapshot &snapshot) {
  if (environmentHasValue(snapshot, kEnvironmentHasWeatherCode)) {
    const int code = snapshot.weather_code;
    if (code == 0) return "CLEAR";
    if (code == 1) return "MAINLY CLEAR";
    if (code == 2) return "PARTLY CLOUDY";
    if (code == 3) return "OVERCAST";
    if (code == 45 || code == 48) return "FOG";
    if (code >= 51 && code <= 57) return "DRIZZLE";
    if (code >= 61 && code <= 67) return "RAIN";
    if (code >= 71 && code <= 77) return "SNOW";
    if (code >= 80 && code <= 82) return "SHOWERS";
    if (code == 85 || code == 86) return "SNOW SHOWERS";
    if (code == 95) return "THUNDERSTORM";
    if (code == 96 || code == 99) return "THUNDER + HAIL";
  }
  return snapshot.weather;
}

bool isCoastalEnvironment(const EnvironmentSnapshot &snapshot) {
  if (snapshot.location_kind == EnvironmentLocationKind::kCoast) {
    return true;
  }
  if (snapshot.location_kind == EnvironmentLocationKind::kPlace) {
    return false;
  }
  return environmentHasValue(snapshot, kEnvironmentHasWaterTemperature) ||
         environmentHasValue(snapshot, kEnvironmentHasWaveHeight) ||
         environmentHasValue(snapshot, kEnvironmentHasSeaLevelHeight);
}

void formatMetric(char *destination, size_t destination_size, bool valid,
                  float value, unsigned int decimals) {
  if (!valid) {
    std::snprintf(destination, destination_size, "--");
    return;
  }
  std::snprintf(destination, destination_size, "%.*f",
                static_cast<int>(decimals), static_cast<double>(value));
}

void drawLoadingSpinner(uint16_t *buffer, int center_x, int center_y,
                        size_t phase) {
  // A bright head fading around a circular track reads as a loading spinner
  // without falling back to the old three-dot placeholder.
  constexpr int kInnerX[] = {0, 14, 23, 27, 23, 14,
                             0, -14, -23, -27, -23, -14};
  constexpr int kInnerY[] = {-27, -23, -14, 0, 14, 23,
                             27, 23, 14, 0, -14, -23};
  constexpr int kOuterX[] = {0, 22, 37, 43, 37, 22,
                             0, -22, -37, -43, -37, -22};
  constexpr int kOuterY[] = {-43, -37, -22, 0, 22, 37,
                             43, 37, 22, 0, -22, -37};
  constexpr uint16_t kColors[] = {
      kWhite,
      kCyan,
      rgb565(31, 178, 202),
      rgb565(28, 151, 177),
      rgb565(25, 126, 153),
      rgb565(23, 105, 133),
      rgb565(21, 86, 114),
      rgb565(19, 70, 96),
      rgb565(18, 59, 83),
      rgb565(17, 54, 77),
      rgb565(19, 70, 96),
      rgb565(24, 111, 139),
  };

  static_assert(arraySize(kInnerX) == arraySize(kInnerY),
                "spinner coordinates must match");
  static_assert(arraySize(kInnerX) == arraySize(kOuterX),
                "spinner coordinates must match");
  static_assert(arraySize(kInnerX) == arraySize(kOuterY),
                "spinner coordinates must match");
  static_assert(arraySize(kInnerX) == arraySize(kColors),
                "spinner colors must match");

  for (size_t index = 0U; index < arraySize(kInnerX); ++index) {
    const int x0 = center_x + kInnerX[index];
    const int y0 = center_y + kInnerY[index];
    const int x1 = center_x + kOuterX[index];
    const int y1 = center_y + kOuterY[index];
    const size_t color_index =
        (index + arraySize(kColors) - phase % arraySize(kColors)) %
        arraySize(kColors);
    const uint16_t color = kColors[color_index];
    for (int offset = -1; offset <= 1; ++offset) {
      drawLine(buffer, x0 + offset, y0, x1 + offset, y1, color);
      drawLine(buffer, x0, y0 + offset, x1, y1 + offset, color);
    }
  }
}

void drawWeatherIcon(uint16_t *buffer, const EnvironmentSnapshot &snapshot) {
  const bool has_code =
      environmentHasValue(snapshot, kEnvironmentHasWeatherCode);
  const int code = has_code ? snapshot.weather_code : 2;
  const bool cloudy = code >= 2;
  const bool precipitation = (code >= 51 && code <= 86) || code >= 95;

  if (code <= 2) {
    for (int ray = 0; ray < 8; ++ray) {
      constexpr int kRayX[] = {0, 35, 50, 35, 0, -35, -50, -35};
      constexpr int kRayY[] = {-50, -35, 0, 35, 50, 35, 0, -35};
      drawLine(buffer, 101 + kRayX[ray] * 3 / 4,
               224 + kRayY[ray] * 3 / 4, 101 + kRayX[ray],
               224 + kRayY[ray], kYellow);
    }
    fillCircle(buffer, 101, 224, 30, kYellow);
  }

  if (cloudy) {
    const uint16_t cloud_color = rgb565(207, 225, 237);
    fillCircle(buffer, 86, 222, 22, cloud_color);
    fillCircle(buffer, 112, 210, 29, cloud_color);
    fillCircle(buffer, 139, 224, 21, cloud_color);
    fillRect(buffer, 70, 220, 86, 30, cloud_color);
  }
  if (precipitation) {
    const uint16_t rain_color = rgb565(65, 174, 245);
    drawLine(buffer, 84, 260, 77, 279, rain_color);
    drawLine(buffer, 111, 260, 104, 279, rain_color);
    drawLine(buffer, 138, 260, 131, 279, rain_color);
  }
}

struct RegisterValue {
  uint16_t command;
  uint16_t value;
};

}  // namespace

bool IRAM_ATTR CoastalDisplay::onTransferDone(
    esp_lcd_panel_io_handle_t panel_io,
    esp_lcd_panel_io_event_data_t *event_data, void *user_context) {
  (void)panel_io;
  (void)event_data;
  CoastalDisplay *display = static_cast<CoastalDisplay *>(user_context);
  BaseType_t task_woken = pdFALSE;
  if (display != nullptr && display->transfer_done_ != nullptr) {
    xSemaphoreGiveFromISR(display->transfer_done_, &task_woken);
  }
  return task_woken == pdTRUE;
}

bool CoastalDisplay::begin() {
  Serial.println("[LCD] initializing TK043 NT35510 800x480 I80-8");

  transfer_done_ = xSemaphoreCreateBinary();
  if (transfer_done_ == nullptr) {
    Serial.println("[LCD] ERROR transfer semaphore allocation failed");
    return false;
  }

  framebuffer_ = static_cast<uint16_t *>(heap_caps_aligned_alloc(
      64, kFramebufferBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (framebuffer_ == nullptr) {
    Serial.println("[LCD] ERROR 768 KB PSRAM framebuffer allocation failed");
    return false;
  }
  partial_transfer_buffer_ = static_cast<uint16_t *>(heap_caps_aligned_alloc(
      64, kTransferBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (partial_transfer_buffer_ == nullptr) {
    Serial.println(
        "[LCD] WARN partial refresh buffer unavailable; loader is static");
  }

  gpio_config_t control_config{};
  control_config.pin_bit_mask =
      (1ULL << static_cast<unsigned>(kReadPin)) |
      (1ULL << static_cast<unsigned>(kResetPin));
  control_config.mode = GPIO_MODE_OUTPUT;
  control_config.pull_up_en = GPIO_PULLUP_DISABLE;
  control_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
  control_config.intr_type = GPIO_INTR_DISABLE;
  if (gpio_config(&control_config) != ESP_OK) {
    Serial.println("[LCD] ERROR control GPIO configuration failed");
    return false;
  }
  gpio_set_level(kReadPin, 1);

  esp_lcd_i80_bus_config_t bus_config{};
  bus_config.dc_gpio_num = kDataCommandPin;
  bus_config.wr_gpio_num = kWritePin;
  bus_config.clk_src = LCD_CLK_SRC_PLL160M;
  for (int &pin : bus_config.data_gpio_nums) {
    pin = GPIO_NUM_NC;
  }
  for (size_t index = 0; index < arraySize(kDataPins); ++index) {
    bus_config.data_gpio_nums[index] = kDataPins[index];
  }
  bus_config.bus_width = arraySize(kDataPins);
  bus_config.max_transfer_bytes = kTransferBytes;
  bus_config.psram_trans_align = 64;
  bus_config.sram_trans_align = 4;
  if (esp_lcd_new_i80_bus(&bus_config, &bus_) != ESP_OK) {
    Serial.println("[LCD] ERROR failed to create I80 bus");
    return false;
  }

  esp_lcd_panel_io_i80_config_t io_config{};
  io_config.cs_gpio_num = kChipSelectPin;
  io_config.pclk_hz = kPixelClockHz;
  io_config.trans_queue_depth = 1;
  io_config.on_color_trans_done = onTransferDone;
  io_config.user_ctx = this;
  io_config.lcd_cmd_bits = 16;
  io_config.lcd_param_bits = 16;
  io_config.dc_levels.dc_idle_level = 0;
  io_config.dc_levels.dc_cmd_level = 0;
  io_config.dc_levels.dc_dummy_level = 0;
  io_config.dc_levels.dc_data_level = 1;
  io_config.flags.swap_color_bytes = 1;
  if (esp_lcd_new_panel_io_i80(bus_, &io_config, &io_) != ESP_OK) {
    Serial.println("[LCD] ERROR failed to create I80 panel IO");
    return false;
  }

  gpio_set_level(kResetPin, 0);
  delay(20);
  gpio_set_level(kResetPin, 1);
  delay(120);

  if (!initializeController()) {
    Serial.println("[LCD] ERROR NT35510 initialization failed");
    return false;
  }

  ready_ = true;
  Serial.println("[LCD] controller ready");
  return true;
}

bool CoastalDisplay::writeRegister(uint16_t command, uint16_t value) {
  return io_ != nullptr &&
         esp_lcd_panel_io_tx_param(io_, command, &value, sizeof(value)) ==
             ESP_OK;
}

bool CoastalDisplay::writeCommand(uint16_t command) {
  return io_ != nullptr &&
         esp_lcd_panel_io_tx_param(io_, command, nullptr, 0) == ESP_OK;
}

bool CoastalDisplay::initializeController() {
  constexpr RegisterValue kPageSelect[] = {
      {0xF000, 0x55}, {0xF001, 0xAA}, {0xF002, 0x52},
      {0xF003, 0x08}, {0xF004, 0x01},
  };
  constexpr RegisterValue kBeforeGamma[] = {
      {0xB000, 0x0C}, {0xB001, 0x0C}, {0xB002, 0x0C},
      {0xB600, 0x46}, {0xB601, 0x46},
  };
  constexpr uint16_t kGamma[] = {
      0x00, 0x01, 0x00, 0x1C, 0x00, 0x4E, 0x00, 0x6A, 0x00,
      0x85, 0x00, 0xAB, 0x00, 0xC4, 0x00, 0xFC, 0x01, 0x23,
      0x01, 0x61, 0x01, 0x94, 0x01, 0xE4, 0x02, 0x27, 0x02,
      0x29, 0x02, 0x65, 0x02, 0xA6, 0x02, 0xCA, 0x02, 0xFD,
      0x03, 0x1D, 0x03, 0x4D, 0x03, 0x6A, 0x03, 0x95, 0x03,
      0xAC, 0x03, 0xCB, 0x03, 0xEA, 0x03, 0xEF,
  };
  static_assert(arraySize(kGamma) == 52, "unexpected NT35510 gamma table");
  constexpr RegisterValue kAfterGamma[] = {
      {0xBA00, 0x36}, {0xBA01, 0x36}, {0xBA02, 0x36},
      {0xB900, 0x26}, {0xB901, 0x26}, {0xB902, 0x26},
  };
  constexpr RegisterValue kFinal[] = {
      {0xB100, 0x0C}, {0xBC00, 0x00}, {0xBC01, 0x80},
      {0xBC02, 0x00}, {0xB800, 0x34}, {0xB801, 0x34},
      {0xB802, 0x34}, {0xB602, 0x46}, {0xB700, 0x26},
      {0xB701, 0x26}, {0xB702, 0x26}, {0xB200, 0x00},
      {0xB201, 0x00}, {0xB202, 0x00}, {0xBF00, 0x01},
      {0xB300, 0x08}, {0xB301, 0x08}, {0xB302, 0x08},
      {0xB500, 0x08}, {0xB501, 0x08}, {0xB502, 0x08},
      {0x3500, 0x00}, {0xB101, 0x0C}, {0xB102, 0x0C},
      {0xBD00, 0x00}, {0xBD01, 0x80}, {0xBD02, 0x00},
      {0xBE00, 0x00}, {0xBE01, 0x55}, {0x3600, 0xA3},
      {0x3A00, 0x55},
  };

  const auto write_sequence = [this](const RegisterValue *values,
                                     size_t count) {
    for (size_t index = 0; index < count; ++index) {
      if (!writeRegister(values[index].command, values[index].value)) {
        return false;
      }
    }
    return true;
  };

  if (!write_sequence(kPageSelect, arraySize(kPageSelect)) ||
      !write_sequence(kBeforeGamma, arraySize(kBeforeGamma))) {
    return false;
  }
  for (uint16_t bank = 0xD1; bank <= 0xD6; ++bank) {
    for (size_t offset = 0; offset < arraySize(kGamma); ++offset) {
      const uint16_t command =
          static_cast<uint16_t>((bank << 8U) | offset);
      if (!writeRegister(command, kGamma[offset])) {
        return false;
      }
    }
  }
  if (!write_sequence(kAfterGamma, arraySize(kAfterGamma)) ||
      !write_sequence(kPageSelect, arraySize(kPageSelect)) ||
      !write_sequence(kFinal, arraySize(kFinal))) {
    return false;
  }

  if (!writeCommand(0x1100)) {
    return false;
  }
  delay(500);
  if (!writeCommand(0x2900)) {
    return false;
  }
  delay(500);
  return true;
}

bool CoastalDisplay::setWindow(uint16_t x_start, uint16_t y_start,
                               uint16_t x_end, uint16_t y_end) {
  if (x_start >= x_end || y_start >= y_end) {
    return false;
  }
  const uint16_t x_last = static_cast<uint16_t>(x_end - 1U);
  const uint16_t y_last = static_cast<uint16_t>(y_end - 1U);
  return writeRegister(0x2A00, x_start >> 8U) &&
         writeRegister(0x2A01, x_start & 0xFFU) &&
         writeRegister(0x2A02, x_last >> 8U) &&
         writeRegister(0x2A03, x_last & 0xFFU) &&
         writeRegister(0x2B00, y_start >> 8U) &&
         writeRegister(0x2B01, y_start & 0xFFU) &&
         writeRegister(0x2B02, y_last >> 8U) &&
         writeRegister(0x2B03, y_last & 0xFFU);
}

bool CoastalDisplay::flushFramebuffer() {
  if (!ready_ || framebuffer_ == nullptr || io_ == nullptr) {
    return false;
  }
  for (int y = 0; y < kHeight; y += kTransferRows) {
    const int rows = std::min(kTransferRows, kHeight - y);
    if (!setWindow(0, static_cast<uint16_t>(y), kWidth,
                   static_cast<uint16_t>(y + rows))) {
      return false;
    }
    const uint16_t *stripe = framebuffer_ + static_cast<size_t>(y) * kWidth;
    const size_t bytes = static_cast<size_t>(rows) * kWidth * sizeof(uint16_t);
    if (esp_lcd_panel_io_tx_color(io_, 0x2C00, stripe, bytes) != ESP_OK) {
      return false;
    }
    if (xSemaphoreTake(transfer_done_, pdMS_TO_TICKS(2000)) != pdTRUE) {
      Serial.printf("[LCD] ERROR DMA timeout at row %d\n", y);
      return false;
    }
  }
  return true;
}

bool CoastalDisplay::flushRegion(int x, int y, int width, int height) {
  if (!ready_ || framebuffer_ == nullptr || partial_transfer_buffer_ == nullptr ||
      io_ == nullptr || width <= 0 || height <= 0 || x < 0 || y < 0 ||
      x + width > kWidth || y + height > kHeight) {
    return false;
  }

  const size_t bytes_per_row =
      static_cast<size_t>(width) * sizeof(uint16_t);
  if (bytes_per_row > kTransferBytes) {
    return false;
  }
  const int rows_per_transfer =
      std::max(1, static_cast<int>(kTransferBytes / bytes_per_row));
  for (int row_start = 0; row_start < height;
       row_start += rows_per_transfer) {
    const int rows = std::min(rows_per_transfer, height - row_start);
    for (int row = 0; row < rows; ++row) {
      const uint16_t *source =
          framebuffer_ +
          static_cast<size_t>(y + row_start + row) * kWidth + x;
      std::copy_n(source, width,
                  partial_transfer_buffer_ + static_cast<size_t>(row) * width);
    }
    if (!setWindow(static_cast<uint16_t>(x),
                   static_cast<uint16_t>(y + row_start),
                   static_cast<uint16_t>(x + width),
                   static_cast<uint16_t>(y + row_start + rows))) {
      return false;
    }
    const size_t bytes = static_cast<size_t>(rows) * bytes_per_row;
    if (esp_lcd_panel_io_tx_color(io_, 0x2C00, partial_transfer_buffer_,
                                  bytes) != ESP_OK) {
      return false;
    }
    if (xSemaphoreTake(transfer_done_, pdMS_TO_TICKS(2000)) != pdTRUE) {
      Serial.printf("[LCD] ERROR partial DMA timeout at row %d\n",
                    y + row_start);
      return false;
    }
  }
  return true;
}

bool CoastalDisplay::showNetworkStatus(bool wifi_connected,
                                       bool server_reachable,
                                       bool environment_reachable) {
  if (!ready_ || framebuffer_ == nullptr) {
    return false;
  }

  const char *badge = "WAITING DATA";
  const char *headline = "WAITING FOR WEATHER";
  uint16_t status_color = kYellow;
  if (!wifi_connected) {
    badge = "WIFI OFFLINE";
    headline = "WAITING FOR WIFI";
    status_color = kRed;
  } else if (!server_reachable) {
    badge = "SERVER OFFLINE";
    headline = "SERVER UNREACHABLE";
    status_color = kRed;
  } else if (environment_reachable) {
    badge = "DATA READY";
    headline = "WEATHER DATA READY";
    status_color = kGreen;
  }

  const char *wifi_value = wifi_connected ? "CONNECTED" : "OFFLINE";
  const char *server_value = !wifi_connected
                                 ? "WAITING WIFI"
                                 : (server_reachable ? "ONLINE"
                                                     : "UNREACHABLE");
  const char *weather_value = environment_reachable ? "READY" : "WAITING";

  std::fill_n(framebuffer_, static_cast<size_t>(kWidth) * kHeight,
              kBackground);

  fillRect(framebuffer_, 0, 0, kWidth, 68, kHeader);
  fillRect(framebuffer_, 0, 66, kWidth, 2, kCyan);
  drawText(framebuffer_, 30, 18, "WEATHER STATION", 4, kWhite);
  constexpr location_picker_ui::Rect kWifiButton{450, 15, 130, 38};
  drawPickerButton(framebuffer_, kWifiButton, "WIFI", true, false);
  constexpr int kBadgeX = 594;
  constexpr int kBadgeWidth = 176;
  fillRect(framebuffer_, kBadgeX, 17, kBadgeWidth, 34,
           rgb565(17, 63, 77));
  drawRect(framebuffer_, kBadgeX, 17, kBadgeWidth, 34, 2, status_color);
  const int badge_width = static_cast<int>(std::strlen(badge)) * 12;
  drawText(framebuffer_, kBadgeX + (kBadgeWidth - badge_width) / 2, 27,
           badge, 2, status_color);

  drawCard(framebuffer_, 28, 88, 744, 160, kCard);
  drawText(framebuffer_, 54, 108, "LIVE WEATHER STATUS", 2, kMuted);
  drawText(framebuffer_, 54, 145, headline, 4, status_color);
  drawText(framebuffer_, 54, 208, "NO DEFAULT MEASUREMENTS ARE SHOWN", 2,
           kCyan);

  drawCard(framebuffer_, 28, 270, 236, 138, kCardAlt);
  drawText(framebuffer_, 48, 291, "WIFI", 2, kMuted);
  drawText(framebuffer_, 48, 333, wifi_value, 3,
           wifi_connected ? kGreen : kRed);

  drawCard(framebuffer_, 282, 270, 236, 138, kCardAlt);
  drawText(framebuffer_, 302, 291, "SERVER", 2, kMuted);
  drawText(framebuffer_, 302, 333, server_value, 3,
           server_reachable ? kGreen
                            : (wifi_connected ? kRed : kYellow));

  drawCard(framebuffer_, 536, 270, 236, 138, kCardAlt);
  drawText(framebuffer_, 556, 291, "WEATHER DATA", 2, kMuted);
  drawText(framebuffer_, 556, 333, weather_value, 3,
           environment_reachable ? kGreen : kYellow);

  fillRect(framebuffer_, 0, 438, 800, 42, kHeader);
  drawText(framebuffer_, 34, 451,
           "TAP WIFI TO CONFIGURE - LIVE VALUES REQUIRE SERVER", 2,
           kWhite);

  if (!flushFramebuffer()) {
    Serial.println("[LCD] ERROR network status transfer failed");
    return false;
  }
  Serial.printf("[LCD] network status displayed wifi=%u server=%u data=%u\n",
                wifi_connected ? 1U : 0U, server_reachable ? 1U : 0U,
                environment_reachable ? 1U : 0U);
  return true;
}

bool CoastalDisplay::showEnvironment(const EnvironmentSnapshot &snapshot) {
  if (!ready_ || framebuffer_ == nullptr) {
    return false;
  }

  char location[24]{};
  char weather[20]{};
  char source[20]{};
  char temperature[12]{};
  char humidity[12]{};
  char wind[12]{};
  char wave[12]{};
  char water[12]{};
  const char *display_location = snapshot.display_location[0] != '\0'
                                     ? snapshot.display_location
                                     : snapshot.location;
  const bool coastal = isCoastalEnvironment(snapshot);
  const bool updating = std::strcmp(snapshot.weather, "UPDATING") == 0;
  const bool has_temperature =
      environmentHasValue(snapshot, kEnvironmentHasAirTemperature);
  const bool has_weather_code =
      environmentHasValue(snapshot, kEnvironmentHasWeatherCode);
  const bool loading_weather =
      updating && (!has_temperature || !has_weather_code);
  makeDisplayText(display_location,
                  coastal ? "SELECTED COAST" : "SELECTED PLACE", location,
                  sizeof(location), 21U);
  makeDisplayText(weatherLabel(snapshot), "WEATHER", weather,
                  sizeof(weather), 16U);
  makeDisplayText(updating ? "SERVER" : snapshot.source, "SERVER", source,
                  sizeof(source), 14U);
  formatMetric(temperature, sizeof(temperature),
               environmentHasValue(snapshot, kEnvironmentHasAirTemperature),
               snapshot.air_temperature_c, 1U);
  formatMetric(humidity, sizeof(humidity),
               environmentHasValue(snapshot, kEnvironmentHasHumidity),
               snapshot.humidity_percent, 0U);
  formatMetric(wind, sizeof(wind),
               environmentHasValue(snapshot, kEnvironmentHasWindSpeed),
               snapshot.wind_speed_kmh, 1U);
  formatMetric(wave, sizeof(wave),
               environmentHasValue(snapshot, kEnvironmentHasWaveHeight),
               snapshot.wave_height_m, 1U);
  formatMetric(water, sizeof(water),
               environmentHasValue(snapshot,
                                   kEnvironmentHasWaterTemperature),
               snapshot.water_temperature_c, 1U);

  std::fill_n(framebuffer_, static_cast<size_t>(kWidth) * kHeight,
              kBackground);

  fillRect(framebuffer_, 0, 0, kWidth, 68, kHeader);
  fillRect(framebuffer_, 0, 66, kWidth, 2, kCyan);
  drawText(framebuffer_, 30, 18,
           coastal ? "COAST WEATHER" : "LOCAL WEATHER", 4, kWhite);
  constexpr location_picker_ui::Rect kWifiButton{480, 15, 130, 38};
  drawPickerButton(framebuffer_, kWifiButton, "WIFI", true, false);
  const bool verified_live =
      !snapshot.stale && !updating &&
      std::strcmp(snapshot.source, "open-meteo") == 0;
  const uint16_t status_color = verified_live ? kGreen : kYellow;
  constexpr location_picker_ui::Rect kRiskButton{625, 15, 145, 38};
  drawPickerButton(framebuffer_, kRiskButton, "RISK", true, false);
  const char *status_text = updating
                                ? "UPDATING"
                                : (snapshot.stale
                                       ? "CACHED"
                                       : (verified_live ? "LIVE DATA"
                                                        : "RESEARCH DATA"));

  drawCard(framebuffer_, 28, 88, 314, 330, kCard);
  drawText(framebuffer_, 52, 105, "SELECTED AREA", 2, kMuted);
  drawText(framebuffer_, 52, 133, location, 2, kGreen);
  if (loading_weather) {
    loading_spinner_phase_ = 0U;
    drawLoadingSpinner(framebuffer_, kLoadingSpinnerCenterX,
                       kLoadingSpinnerCenterY, loading_spinner_phase_);
    drawText(framebuffer_, 165, 195, "LOADING", 3, kWhite);
    drawText(framebuffer_, 165, 236, "WEATHER DATA", 2, kCyan);
  } else {
    drawWeatherIcon(framebuffer_, snapshot);
    const int temperature_scale = std::strlen(temperature) <= 4U ? 6 : 5;
    drawText(framebuffer_, 151, 174, temperature, temperature_scale, kWhite);
    if (has_temperature) {
      const int number_width = static_cast<int>(std::strlen(temperature)) *
                               6 * temperature_scale;
      const int degree_x = std::min(307, 151 + number_width + 4);
      fillCircle(framebuffer_, degree_x, 178, 7, kWhite);
      fillCircle(framebuffer_, degree_x, 178, 3, kCard);
      drawText(framebuffer_, degree_x + 14, 189, "C", 3, kWhite);
    }
    drawText(framebuffer_, 151, 252, weather, 2, kCyan);
  }
  drawText(framebuffer_, 52, 342, "SOURCE", 2, kMuted);
  drawText(framebuffer_, 52, 373, source, 2,
           snapshot.stale ? kYellow : kGreen);

  drawCard(framebuffer_, 366, 88, 190, 154, kCardAlt);
  drawText(framebuffer_, 386, 108, "WIND", 2, kMuted);
  drawText(framebuffer_, 386, 145, wind, 4, kWhite);
  drawText(framebuffer_, 386, 196, "KM/H", 2, kCyan);

  drawCard(framebuffer_, 578, 88, 194, 154, kCardAlt);
  drawText(framebuffer_, 598, 108, "HUMIDITY", 2, kMuted);
  drawText(framebuffer_, 598, 145, humidity, 4, kWhite);
  if (environmentHasValue(snapshot, kEnvironmentHasHumidity)) {
    const int value_width = static_cast<int>(std::strlen(humidity)) * 24;
    drawText(framebuffer_, std::min(742, 598 + value_width + 8), 154, "%", 3,
             kCyan);
  }

  drawCard(framebuffer_, 366, 264, 190, 154, kCardAlt);
  drawText(framebuffer_, 386, 284, "WAVE", 2, kMuted);
  drawText(framebuffer_, 386, 321, wave, 4, kWhite);
  if (environmentHasValue(snapshot, kEnvironmentHasWaveHeight)) {
    drawText(framebuffer_, 500, 330, "M", 3, kCyan);
  } else if (!coastal) {
    drawText(framebuffer_, 386, 374, "COAST ONLY", 2, kMuted);
  }

  drawCard(framebuffer_, 578, 264, 194, 154, kCardAlt);
  drawText(framebuffer_, 598, 284, "WATER", 2, kMuted);
  drawText(framebuffer_, 598, 321, water, 4, kWhite);
  if (environmentHasValue(snapshot, kEnvironmentHasWaterTemperature)) {
    drawText(framebuffer_, 724, 330, "C", 3, kCyan);
  } else if (!coastal) {
    drawText(framebuffer_, 598, 374, "COAST ONLY", 2, kMuted);
  }

  fillRect(framebuffer_, 0, 438, 240, 42,
           snapshot.stale ? rgb565(98, 77, 27) : rgb565(11, 91, 69));
  fillRect(framebuffer_, 240, 438, 560, 42, kHeader);
  drawText(framebuffer_, 25, 451, status_text, 2, status_color);
  drawText(framebuffer_, 273, 451, "AREA: CARD  RISK: HEADER  WIFI: HEADER", 2,
           kWhite);

  if (!flushFramebuffer()) {
    Serial.println("[LCD] ERROR environment page transfer failed");
    return false;
  }
  Serial.printf("[LCD] environment displayed location='%s' stale=%u\n",
                snapshot.location, snapshot.stale ? 1U : 0U);
  return true;
}

bool CoastalDisplay::showRiskOverview(const RiskSnapshot &risk,
                                      const EnvironmentSnapshot &environment,
                                      const ModelCatalog &models,
                                      const TelemetrySnapshot &telemetry,
                                      uint8_t availability,
                                      int http_status) {
  if (!ready_ || framebuffer_ == nullptr) {
    return false;
  }

  // The ESP32 owns the deterministic local sensor state. The remote model is
  // informational only; its percentage is class confidence, never a disaster
  // occurrence probability and never an actuator command.
  constexpr uint8_t kRiskReady = 1U;
  constexpr uint8_t kRiskNoTelemetry = 2U;
  const bool ready = availability == kRiskReady && risk.fetched_at_ms != 0U;
  const bool no_telemetry = availability == kRiskNoTelemetry;

  char location[28]{};
  const char *display_location =
      environment.display_location[0] != '\0'
          ? environment.display_location
          : environment.location;
  makeDisplayText(display_location, "SELECTED COAST", location,
                  sizeof(location), 24U);

  const char *risk_name = "WAITING";
  const char *headline = "WAITING FOR MODEL DATA";
  uint16_t risk_color = kYellow;
  if (no_telemetry) {
    risk_name = "NO SENSOR DATA";
    headline = "WAITING FOR SENSOR";
  } else if (!ready) {
    risk_name = http_status > 0 ? "MODEL UNAVAILABLE" : "CONNECTING";
    headline = "RESEARCH CHANNEL OFFLINE";
    risk_color = kRed;
  } else {
    switch (risk.risk_level) {
      case 0U:
        risk_name = "LOW RISK";
        risk_color = kGreen;
        break;
      case 1U:
        risk_name = "ADVISORY";
        risk_color = kYellow;
        break;
      case 2U:
        risk_name = "WARNING";
        risk_color = rgb565(255, 137, 61);
        break;
      case 3U:
      default:
        risk_name = "CRITICAL";
        risk_color = kRed;
        break;
    }
    headline = risk_name;
  }

  char confidence[12]{"--"};
  char horizon[16]{"--"};
  char local_alarm[12]{"--"};
  char wave[12]{"--"};
  char wind[12]{"--"};
  if (ready) {
    const int percent = std::max(
        0, std::min(100, static_cast<int>(
                             risk.environmental_probability * 100.0F + 0.5F)));
    std::snprintf(confidence, sizeof(confidence), "%d%%", percent);
    std::snprintf(horizon, sizeof(horizon), "%u HOURS",
                  static_cast<unsigned>(risk.forecast_horizon_hours));
  }
  // Use the locally generated frame for the local alarm card even when the
  // research API is offline. A server response must never replace or gate the
  // device-local safety result.
  const bool local_alarm_available = telemetry.telemetry_fresh;
  const uint8_t local_alarm_level =
      local_alarm_available ? telemetry.latest.alarm_level : 4U;
  if (local_alarm_available) {
    if (local_alarm_level == 4U) {
      std::snprintf(local_alarm, sizeof(local_alarm), "FAULT");
    } else {
      std::snprintf(local_alarm, sizeof(local_alarm), "LEVEL %u",
                    static_cast<unsigned>(local_alarm_level));
    }
  }
  formatMetric(wave, sizeof(wave),
               environmentHasValue(environment, kEnvironmentHasWaveHeight),
               environment.wave_height_m, 1U);
  formatMetric(wind, sizeof(wind),
               environmentHasValue(environment, kEnvironmentHasWindSpeed),
               environment.wind_speed_kmh, 1U);

  char relative_level[20]{"--"};
  char ultrasonic_detail[28]{};
  if (!telemetry.has_telemetry) {
    std::snprintf(ultrasonic_detail, sizeof(ultrasonic_detail),
                  "NO SENSOR DATA");
  } else if (!telemetry.telemetry_fresh) {
    std::snprintf(ultrasonic_detail, sizeof(ultrasonic_detail),
                  "SENSOR RUNTIME OFFLINE");
  } else if (!telemetry.ultrasonic_available) {
    std::snprintf(ultrasonic_detail, sizeof(ultrasonic_detail),
                  "SEARCHING ECHO");
  } else {
    const ultrasonic_ui::Presentation values =
        ultrasonic_ui::presentation(telemetry.latest);
    std::snprintf(relative_level, sizeof(relative_level), "%+ld MM",
                  static_cast<long>(values.level_change_mm));
    std::snprintf(ultrasonic_detail, sizeof(ultrasonic_detail),
                  "SENSOR GAP %lu MM",
                  static_cast<unsigned long>(values.sensor_gap_mm));
  }

  const bool research_shadow =
      ready && std::strcmp(risk.deployment_mode, "shadow") == 0;
  const bool degraded = ready && (risk.degraded || risk.stale);
  const char *quality = !ready ? "NO RESULT" : risk.data_quality;
  const char *mode = !ready ? "RESEARCH ONLY" : risk.deployment_mode;
  const ModelOption *selected_model =
      findModel(models, models.selected_model_id);
  char selected_model_name[20]{};
  makeDisplayText(selected_model != nullptr ? selected_model->display_name
                                            : models.selected_model_id,
                  "MODEL", selected_model_name, sizeof(selected_model_name),
                  15U);
  char model[30]{};
  makeDisplayText(ready ? risk.model_version : models.selected_model_id,
                  "NO MODEL RESULT", model, sizeof(model), 25U);

  std::fill_n(framebuffer_, static_cast<size_t>(kWidth) * kHeight,
              kBackground);
  fillRect(framebuffer_, 0, 0, kWidth, 68, kHeader);
  fillRect(framebuffer_, 0, 66, kWidth, 2, kCyan);
  drawText(framebuffer_, 28, 14, "COASTWATCH", 4, kWhite);
  drawText(framebuffer_, 244, 15, "RESEARCH", 2, kCyan);
  drawText(framebuffer_, 244, 38, selected_model_name, 1, kWhite);
  constexpr location_picker_ui::Rect kModelsButton{450, 15, 110, 38};
  constexpr location_picker_ui::Rect kWeatherButton{574, 15, 92, 38};
  constexpr location_picker_ui::Rect kWifiButton{680, 15, 92, 38};
  drawPickerButton(framebuffer_, kModelsButton, "MODELS", true, true);
  drawPickerButton(framebuffer_, kWeatherButton, "WEATHER", true, false);
  drawPickerButton(framebuffer_, kWifiButton, "WIFI", true, false);

  drawCard(framebuffer_, 28, 88, 342, 330, kCard);
  drawText(framebuffer_, 50, 108, location, 2, kGreen);
  drawText(framebuffer_, 50, 142, "NEXT 6H CONDITION", 2, kMuted);
  drawText(framebuffer_, 50, 176, headline,
           std::strlen(headline) <= 18U ? 3 : 2, risk_color);
  fillCircle(framebuffer_, 199, 277, 88, rgb565(10, 31, 52));
  for (int offset = 0; offset < 4; ++offset) {
    drawLine(framebuffer_, 199 - 66 - offset, 277 - 59,
             199 + 66 + offset, 277 - 59, risk_color);
    drawLine(framebuffer_, 199 + 66 + offset, 277 - 59,
             199 + 86, 277, risk_color);
    drawLine(framebuffer_, 199 + 86, 277,
             199 + 66 + offset, 277 + 59, risk_color);
    drawLine(framebuffer_, 199 + 66 + offset, 277 + 59,
             199 - 66 - offset, 277 + 59, risk_color);
    drawLine(framebuffer_, 199 - 66 - offset, 277 + 59,
             199 - 86, 277, risk_color);
    drawLine(framebuffer_, 199 - 86, 277,
             199 - 66 - offset, 277 - 59, risk_color);
  }
  drawCenteredText(framebuffer_, 111, 232, 176, 52, confidence,
                   ready ? 6 : 4, risk_color);
  drawCenteredText(framebuffer_, 111, 294, 176, 30, "MODEL CONFIDENCE", 2,
                   kMuted);
  drawCenteredText(framebuffer_, 50, 380, 298, 24,
                   ready ? horizon : "NOT A DISASTER PROBABILITY", 2,
                   ready ? kCyan : kYellow);

  drawCard(framebuffer_, 392, 88, 176, 142, kCardAlt);
  drawText(framebuffer_, 410, 107, "WAVE", 2, kMuted);
  drawText(framebuffer_, 410, 142, wave, 4, kWhite);
  drawText(framebuffer_, 514, 151, "M", 2, kCyan);
  if (environmentHasValue(environment, kEnvironmentHasWavePeriod)) {
    char period[24]{};
    std::snprintf(period, sizeof(period), "PERIOD %.1F S",
                  static_cast<double>(environment.wave_period_s));
    drawText(framebuffer_, 410, 198, period, 2, kMuted);
  }

  drawCard(framebuffer_, 590, 88, 182, 142, kCardAlt);
  drawText(framebuffer_, 608, 107, "WIND", 2, kMuted);
  drawText(framebuffer_, 608, 142, wind, 4, kWhite);
  drawText(framebuffer_, 608, 198, "KM/H", 2, kCyan);

  drawCard(framebuffer_, 392, 252, 176, 166, kCardAlt);
  drawText(framebuffer_, 410, 271, ultrasonic_ui::kLevelChangeLabel, 2,
           kMuted);
  drawText(framebuffer_, 410, 306, relative_level, 3,
           telemetry.ultrasonic_available ? kWhite : kRed);
  drawText(framebuffer_, 410, 360, ultrasonic_detail, 1,
           telemetry.ultrasonic_available ? kCyan : kRed);

  drawCard(framebuffer_, 590, 252, 182, 166, kCardAlt);
  drawText(framebuffer_, 608, 271, "LOCAL SENSOR", 2, kMuted);
  drawText(framebuffer_, 608, 306, local_alarm, 3,
           local_alarm_available && local_alarm_level > 1U ? kRed : kWhite);
  drawText(framebuffer_, 608, 352, "QUALITY", 2, kMuted);
  drawText(framebuffer_, 608, 382, quality, 2,
           degraded ? kYellow : (ready ? kGreen : kMuted));

  fillRect(framebuffer_, 0, 438, 800, 42,
           research_shadow ? rgb565(73, 47, 13) : kHeader);
  drawText(framebuffer_, 18, 451,
           research_shadow ? "SHADOW / RESEARCH" : mode, 2,
           research_shadow ? kYellow : kCyan);
  drawText(framebuffer_, 246, 451, model, 2, kWhite);
  drawText(framebuffer_, 650, 451, "LOCAL FIRST", 2, kGreen);

  if (!flushFramebuffer()) {
    Serial.println("[LCD] ERROR risk overview transfer failed");
    return false;
  }
  Serial.printf(
      "[LCD] risk overview displayed ready=%u class=%s quality=%s "
      "ultrasonic=%u level_change=%ld sensor_gap=%lu\n",
      ready ? 1U : 0U, risk_name, quality,
      telemetry.ultrasonic_available ? 1U : 0U,
      static_cast<long>(telemetry.latest.water_rise_mm),
      static_cast<unsigned long>(telemetry.latest.distance_mm));
  return true;
}

bool CoastalDisplay::showModelCatalog(const ModelCatalog &catalog) {
  if (!ready_ || framebuffer_ == nullptr) {
    return false;
  }

  std::fill_n(framebuffer_, static_cast<size_t>(kWidth) * kHeight,
              kBackground);
  fillRect(framebuffer_, 0, 0, kWidth, 68, kHeader);
  fillRect(framebuffer_, 0, 66, kWidth, 2, kCyan);
  drawModelButton(framebuffer_, model_ui::kBackButton, "BACK", true, false);
  drawText(framebuffer_, 164, 16, "MODEL LIBRARY", 4, kWhite);
  drawModelButton(framebuffer_, model_ui::kCollectionButton, "COLLECTION",
                  true, true);

  for (size_t index = 0U; index < model_ui::kCardCount; ++index) {
    const model_ui::Rect bounds = model_ui::cardRect(index);
    const bool present = index < catalog.count;
    const ModelOption *model = present ? &catalog.models[index] : nullptr;
    const bool selected =
        present && std::strcmp(catalog.selected_model_id, model->model_id) == 0;
    const bool target_matches =
        present && std::strcmp(catalog.pending_model_id, model->model_id) == 0;
    const bool pending =
        target_matches && catalog.state == ModelCatalogState::kSelecting;
    const bool selection_failed =
        target_matches && catalog.state == ModelCatalogState::kError;
    const bool selectable = present && modelStatusSelectable(model->status);
    const uint16_t border = selection_failed
                                ? kRed
                                : (selected ? kGreen
                                            : (selectable ? kCyan : kMuted));
    fillRect(framebuffer_, bounds.x, bounds.y, bounds.width, bounds.height,
             selection_failed
                 ? rgb565(67, 24, 35)
                 : (selected ? rgb565(12, 58, 66) : kCard));
    drawRect(framebuffer_, bounds.x, bounds.y, bounds.width, bounds.height,
             selected ? 4 : 2, border);

    char number[12]{};
    std::snprintf(number, sizeof(number), "MODEL %u",
                  static_cast<unsigned>(index + 1U));
    drawText(framebuffer_, bounds.x + 16, bounds.y + 16, number, 2, kMuted);
    if (!present) {
      drawCenteredText(framebuffer_, bounds.x, bounds.y + 92, bounds.width,
                       60, catalog.state == ModelCatalogState::kLoading
                               ? "LOADING"
                               : "NO MODEL",
                       3, kMuted);
      continue;
    }

    char display_name[24]{};
    char model_id[36]{};
    char mode[22]{};
    char description[38]{};
    makeDisplayText(model->display_name, "MODEL", display_name,
                    sizeof(display_name), 18U);
    makeDisplayText(model->model_id, "UNKNOWN", model_id, sizeof(model_id),
                    33U);
    makeDisplayText(model->mode, "RESEARCH", mode, sizeof(mode), 18U);
    makeDisplayText(model->description, "RESEARCH MODEL", description,
                    sizeof(description), 35U);
    const uint16_t status_color =
        model->status == ModelStatus::kReady
            ? kGreen
            : (model->status == ModelStatus::kNotTrained ? kYellow : kRed);
    drawText(framebuffer_, bounds.x + 16, bounds.y + 52, display_name, 2,
             kWhite);
    drawText(framebuffer_, bounds.x + 16, bounds.y + 91, model_id, 1, kMuted);
    drawText(framebuffer_, bounds.x + 16, bounds.y + 124,
             modelStatusName(model->status), 2, status_color);
    drawText(framebuffer_, bounds.x + 16, bounds.y + 158, mode, 2, kCyan);
    drawText(framebuffer_, bounds.x + 16, bounds.y + 194, description, 1,
             kWhite);
    const char *action = selection_failed
                             ? "SELECT FAILED - RETRY"
                             : (pending
                                    ? "SELECTING"
                                    : (selected ? "SELECTED"
                                                : (selectable
                                                       ? "TAP TO SELECT"
                                                       : "NOT SELECTABLE")));
    drawCenteredText(framebuffer_, bounds.x + 12, bounds.y + 238,
                     bounds.width - 24, 32, action, 2,
                     selection_failed
                         ? kRed
                         : (selected ? kGreen
                                     : (selectable ? kCyan : kMuted)));
  }

  char state_line[96]{};
  if (catalog.state == ModelCatalogState::kError &&
      catalog.pending_model_id[0] != '\0') {
    std::snprintf(state_line, sizeof(state_line),
                  "SELECTION FAILED HTTP %d - TAP RED CARD TO RETRY",
                  catalog.http_status);
  } else if (catalog.state == ModelCatalogState::kError) {
    std::snprintf(state_line, sizeof(state_line),
                  "MODEL SERVICE ERROR HTTP %d - REOPEN TO RETRY",
                  catalog.http_status);
  } else if (catalog.state == ModelCatalogState::kSelecting) {
    std::snprintf(state_line, sizeof(state_line),
                  "SERVER IS CHANGING THE SELECTED MODEL");
  } else if (catalog.state == ModelCatalogState::kLoading) {
    std::snprintf(state_line, sizeof(state_line),
                  "LOADING MODELS FROM SERVER");
  } else {
    std::snprintf(state_line, sizeof(state_line),
                  "READY MODELS ONLY - PREDICTION RUNS ON SERVER");
  }
  drawCenteredText(framebuffer_, 28, 395, 744, 28, state_line, 2,
                   catalog.state == ModelCatalogState::kError ? kRed : kMuted);
  fillRect(framebuffer_, 0, 438, 800, 42, rgb565(73, 47, 13));
  drawText(framebuffer_, 18, 451, "SIMULATION / RESEARCH", 2, kYellow);
  drawText(framebuffer_, 330, 451, "SERVER MODEL DISPLAY", 2, kWhite);
  drawText(framebuffer_, 650, 451, "LOCAL FIRST", 2, kGreen);

  if (!flushFramebuffer()) {
    Serial.println("[LCD] ERROR model catalog transfer failed");
    return false;
  }
  Serial.printf("[LCD] models displayed count=%u selected=%s state=%u\n",
                static_cast<unsigned>(catalog.count),
                catalog.selected_model_id,
                static_cast<unsigned>(catalog.state));
  return true;
}

bool CoastalDisplay::showSimulationCollection(
    const SimulationSnapshot &simulation, const ModelCatalog &models,
    bool stop_confirmation_pending) {
  if (!ready_ || framebuffer_ == nullptr) {
    return false;
  }

  const bool open = simulationSessionOpen(simulation.state);
  const bool can_start = simulationCanStart(simulation.state);
  const bool can_stop = simulationCanStop(simulation.state);
  const bool busy = simulation.state == SimulationState::kStarting ||
                    simulation.state == SimulationState::kStopping;
  const char *button_label = "START";
  if (simulation.state == SimulationState::kStarting) {
    button_label = "STARTING";
  } else if (simulation.state == SimulationState::kStopping) {
    button_label = "STOPPING";
  } else if (can_stop) {
    button_label = stop_confirmation_pending
                       ? "CONFIRM STOP"
                       : (simulation.state == SimulationState::kStopFailed
                              ? "RETRY STOP"
                              : "STOP");
  } else if (simulation.state == SimulationState::kStartFailed) {
    button_label = "RETRY START";
  }
  const bool button_enabled = open ? can_stop : can_start;

  const uint32_t now_ms = millis();
  const SimulationUltrasonicQuality ultrasonic_quality =
      simulationUltrasonicQuality(simulation, now_ms,
                                  kCollectionTelemetryMaximumAgeMs);
  const bool ultrasonic_valid =
      ultrasonic_quality == SimulationUltrasonicQuality::kValid;

  char level_change[20]{"--"};
  char sensor_gap[20]{"--"};
  char rise_rate[20]{"--"};
  // Two full uint32 values plus '/' and the NUL terminator fit without
  // truncation even after multi-year uptime.
  char local_sample_quality[24]{};
  if (ultrasonic_valid) {
    const ultrasonic_ui::Presentation values =
        ultrasonic_ui::presentation(simulation.latest);
    std::snprintf(level_change, sizeof(level_change), "%+ld MM",
                  static_cast<long>(values.level_change_mm));
    std::snprintf(sensor_gap, sizeof(sensor_gap), "%lu MM",
                  static_cast<unsigned long>(values.sensor_gap_mm));
    std::snprintf(rise_rate, sizeof(rise_rate), "%+ld MM/S",
                  static_cast<long>(simulation.latest.rise_rate_mm_s));
  }
  std::snprintf(local_sample_quality, sizeof(local_sample_quality), "%lu/%lu",
                static_cast<unsigned long>(
                    simulation.local_valid_ultrasonic_sample_count),
                static_cast<unsigned long>(simulation.local_tel_sample_count));

  const ModelOption *selected = findModel(models, models.selected_model_id);
  char selected_name[32]{};
  makeDisplayText(selected != nullptr ? selected->display_name
                                      : models.selected_model_id,
                  "NO MODEL SELECTED", selected_name, sizeof(selected_name),
                  28U);
  char session_id[42]{};
  makeDisplayText(simulation.session_id, "NO SESSION", session_id,
                  sizeof(session_id), 38U);

  std::fill_n(framebuffer_, static_cast<size_t>(kWidth) * kHeight,
              kBackground);
  fillRect(framebuffer_, 0, 0, kWidth, 68, kHeader);
  fillRect(framebuffer_, 0, 66, kWidth, 2, kCyan);
  drawModelButton(framebuffer_, model_ui::kBackButton, "BACK", true, false);
  drawText(framebuffer_, 164, 16, "DATA COLLECTION", 4, kWhite);
  drawModelButton(framebuffer_, model_ui::kSessionButton, button_label,
                  button_enabled && !busy, !open, open);

  const int card_x[] = {28, 220, 412, 604};
  const char *labels[] = {ultrasonic_ui::kLevelChangeLabel,
                          ultrasonic_ui::kSensorGapLabel, "ULTRASONIC",
                          "LOCAL VALID/FRAMES"};
  const char *values[] = {
      level_change, sensor_gap,
      simulationUltrasonicQualityName(ultrasonic_quality),
      local_sample_quality};
  for (size_t index = 0U; index < 4U; ++index) {
    drawCard(framebuffer_, card_x[index], 94, 168, 128,
             index == 0U ? kCard : kCardAlt);
    if (index == 0U) {
      drawRect(framebuffer_, card_x[index], 94, 168, 128, 2, kCyan);
    }
    const int label_scale = std::strlen(labels[index]) > 12U ? 1 : 2;
    drawText(framebuffer_, card_x[index] + 16,
             label_scale == 1 ? 116 : 112, labels[index], label_scale,
             index == 0U ? kCyan : kMuted);
    const size_t value_length = std::strlen(values[index]);
    const int value_scale =
        value_length <= 7U ? 3 : (value_length <= 12U ? 2 : 1);
    uint16_t value_color = index == 0U ? kCyan : kWhite;
    if (index < 2U && !ultrasonic_valid) {
      value_color = kMuted;
    } else if (index == 2U) {
      value_color = ultrasonic_valid
                        ? kGreen
                        : (ultrasonic_quality ==
                                   SimulationUltrasonicQuality::kWaiting
                               ? kMuted
                               : kRed);
    }
    drawCenteredText(framebuffer_, card_x[index] + 8, 150, 152, 48,
                     values[index], value_scale, value_color);
  }

  drawCard(framebuffer_, 28, 244, 744, 166, kCard);
  const char *headline =
      simulation.has_telemetry ? simulationStateName(simulation.state)
                               : (open ? "WAITING SENSOR"
                                       : simulationStateName(simulation.state));
  const uint16_t state_color =
      simulation.state == SimulationState::kActive
          ? kGreen
          : ((simulation.state == SimulationState::kStartFailed ||
              simulation.state == SimulationState::kStopFailed)
                 ? kRed
                 : kYellow);
  drawText(framebuffer_, 50, 264, headline, 4, state_color);
  drawText(framebuffer_, 50, 311, "SESSION", 2, kMuted);
  drawText(framebuffer_, 158, 311, session_id, 2, kWhite);
  drawText(framebuffer_, 50, 345, "PREDICTION MODEL", 2, kMuted);
  drawText(framebuffer_, 254, 345, selected_name, 2, kCyan);
  if (stop_confirmation_pending && can_stop) {
    drawText(framebuffer_, 50, 380,
             "STOP WILL CLOSE THIS SESSION - TAP CONFIRM STOP",
             1, kYellow);
  } else if (simulation.state == SimulationState::kStopping) {
    drawText(framebuffer_, 50, 380,
             "WAITING FOR SERVER TO CLOSE SESSION - DATA KEPT SAFE",
             1, kYellow);
  } else if (simulation.state == SimulationState::kStarting) {
    drawText(framebuffer_, 50, 380,
             "WAITING FOR SERVER TO OPEN SESSION - DO NOT TAP AGAIN",
             1, kYellow);
  } else if (simulation.state == SimulationState::kStopFailed) {
    char error[64]{};
    std::snprintf(error, sizeof(error),
                  "STOP FAILED HTTP %d - SESSION REMAINS OPEN - RETRY STOP",
                  simulation.http_status);
    drawText(framebuffer_, 50, 380, error, 1, kRed);
  } else if (simulation.state == SimulationState::kStartFailed) {
    char error[52]{};
    std::snprintf(error, sizeof(error),
                  "START FAILED HTTP %d - TAP START TO RETRY",
                  simulation.http_status);
    drawText(framebuffer_, 50, 380, error, 1, kRed);
  } else {
    char status_line[110]{};
    uint16_t status_color = kMuted;
    if (!open) {
      std::snprintf(status_line, sizeof(status_line),
                    "START CREATES A SERVER SESSION - LABELS STAY ON WEBSITE");
    } else if (!simulation.has_upload_ack) {
      std::snprintf(status_line, sizeof(status_line),
                    "SERVER STORED@SYNC %lu | WAITING FIRST UPLOAD ACK | RATE %s",
                    static_cast<unsigned long>(
                        simulation.server_stored_sample_count),
                    rise_rate);
      status_color = kYellow;
    } else {
      const bool upload_stale =
          static_cast<uint32_t>(now_ms - simulation.last_upload_ack_ms) >
          kCollectionUploadFreshnessMs;
      if (!simulation.last_upload_ack_succeeded) {
        std::snprintf(
            status_line, sizeof(status_line),
            "SERVER STORED@SYNC %lu | ACK FAIL HTTP %d | OK %lu FAIL %lu | RETRY",
            static_cast<unsigned long>(
                simulation.server_stored_sample_count),
            simulation.last_upload_ack_http_status,
            static_cast<unsigned long>(simulation.upload_ack_success_count),
            static_cast<unsigned long>(simulation.upload_ack_failure_count));
        status_color = kRed;
      } else if (upload_stale) {
        std::snprintf(
            status_line, sizeof(status_line),
            "SERVER STORED@SYNC %lu | ACK DELAYED HTTP %d SEQ %lu | OK %lu FAIL %lu",
            static_cast<unsigned long>(
                simulation.server_stored_sample_count),
            simulation.last_upload_ack_http_status,
            static_cast<unsigned long>(simulation.last_upload_ack_seq),
            static_cast<unsigned long>(simulation.upload_ack_success_count),
            static_cast<unsigned long>(simulation.upload_ack_failure_count));
        status_color = kYellow;
      } else {
        std::snprintf(
            status_line, sizeof(status_line),
            "SERVER STORED@SYNC %lu | ACK OK HTTP %d SEQ %lu | OK %lu FAIL %lu | RATE %s",
            static_cast<unsigned long>(
                simulation.server_stored_sample_count),
            simulation.last_upload_ack_http_status,
            static_cast<unsigned long>(simulation.last_upload_ack_seq),
            static_cast<unsigned long>(simulation.upload_ack_success_count),
            static_cast<unsigned long>(simulation.upload_ack_failure_count),
            rise_rate);
        status_color = kGreen;
      }
    }
    drawText(framebuffer_, 50, 380, status_line, 1, status_color);
  }

  fillRect(framebuffer_, 0, 438, 800, 42, rgb565(73, 47, 13));
  drawText(framebuffer_, 18, 451, "SIMULATION / RESEARCH", 2, kYellow);
  drawText(framebuffer_, 330, 451, "LABELS: WEBSITE ONLY", 2, kWhite);
  drawText(framebuffer_, 650, 451, "LOCAL FIRST", 2, kGreen);

  if (!flushFramebuffer()) {
    Serial.println("[LCD] ERROR collection page transfer failed");
    return false;
  }
  Serial.printf(
      "[LCD] collection displayed state=%s server_sync=%lu local_tel=%lu "
      "local_valid=%lu ack_ok=%lu ack_fail=%lu confirm=%u\n",
                simulationStateName(simulation.state),
                static_cast<unsigned long>(
                    simulation.server_stored_sample_count),
                static_cast<unsigned long>(simulation.local_tel_sample_count),
                static_cast<unsigned long>(
                    simulation.local_valid_ultrasonic_sample_count),
                static_cast<unsigned long>(
                    simulation.upload_ack_success_count),
                static_cast<unsigned long>(
                    simulation.upload_ack_failure_count),
                stop_confirmation_pending ? 1U : 0U);
  return true;
}

bool CoastalDisplay::animateEnvironmentLoading() {
  if (!ready_ || framebuffer_ == nullptr ||
      partial_transfer_buffer_ == nullptr) {
    return false;
  }
  loading_spinner_phase_ = (loading_spinner_phase_ + 1U) % 12U;
  fillRect(framebuffer_, kLoadingSpinnerRegionX, kLoadingSpinnerRegionY,
           kLoadingSpinnerRegionWidth, kLoadingSpinnerRegionHeight, kCard);
  drawLoadingSpinner(framebuffer_, kLoadingSpinnerCenterX,
                     kLoadingSpinnerCenterY, loading_spinner_phase_);
  return flushRegion(kLoadingSpinnerRegionX, kLoadingSpinnerRegionY,
                     kLoadingSpinnerRegionWidth,
                     kLoadingSpinnerRegionHeight);
}

bool CoastalDisplay::showLocationPicker(const LocationCatalog &catalog,
                                        size_t page, int selected_index) {
  if (!ready_ || framebuffer_ == nullptr) {
    return false;
  }

  const size_t count =
      std::min(catalog.count, static_cast<size_t>(kLocationCatalogCapacity));
  const size_t page_count =
      std::max<size_t>(1U, (count + location_picker_ui::kPageSize - 1U) /
                               location_picker_ui::kPageSize);
  const size_t visible_page = std::min(page, page_count - 1U);
  const bool busy = catalog.state == LocationCatalogState::kLoading ||
                    catalog.state == LocationCatalogState::kSaving;
  const bool selection_valid =
      selected_index >= 0 && static_cast<size_t>(selected_index) < count;
  const bool coastal_catalog =
      count > 0U &&
      std::all_of(catalog.options, catalog.options + count,
                  [](const LocationOption &option) {
                    return option.is_coastal;
                  });

  std::fill_n(framebuffer_, static_cast<size_t>(kWidth) * kHeight,
              kBackground);

  fillRect(framebuffer_, 0, 0, kWidth, 64, kHeader);
  fillRect(framebuffer_, 0, 62, kWidth, 2, kCyan);
  drawText(framebuffer_, 28, 16,
           coastal_catalog ? "SELECT COAST" : "SELECT PLACE", 4, kWhite);

  drawPickerButton(framebuffer_, location_picker_ui::kSearchButton, "SEARCH",
                   !busy, false);

  char page_text[12]{};
  std::snprintf(page_text, sizeof(page_text), "%u/%u",
                 static_cast<unsigned>(visible_page + 1U),
                 static_cast<unsigned>(page_count));
  drawText(framebuffer_, 548, 25, page_text, 2, kMuted);

  const char *state_label = catalogStateLabel(catalog.state);
  const uint16_t state_color = catalogStateColor(catalog.state);
  constexpr location_picker_ui::Rect kStateBadge{620, 15, 150, 34};
  fillRect(framebuffer_, kStateBadge.x, kStateBadge.y, kStateBadge.width,
           kStateBadge.height, rgb565(17, 50, 65));
  drawRect(framebuffer_, kStateBadge.x, kStateBadge.y, kStateBadge.width,
           kStateBadge.height, 2, state_color);
  const int state_width = static_cast<int>(std::strlen(state_label)) * 12;
  drawText(framebuffer_,
           kStateBadge.x + (kStateBadge.width - state_width) / 2,
           kStateBadge.y + 10, state_label, 2, state_color);

  for (size_t slot = 0U; slot < location_picker_ui::kPageSize; ++slot) {
    const size_t option_index =
        visible_page * location_picker_ui::kPageSize + slot;
    if (option_index >= count) {
      continue;
    }

    const location_picker_ui::Rect bounds =
        location_picker_ui::cardRect(slot);
    const bool selected =
        selection_valid && static_cast<size_t>(selected_index) == option_index;
    drawCard(framebuffer_, bounds.x, bounds.y, bounds.width, bounds.height,
             selected ? rgb565(15, 75, 82) : kCard);
    if (selected) {
      drawRect(framebuffer_, bounds.x, bounds.y, bounds.width, bounds.height,
               4, kGreen);
    }

    char ordinal[4]{};
    std::snprintf(ordinal, sizeof(ordinal), "%02u",
                  static_cast<unsigned>(option_index + 1U));
    drawText(framebuffer_, bounds.x + 14, bounds.y + 26, ordinal, 2,
             selected ? kGreen : kMuted);

    char display_name[33]{};
    char first_line[25]{};
    char second_line[25]{};
    makeDisplayText(catalog.options[option_index].display_location,
                    "UNNAMED REGION", display_name, sizeof(display_name),
                    32U);
    splitPickerLabel(display_name, first_line, sizeof(first_line), second_line,
                     sizeof(second_line));
    const int first_y = second_line[0] == '\0' ? bounds.y + 26 : bounds.y + 11;
    drawText(framebuffer_, bounds.x + 52, first_y, first_line, 2, kWhite);
    if (second_line[0] != '\0') {
      drawText(framebuffer_, bounds.x + 52, bounds.y + 40, second_line, 2,
               kCyan);
    }
  }

  if (count == 0U) {
    const char *message = "NO REGIONS";
    if (catalog.state == LocationCatalogState::kLoading) {
      message = "FETCHING REGIONS...";
    } else if (catalog.state == LocationCatalogState::kError) {
      message = "REGION REQUEST FAILED";
    }
    const int message_width = static_cast<int>(std::strlen(message)) * 18;
    drawText(framebuffer_, std::max(24, (kWidth - message_width) / 2), 210,
             message, 3,
             catalog.state == LocationCatalogState::kError ? kRed : kMuted);
    if (catalog.state == LocationCatalogState::kError &&
        catalog.http_status != 0) {
      char http_text[24]{};
      std::snprintf(http_text, sizeof(http_text), "HTTP %d",
                    catalog.http_status);
      const int http_width = static_cast<int>(std::strlen(http_text)) * 12;
      drawText(framebuffer_, (kWidth - http_width) / 2, 252, http_text, 2,
               kYellow);
    }
  }

  drawPickerButton(framebuffer_, location_picker_ui::kBackButton, "BACK",
                   true, false);
  drawPickerButton(framebuffer_, location_picker_ui::kPreviousButton, "PREV",
                   visible_page > 0U && !busy, false);
  drawPickerButton(framebuffer_, location_picker_ui::kNextButton, "NEXT",
                   visible_page + 1U < page_count && !busy, false);
  drawPickerButton(framebuffer_, location_picker_ui::kApplyButton, "APPLY",
                   selection_valid && !busy, true);

  if (!flushFramebuffer()) {
    Serial.println("[LCD] ERROR location picker transfer failed");
    return false;
  }
  Serial.printf(
      "[LCD] location picker displayed state=%s page=%u/%u count=%u "
      "selected=%d revision=%lu\n",
      state_label, static_cast<unsigned>(visible_page + 1U),
      static_cast<unsigned>(page_count), static_cast<unsigned>(count),
      selected_index, static_cast<unsigned long>(catalog.revision));
  return true;
}

bool CoastalDisplay::showLocationSearch(const char *query,
                                        size_t query_length,
                                        WifiKeyboardMode mode) {
  if (!ready_ || framebuffer_ == nullptr) {
    return false;
  }

  size_t safe_length = 0U;
  if (query != nullptr) {
    while (safe_length < query_length &&
           safe_length + 1U < kLocationSearchQueryBytes &&
           query[safe_length] != '\0') {
      ++safe_length;
    }
  }
  const bool can_append = safe_length + 1U < kLocationSearchQueryBytes;
  size_t first_non_space = 0U;
  while (first_non_space < safe_length && query[first_non_space] == ' ') {
    ++first_non_space;
  }
  size_t last_non_space = safe_length;
  while (last_non_space > first_non_space &&
         query[last_non_space - 1U] == ' ') {
    --last_non_space;
  }
  const bool can_search = last_non_space - first_non_space >= 2U;

  std::fill_n(framebuffer_, static_cast<size_t>(kWidth) * kHeight,
              kBackground);
  fillRect(framebuffer_, 0, 0, kWidth, 64, kHeader);
  fillRect(framebuffer_, 0, 62, kWidth, 2, kCyan);
  drawText(framebuffer_, 28, 16, "SEARCH LOCATION", 4, kWhite);

  const char *mode_label = mode == WifiKeyboardMode::kLower
                               ? "LOWER"
                               : (mode == WifiKeyboardMode::kUpper
                                      ? "UPPER"
                                      : "SYMBOLS");
  constexpr wifi_setup_ui::Rect kModeBadge{620, 15, 150, 34};
  fillRect(framebuffer_, kModeBadge.x, kModeBadge.y, kModeBadge.width,
           kModeBadge.height, rgb565(17, 50, 65));
  drawRect(framebuffer_, kModeBadge.x, kModeBadge.y, kModeBadge.width,
           kModeBadge.height, 2, kCyan);
  const int mode_width = static_cast<int>(std::strlen(mode_label)) * 12;
  drawText(framebuffer_,
           kModeBadge.x + (kModeBadge.width - mode_width) / 2,
           kModeBadge.y + 10, mode_label, 2, kCyan);

  fillRect(framebuffer_, 24, 72, 752, 44, kCardAlt);
  drawRect(framebuffer_, 24, 72, 752, 44, 2, kCyan);
  char visible_query[kLocationSearchQueryBytes]{};
  for (size_t index = 0U; index < safe_length; ++index) {
    const unsigned char character = static_cast<unsigned char>(query[index]);
    visible_query[index] = character >= 0x20U && character <= 0x7EU
                               ? static_cast<char>(character)
                               : '?';
  }
  if (safe_length == 0U) {
    drawText(framebuffer_, 40, 84, "TYPE A PLACE IN ENGLISH", 2, kMuted);
  } else {
    drawText(framebuffer_, 40, 84, visible_query, 2, kWhite);
  }
  char length_text[12]{};
  std::snprintf(length_text, sizeof(length_text), "LEN %u",
                static_cast<unsigned>(safe_length));
  drawText(framebuffer_, 700, 88, length_text, 1, kMuted);

  for (size_t row = 0U; row < wifi_keyboard_ui::kRows; ++row) {
    for (size_t column = 0U; column < wifi_keyboard_ui::kColumns; ++column) {
      const wifi_keyboard_ui::Rect bounds =
          wifi_keyboard_ui::keyRect(row, column);
      char label[8]{};
      bool enabled = true;
      const char character =
          wifi_keyboard_ui::keyCharacter(mode, row, column);
      if (character != '\0') {
        label[0] = character;
        label[1] = '\0';
        enabled = can_append;
      } else if (wifi_keyboard_ui::isBackspaceCell(row, column)) {
        std::snprintf(label, sizeof(label), "DEL");
        enabled = safe_length > 0U;
      } else if (wifi_keyboard_ui::isCaseCell(row, column)) {
        if (mode == WifiKeyboardMode::kSymbols) {
          enabled = false;
        } else {
          std::snprintf(label, sizeof(label),
                        mode == WifiKeyboardMode::kLower ? "UP" : "LOW");
        }
      } else if (wifi_keyboard_ui::isClearCell(row, column)) {
        std::snprintf(label, sizeof(label), "CLR");
        enabled = safe_length > 0U;
      } else if (wifi_keyboard_ui::isModeCell(row, column)) {
        std::snprintf(label, sizeof(label),
                      mode == WifiKeyboardMode::kSymbols ? "ABC" : "SYM");
      } else {
        enabled = false;
      }
      drawWifiButton(framebuffer_, bounds, label, enabled, false);
    }
  }

  drawText(framebuffer_, 28, 360,
           can_search ? "READY - SEARCHES WORLDWIDE"
                      : "ENTER AT LEAST 2 CHARACTERS",
           2, can_search ? kGreen : kMuted);
  drawWifiButton(framebuffer_, wifi_keyboard_ui::kCancelButton, "BACK", true,
                 false);
  drawWifiButton(framebuffer_, wifi_keyboard_ui::kSpaceButton, "SPACE",
                 can_append, false);
  drawWifiButton(framebuffer_, wifi_keyboard_ui::kConnectButton, "SEARCH",
                 can_search, true);

  if (!flushFramebuffer()) {
    Serial.println("[LCD] ERROR location search transfer failed");
    return false;
  }
  Serial.printf("[LCD] location search displayed length=%u mode=%u\n",
                static_cast<unsigned>(safe_length),
                static_cast<unsigned>(mode));
  return true;
}

bool CoastalDisplay::showWifiPicker(const WifiCatalog &catalog, size_t page) {
  if (!ready_ || framebuffer_ == nullptr) {
    return false;
  }

  const size_t count =
      std::min(catalog.count, static_cast<size_t>(kWifiCatalogCapacity));
  const size_t page_count =
      std::max<size_t>(1U, (count + wifi_setup_ui::kPageSize - 1U) /
                               wifi_setup_ui::kPageSize);
  const size_t visible_page = std::min(page, page_count - 1U);
  const bool busy = catalog.state == WifiSetupState::kScanning ||
                    catalog.state == WifiSetupState::kConnecting ||
                    catalog.state == WifiSetupState::kForgetting;
  const char *state_label = wifiStateLabel(catalog.state);
  const uint16_t state_color = wifiStateColor(catalog.state);

  std::fill_n(framebuffer_, static_cast<size_t>(kWidth) * kHeight,
              kBackground);
  fillRect(framebuffer_, 0, 0, kWidth, 64, kHeader);
  fillRect(framebuffer_, 0, 62, kWidth, 2, kCyan);
  drawText(framebuffer_, 28, 16, "SELECT WIFI", 4, kWhite);

  char page_text[20]{};
  std::snprintf(page_text, sizeof(page_text), "PAGE %u/%u",
                static_cast<unsigned>(visible_page + 1U),
                static_cast<unsigned>(page_count));
  drawText(framebuffer_, 350, 25, page_text, 2, kMuted);

  constexpr wifi_setup_ui::Rect kStateBadge{620, 15, 150, 34};
  fillRect(framebuffer_, kStateBadge.x, kStateBadge.y, kStateBadge.width,
           kStateBadge.height, rgb565(17, 50, 65));
  drawRect(framebuffer_, kStateBadge.x, kStateBadge.y, kStateBadge.width,
           kStateBadge.height, 2, state_color);
  const int state_width = static_cast<int>(std::strlen(state_label)) * 12;
  drawText(framebuffer_,
           kStateBadge.x + (kStateBadge.width - state_width) / 2,
           kStateBadge.y + 10, state_label, 2, state_color);

  for (size_t slot = 0U; slot < wifi_setup_ui::kPageSize; ++slot) {
    const size_t option_index = visible_page * wifi_setup_ui::kPageSize + slot;
    if (option_index >= count) {
      continue;
    }

    const wifi_setup_ui::Rect bounds = wifi_setup_ui::cardRect(slot);
    const WifiNetworkOption &option = catalog.options[option_index];
    const bool active = catalog.active_ssid[0] != '\0' &&
                        std::strcmp(option.ssid, catalog.active_ssid) == 0;
    drawCard(framebuffer_, bounds.x, bounds.y, bounds.width, bounds.height,
             active ? rgb565(15, 75, 82) : kCard);
    if (active) {
      drawRect(framebuffer_, bounds.x, bounds.y, bounds.width, bounds.height,
               4, kGreen);
    }

    char ordinal[4]{};
    std::snprintf(ordinal, sizeof(ordinal), "%02u",
                  static_cast<unsigned>(option_index + 1U));
    drawText(framebuffer_, bounds.x + 14, bounds.y + 12, ordinal, 2,
             active ? kGreen : kMuted);

    char ssid[27]{};
    makeWifiDisplayText(option.ssid, ssid, sizeof(ssid), 25U);
    drawText(framebuffer_, bounds.x + 52, bounds.y + 10, ssid, 2, kWhite);

    char details[32]{};
    const char *security = !option.supported
                               ? "UNSUPPORTED"
                               : (option.secured ? "LOCK" : "OPEN");
    std::snprintf(details, sizeof(details), "%s  %ld DBM%s", security,
                  static_cast<long>(option.rssi), active ? "  ACTIVE" : "");
    drawText(framebuffer_, bounds.x + 52, bounds.y + 39, details, 1,
             active ? kGreen : kCyan);
  }

  if (count == 0U) {
    const char *message = "NO NETWORKS FOUND";
    if (catalog.state == WifiSetupState::kScanning) {
      message = "SCANNING NETWORKS...";
    } else if (catalog.state == WifiSetupState::kError) {
      message = wifiErrorLabel(catalog.error);
    }
    const int message_width = static_cast<int>(std::strlen(message)) * 18;
    drawText(framebuffer_, std::max(24, (kWidth - message_width) / 2), 214,
             message, 3,
             catalog.state == WifiSetupState::kError ? kRed : kMuted);
  }

  const bool has_saved_wifi = catalog.active_ssid[0] != '\0';
  char saved_ssid[29]{};
  if (has_saved_wifi) {
    makeWifiDisplayText(catalog.active_ssid, saved_ssid, sizeof(saved_ssid),
                        27U);
  }
  char saved_text[40]{};
  std::snprintf(saved_text, sizeof(saved_text), "SAVED: %s",
                has_saved_wifi ? saved_ssid : "NONE");
  drawText(framebuffer_, 28, 385, saved_text, 1,
           has_saved_wifi ? kGreen : kMuted);

  drawWifiButton(framebuffer_, wifi_setup_ui::kBackButton, "BACK", true,
                  false);
  drawWifiButton(framebuffer_, wifi_setup_ui::kRescanButton, "RESCAN",
                  !busy, false);
  drawWifiButton(framebuffer_, wifi_setup_ui::kForgetButton, "FORGET",
                 has_saved_wifi && !busy, false, true);
  drawWifiButton(framebuffer_, wifi_setup_ui::kPreviousButton, "PREV",
                 visible_page > 0U && !busy, false);
  drawWifiButton(framebuffer_, wifi_setup_ui::kNextButton, "NEXT",
                 visible_page + 1U < page_count && !busy, false);

  if (!flushFramebuffer()) {
    Serial.println("[LCD] ERROR wifi picker transfer failed");
    return false;
  }
  Serial.printf(
      "[LCD] wifi picker displayed state=%s page=%u/%u count=%u "
      "revision=%lu\n",
      state_label, static_cast<unsigned>(visible_page + 1U),
      static_cast<unsigned>(page_count), static_cast<unsigned>(count),
      static_cast<unsigned long>(catalog.revision));
  return true;
}

bool CoastalDisplay::showWifiForgetConfirm(const char *ssid,
                                           WifiSetupState state,
                                           WifiSetupError error) {
  if (!ready_ || framebuffer_ == nullptr) {
    return false;
  }

  const bool busy = state == WifiSetupState::kForgetting;
  std::fill_n(framebuffer_, static_cast<size_t>(kWidth) * kHeight,
              kBackground);
  fillRect(framebuffer_, 0, 0, kWidth, 64, kHeader);
  fillRect(framebuffer_, 0, 62, kWidth, 2, kRed);
  drawText(framebuffer_, 28, 16, "FORGET WIFI", 4, kWhite);

  constexpr wifi_setup_ui::Rect kStateBadge{620, 15, 150, 34};
  const char *state_label = wifiStateLabel(state);
  const uint16_t state_color = wifiStateColor(state);
  fillRect(framebuffer_, kStateBadge.x, kStateBadge.y, kStateBadge.width,
           kStateBadge.height, rgb565(17, 50, 65));
  drawRect(framebuffer_, kStateBadge.x, kStateBadge.y, kStateBadge.width,
           kStateBadge.height, 2, state_color);
  const int state_width = static_cast<int>(std::strlen(state_label)) * 12;
  drawText(framebuffer_,
           kStateBadge.x + (kStateBadge.width - state_width) / 2,
           kStateBadge.y + 10, state_label, 2, state_color);

  drawCard(framebuffer_, 80, 96, 640, 248, rgb565(42, 25, 34));
  drawRect(framebuffer_, 80, 96, 640, 248, 3, kRed);
  drawCenteredText(framebuffer_, 80, 116, 640, 32, "REMOVE SAVED WIFI?", 3,
                   kWhite);
  char display_ssid[31]{};
  makeWifiDisplayText(ssid, display_ssid, sizeof(display_ssid), 29U);
  drawCenteredText(framebuffer_, 120, 172, 560, 42,
                   display_ssid[0] == '\0' ? "UNKNOWN" : display_ssid, 3,
                   kYellow);
  drawCenteredText(framebuffer_, 80, 238, 640, 24,
                   "THE DEVICE WILL GO OFFLINE", 2, kRed);
  drawCenteredText(framebuffer_, 80, 276, 640, 24,
                   "SELECT A WIFI NETWORK AGAIN", 2, kMuted);
  if (error == WifiSetupError::kForgetFailed) {
    drawCenteredText(framebuffer_, 80, 314, 640, 18,
                     wifiErrorLabel(error), 1, kRed);
  }

  drawWifiButton(framebuffer_, wifi_setup_ui::kForgetCancelButton, "CANCEL",
                 !busy, false);
  drawWifiButton(framebuffer_, wifi_setup_ui::kForgetConfirmButton,
                 busy ? "FORGETTING" : (error == WifiSetupError::kForgetFailed
                                             ? "RETRY"
                                             : "FORGET"),
                 !busy, false, true);

  if (!flushFramebuffer()) {
    Serial.println("[LCD] ERROR wifi forget confirmation transfer failed");
    return false;
  }
  Serial.printf("[LCD] wifi forget confirmation displayed state=%s ssid='%s'\n",
                state_label, display_ssid);
  return true;
}

bool CoastalDisplay::showWifiPassword(const WifiNetworkOption &network,
                                      const char *password,
                                      size_t password_length,
                                      WifiKeyboardMode mode,
                                      WifiSetupState state,
                                      WifiSetupError error,
                                      const char *key_feedback) {
  if (!ready_ || framebuffer_ == nullptr) {
    return false;
  }

  const bool busy = state == WifiSetupState::kConnecting;
  const bool connected = state == WifiSetupState::kConnected;
  const bool password_valid =
      network.supported &&
      (!network.secured ||
       (password_length >= 8U && password_length <= 63U));
  const char *state_label = wifiStateLabel(state);
  const uint16_t state_color = wifiStateColor(state);

  std::fill_n(framebuffer_, static_cast<size_t>(kWidth) * kHeight,
              kBackground);
  fillRect(framebuffer_, 0, 0, kWidth, 64, kHeader);
  fillRect(framebuffer_, 0, 62, kWidth, 2, kCyan);
  drawText(framebuffer_, 28, 16, "WIFI PASSWORD", 4, kWhite);
  drawWifiKeyFeedback(framebuffer_, key_feedback);

  constexpr wifi_setup_ui::Rect kStateBadge{620, 15, 150, 34};
  fillRect(framebuffer_, kStateBadge.x, kStateBadge.y, kStateBadge.width,
           kStateBadge.height, rgb565(17, 50, 65));
  drawRect(framebuffer_, kStateBadge.x, kStateBadge.y, kStateBadge.width,
           kStateBadge.height, 2, state_color);
  const int state_width = static_cast<int>(std::strlen(state_label)) * 12;
  drawText(framebuffer_,
           kStateBadge.x + (kStateBadge.width - state_width) / 2,
           kStateBadge.y + 10, state_label, 2, state_color);

  char ssid[25]{};
  makeWifiDisplayText(network.ssid, ssid, sizeof(ssid), 23U);
  drawText(framebuffer_, 28, 78, ssid, 2, kWhite);
  const char *security_label = !network.supported
                                   ? "UNSUPPORTED SECURITY"
                                   : (network.secured ? "SECURED NETWORK"
                                                      : "OPEN NETWORK");
  drawText(framebuffer_, 28, 102, security_label, 1,
           !network.supported ? kRed
                              : (network.secured ? kYellow : kGreen));
  const char *mode_label = mode == WifiKeyboardMode::kLower
                               ? "VISIBLE - LOWER"
                               : (mode == WifiKeyboardMode::kUpper
                                      ? "VISIBLE - UPPER"
                                      : "VISIBLE - SYMBOLS");
  drawText(framebuffer_, 174, 102, mode_label, 1, kCyan);

  fillRect(framebuffer_, 330, 72, 446, 44, kCardAlt);
  drawRect(framebuffer_, 330, 72, 446, 44, 2, kCyan);
  char visible_password[27]{};
  size_t safe_length = 0U;
  if (password != nullptr) {
    while (safe_length < password_length &&
           safe_length < kWifiPasswordBytes - 1U &&
           password[safe_length] != '\0') {
      ++safe_length;
    }
  }
  if (safe_length <= 26U) {
    std::copy_n(password == nullptr ? "" : password, safe_length,
                visible_password);
  } else {
    visible_password[0] = '<';
    std::copy_n(password + safe_length - 25U, 25U, visible_password + 1U);
  }
  const char *password_text = visible_password;
  uint16_t password_color = kWhite;
  if (!network.supported) {
    password_text = "SECURITY NOT SUPPORTED";
    password_color = kRed;
  } else if (!network.secured) {
    password_text = "NO PASSWORD REQUIRED";
    password_color = kGreen;
  } else if (safe_length == 0U) {
    password_text = "TYPE PASSWORD";
    password_color = kMuted;
  }
  drawText(framebuffer_, 346, 84, password_text, 2, password_color);
  char length_text[12]{};
  std::snprintf(length_text, sizeof(length_text), "LEN %u",
                static_cast<unsigned>(password_length));
  drawText(framebuffer_, 690, 88, length_text, 1, kMuted);

  for (size_t row = 0U; row < wifi_keyboard_ui::kRows; ++row) {
    for (size_t column = 0U; column < wifi_keyboard_ui::kColumns; ++column) {
      const wifi_keyboard_ui::Rect bounds =
          wifi_keyboard_ui::keyRect(row, column);
      char label[8]{};
      bool enabled =
          !busy && !connected && network.secured && network.supported;
      const char character =
          wifi_keyboard_ui::keyCharacter(mode, row, column);
      if (character != '\0') {
        label[0] = character;
        label[1] = '\0';
      } else if (wifi_keyboard_ui::isBackspaceCell(row, column)) {
        std::snprintf(label, sizeof(label), "DEL");
        enabled = enabled && password_length > 0U;
      } else if (wifi_keyboard_ui::isCaseCell(row, column)) {
        if (mode == WifiKeyboardMode::kSymbols) {
          enabled = false;
        } else {
          std::snprintf(label, sizeof(label),
                        mode == WifiKeyboardMode::kLower ? "UP" : "LOW");
        }
      } else if (wifi_keyboard_ui::isClearCell(row, column)) {
        std::snprintf(label, sizeof(label), "CLR");
        enabled = enabled && password_length > 0U;
      } else if (wifi_keyboard_ui::isModeCell(row, column)) {
        std::snprintf(label, sizeof(label),
                      mode == WifiKeyboardMode::kSymbols ? "ABC" : "SYM");
      } else {
        enabled = false;
      }
      drawWifiButton(framebuffer_, bounds, label, enabled, false);
    }
  }

  const char *message = wifiErrorLabel(error);
  uint16_t message_color = error == WifiSetupError::kNone ? kMuted : kRed;
  if (busy) {
    message = "CONNECTING - OLD NETWORK IS KEPT UNTIL SUCCESS";
    message_color = kYellow;
  } else if (connected) {
    message = "CONNECTED AND SAVED - RETURNING TO WEATHER";
    message_color = kGreen;
  } else if (error == WifiSetupError::kNone && network.secured &&
             !password_valid) {
    message = network.supported ? "ENTER 8 TO 63 CHARACTERS"
                                : "SELECT A WPA/WPA2/WPA3 PERSONAL NETWORK";
  } else if (error == WifiSetupError::kNone && !network.secured) {
    message = "NO PASSWORD NEEDED - TAP CONNECT";
    message_color = kGreen;
  }
  drawText(framebuffer_, 28, 360, message, 2, message_color);

  drawWifiButton(framebuffer_, wifi_keyboard_ui::kCancelButton, "BACK",
                 !busy && !connected, false);
  drawWifiButton(framebuffer_, wifi_keyboard_ui::kSpaceButton, "SPACE",
                 !busy && !connected && network.secured && network.supported,
                 false);
  drawWifiButton(framebuffer_, wifi_keyboard_ui::kConnectButton, "CONNECT",
                 !busy && !connected && password_valid, true);

  if (!flushFramebuffer()) {
    Serial.println("[LCD] ERROR wifi password transfer failed");
    return false;
  }
  Serial.printf(
      "[LCD] wifi password displayed ssid='%s' length=%u state=%s "
      "error=%u\n",
      network.ssid, static_cast<unsigned>(password_length), state_label,
      static_cast<unsigned>(error));
  return true;
}

bool CoastalDisplay::updateWifiKeyFeedback(const char *key_feedback) {
  if (!ready_ || framebuffer_ == nullptr ||
      partial_transfer_buffer_ == nullptr) {
    return false;
  }
  drawWifiKeyFeedback(framebuffer_, key_feedback);
  return flushRegion(kWifiKeyFeedbackX, kWifiKeyFeedbackY,
                     kWifiKeyFeedbackWidth, kWifiKeyFeedbackHeight);
}
