#include "morph/outputs.h"

namespace morph {

Rgb color_rgb(LedColor color) {
  switch (color) {
    case LedColor::Blue: return {0, 60, 255};
    case LedColor::Yellow: return {255, 170, 0};
    case LedColor::Green: return {0, 220, 40};
    case LedColor::White: return {255, 255, 255};
    case LedColor::Red: return {255, 0, 0};
  }
  return {};
}

Rgb led_output(LedColor color, LedPattern pattern, uint32_t now_ms) {
  const Rgb full = color_rgb(color);
  switch (pattern) {
    case LedPattern::Solid:
      return full;
    case LedPattern::Off:
      return {};
    case LedPattern::Blink:
      return (now_ms % kBlinkPeriodMs) < kBlinkPeriodMs / 2 ? full : Rgb{};
    case LedPattern::Pulse: {
      // Triangle wave between 10% and 100% brightness (integer math, no floats).
      const uint32_t phase = now_ms % kPulsePeriodMs;
      const uint32_t half = kPulsePeriodMs / 2;
      const uint32_t ramp = phase < half ? phase : kPulsePeriodMs - phase;  // 0..half
      const uint32_t level = 26 + (229 * ramp) / half;                     // 26..255
      return {static_cast<uint8_t>(full.r * level / 255), static_cast<uint8_t>(full.g * level / 255),
              static_cast<uint8_t>(full.b * level / 255)};
    }
  }
  return {};
}

std::string lcd_line(std::string_view text) {
  std::string out;
  for (size_t i = 0; i < text.size() && out.size() < kLcdColumns; ++i) {
    const unsigned char c = static_cast<unsigned char>(text[i]);
    if (c >= 0x20 && c < 0x7F) {
      out.push_back(static_cast<char>(c));
    } else if ((c & 0xC0) == 0x80) {
      continue;  // UTF-8 continuation byte: already represented by the '?' of its lead byte
    } else {
      out.push_back('?');
    }
  }
  out.resize(kLcdColumns, ' ');
  return out;
}

}  // namespace morph
