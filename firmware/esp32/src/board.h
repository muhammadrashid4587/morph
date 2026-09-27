// ESP32 hardware layer (Arduino). Reads inputs into a morph::InputSample and
// applies morph::Outputs to pins. It never decides anything about safety: it
// only obeys the controller, and it puts every known motor pin into a safe
// state before anything else runs.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "board_config.h"
#include "display.h"
#include "morph/controller.h"
#include "morph/inputs.h"

// 50 Hz RC-style PWM output (servo or ESC signal) on an LEDC channel.
class PwmOut {
 public:
  bool begin(int pin, int channel);
  void write_pulse_us(int pulse_us);  // 0 = no pulses (line held low)
  bool attached() const { return pin_ >= 0; }

 private:
  int pin_ = -1;
  int channel_ = -1;
  int last_us_ = -1;
};

class Board {
 public:
  explicit Board(const board::BoardConfig& cfg) : cfg_(cfg) {}

  void begin();                                // call first in setup(): motors into safe states
  morph::InputSample read(uint32_t now_ms);    // sample every input
  void apply(const morph::Outputs& out);       // drive every output
  // Raw readings for calibration printouts while a device is not yet configured.
  std::string calibration_report();

 private:
  struct Joint {
    hw::JointSpec spec;
    PwmOut servo;
    int last_dir = 0;
    bool en_known = false;
  };

  const board::BoardConfig& cfg_;
  std::vector<Joint> joints_;
  PwmOut esc_;
  NullLed led_;
  NullLcd lcd_;
  morph::Rgb last_led_{1, 2, 3};  // forces the first update
  std::string last_lcd1_, last_lcd2_;
  morph::QuadratureDecoder encoder_{4};
  morph::AxisCalibration joystick_cal_;
  float joystick_x_ = 0.0f;
  bool touch_ = false;
  uint32_t next_slow_sample_ms_ = 0;
};
