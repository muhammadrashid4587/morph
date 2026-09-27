// LED and LCD driver interfaces. The hardware types are NOT confirmed, so only
// disabled (null) implementations exist. To enable one: confirm the type in
// hardware_config.h, implement the interface for it, and select it in board.cpp.
#pragma once

#include <string>

#include "morph/outputs.h"

class LedDriver {
 public:
  virtual ~LedDriver() = default;
  virtual void show(const morph::Rgb& color) = 0;
};

class LcdDriver {
 public:
  virtual ~LcdDriver() = default;
  virtual void show(const std::string& line1, const std::string& line2) = 0;  // 16 chars each
};

// Disabled: LED type unknown. set_led is acked ok:false ("led not configured").
class NullLed : public LedDriver {
 public:
  void show(const morph::Rgb&) override {}
};

// Disabled: LCD type unknown. set_lcd is acked ok:false ("lcd not configured").
class NullLcd : public LcdDriver {
 public:
  void show(const std::string&, const std::string&) override {}
};
