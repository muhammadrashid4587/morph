// Device-independent LED pattern and LCD text logic. The actual LED/LCD
// hardware type is not confirmed yet; these compute *what* to show.
#pragma once

#include <cstdint>
#include <string>
#include <string_view>

#include "morph/protocol.h"

namespace morph {

struct Rgb {
  uint8_t r = 0, g = 0, b = 0;
  bool operator==(const Rgb& o) const { return r == o.r && g == o.g && b == o.b; }
  bool operator!=(const Rgb& o) const { return !(*this == o); }
};

constexpr uint32_t kBlinkPeriodMs = 500;   // 250 ms on, 250 ms off
constexpr uint32_t kPulsePeriodMs = 1500;  // smooth breathing

Rgb color_rgb(LedColor color);

// The LED color to show at `now_ms` for a contract color + pattern.
Rgb led_output(LedColor color, LedPattern pattern, uint32_t now_ms);

constexpr size_t kLcdColumns = 16;  // contract §3A: lines longer than 16 chars are truncated

// Printable-ASCII, exactly kLcdColumns wide: non-ASCII/control bytes become '?'
// (a multi-byte UTF-8 character becomes a single '?'), long text is truncated,
// short text is padded with spaces.
std::string lcd_line(std::string_view text);

}  // namespace morph
