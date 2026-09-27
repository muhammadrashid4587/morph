// The ESP32's device-independent brain. Feed it serial lines, input samples
// and time; it produces contract JSON lines and the outputs every driver must apply.
//
// Safety rules enforced here (the drivers just obey `Outputs`):
//   - Motors are OFF at boot, until the Pi has spoken (heartbeat/any valid cmd).
//   - Motors go OFF on heartbeat loss (1000 ms), on any invalid command, on `stop`,
//     and while the latched E-Stop is active. Only a physical reset clears the E-Stop.
//   - Motors are never enabled unless an e-stop input is configured.
//   - The Pi's LED/LCD requests are overridden locally by E-STOP and PI LOST screens.
#pragma once

#include <cstdint>
#include <deque>
#include <string>
#include <string_view>
#include <vector>

#include "morph/arm.h"
#include "morph/config.h"
#include "morph/inputs.h"
#include "morph/outputs.h"
#include "morph/protocol.h"
#include "morph/safety.h"

namespace morph {

// One sample of every input, already converted to logical values by the drivers.
struct InputSample {
  bool button_a = false;     // true = pressed
  bool button_b = false;
  bool touch = false;        // true = touched
  bool estop = false;        // true = e-stop engaged (pressed / power cut sensed)
  bool estop_reset = false;  // true = reset input pressed (only for momentary e-stops)
  float joystick_x = 0.0f;   // normalized -1..1 (when the dial source is the joystick)
  int encoder_detents = 0;   // detents since the previous sample (when the dial source is an encoder)
};

struct Outputs {
  bool motion_allowed = false;  // e-stop clear, link alive, arm usable
  Rgb led;
  std::string lcd_line1;  // always exactly 16 characters
  std::string lcd_line2;
  std::vector<JointOutput> joints;
  int esc_pulse_us = 0;  // 0 = ESC not configured (do not drive the pin)
};

class Controller {
 public:
  explicit Controller(const Config& config);

  void begin(uint32_t now_ms);  // call once at boot: sends hello
  void on_line(std::string_view line, uint32_t now_ms);  // one received line (no '\n')
  void tick(const InputSample& in, uint32_t now_ms, uint32_t now_us);

  bool pop_line(std::string& out);  // next JSON line to send (without '\n')
  const Outputs& outputs() const { return out_; }

  // Introspection (simulator, tests, debug).
  bool estop_active() const { return estop_.active(); }
  bool link_alive() const { return link_alive_; }
  Mode mode() const { return mode_; }
  ArmState arm_state() const { return arm_.state(); }
  const ArmController& arm() const { return arm_; }
  const Config& config() const { return config_; }

 private:
  void emit(std::string line) { tx_.push_back(std::move(line)); }
  void handle_command(const Command& c, const std::string& cmd, uint32_t now_ms);
  // Stops the arm and powers joints off; acks an in-flight arm_pose with ok:false.
  void safety_stop(const char* reason);
  void refresh_outputs(uint32_t now_ms);
  void poll_inputs(const InputSample& in, uint32_t now_ms);

  Config config_;
  bool arm_usable_;
  ArmController arm_;
  EStopLatch estop_;
  LinkWatchdog link_;
  bool link_alive_ = false;
  ButtonTracker button_a_, button_b_;
  TapDetector touch_;
  JoystickDial joystick_dial_;
  DialAccumulator dial_;
  EscOutput esc_;

  Mode mode_ = Mode::Idle;
  LedColor led_color_ = LedColor::White;
  LedPattern led_pattern_ = LedPattern::Off;
  std::string pi_line1_, pi_line2_;
  bool pose_ack_pending_ = false;
  uint32_t next_status_ms_ = 0;
  std::deque<std::string> tx_;
  Outputs out_;
};

}  // namespace morph
