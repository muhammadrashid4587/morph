// Runtime configuration of the device-independent core. The firmware builds
// this from include/hardware_config.h; tests and the simulator build it directly.
// Everything defaults to DISABLED: a device only works once it is configured.
#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "morph/motion.h"
#include "morph/protocol.h"

namespace morph {

struct ButtonConfig {
  bool enabled = false;
  uint32_t debounce_ms = 30;
  uint32_t long_press_ms = 1000;  // contract: long_press = held >= 1 s
};

struct TouchConfig {
  bool enabled = false;
  uint32_t debounce_ms = 30;
  uint32_t max_tap_ms = 800;
};

enum class DialSource { None, Encoder, JoystickX };

struct DialConfig {
  DialSource source = DialSource::None;
  bool invert = false;                   // flip so clockwise / stick-right is positive
  int encoder_transitions_per_detent = 4;
  float joystick_threshold = 0.6f;
  uint32_t joystick_initial_delay_ms = 400;
  uint32_t joystick_repeat_ms = 150;
};

struct EstopConfig {
  bool configured = false;            // a real e-stop input exists and is wired
  bool requires_reset_input = false;  // momentary e-stop button: needs a separate reset input
  uint32_t reset_stable_ms = 500;
};

enum class JointKind { Servo, Stepper };

struct JointConfig {
  std::string name;
  JointKind kind = JointKind::Servo;
  ServoLimits servo;
  StepperLimits stepper;
  // Target per contract pose, in order home, point_blue, point_yellow, point_green, nod:
  // degrees for servo joints, steps for stepper joints.
  std::array<float, kArmPoseCount> pose_targets{};
};

struct ArmConfig {
  bool enabled = false;
  std::vector<JointConfig> joints;
  uint32_t pose_timeout_ms = 8000;  // a pose that has not finished by then fails
};

struct Config {
  bool led_enabled = false;
  bool lcd_enabled = false;
  ButtonConfig button_a, button_b;
  TouchConfig touch;
  DialConfig dial;
  EstopConfig estop;
  ArmConfig arm;
  bool esc_enabled = false;
  EscLimits esc;
  uint32_t heartbeat_timeout_ms = 1000;  // contract §3A
  uint32_t status_period_ms = 500;       // contract §3B
};

// Why the arm cannot be enabled (empty when it can). Motors require a configured e-stop.
std::vector<std::string> arm_problems(const Config& config);
bool arm_usable(const Config& config);

// Capabilities for the hello event, in contract order: led, lcd, button_a, button_b, dial, arm.
std::vector<std::string> capabilities(const Config& config);

}  // namespace morph
