#include "board_config.h"

#include <string>

namespace board {
namespace {

using hw::UNKNOWN;

void need(std::vector<std::string>& missing, bool ok, const std::string& what) {
  if (!ok) missing.push_back(what);
}

}  // namespace

bool pin_usable(int pin) {
  if (pin < 0 || pin > 48) return false;
#if defined(CONFIG_IDF_TARGET_ESP32) || !defined(ARDUINO)
  // Classic ESP32: GPIO 6-11 are wired to the SPI flash and must never be used.
  if (pin >= 6 && pin <= 11) return false;
#endif
  return true;
}

bool input_known(int pin, int active_level, hw::Pull pull) {
  return pin_usable(pin) && (active_level == 0 || active_level == 1) && pull != hw::Pull::Unknown;
}

morph::JointConfig joint_config(const hw::JointSpec& spec, std::vector<std::string>& missing) {
  morph::JointConfig j;
  j.name = spec.name;
  const std::string where = std::string("arm joint '") + spec.name + "': ";
  for (size_t i = 0; i < j.pose_targets.size(); ++i) j.pose_targets[i] = spec.pose_targets[i];
  if (spec.kind == hw::JointKind::Servo) {
    j.kind = morph::JointKind::Servo;
    j.servo = morph::ServoLimits{spec.servo_pulse_us_at_0deg, spec.servo_pulse_us_at_180deg, spec.servo_min_deg,
                                 spec.servo_max_deg, spec.servo_max_speed_dps};
    need(missing, pin_usable(spec.servo_signal_pin), where + "servo signal pin");
    need(missing, morph::valid(j.servo), where + "servo pulse calibration / joint limits / speed");
  } else {
    j.kind = morph::JointKind::Stepper;
    j.stepper = morph::StepperLimits{spec.stepper_max_speed_sps, spec.stepper_accel_sps2, spec.stepper_min_steps,
                                     spec.stepper_max_steps};
    need(missing, pin_usable(spec.step_pin) && pin_usable(spec.dir_pin) && pin_usable(spec.enable_pin),
         where + "STEP/DIR/EN pins");
    need(missing, spec.enable_active_level == 0 || spec.enable_active_level == 1, where + "EN active level");
    need(missing, morph::valid(j.stepper), where + "speed / acceleration / travel limits");
  }
  return j;
}

BoardConfig build() {
  BoardConfig out;
  morph::Config& c = out.config;
  std::vector<std::string>& missing = out.missing;

  // E-stop: without it, no motor may ever be enabled.
  {
    std::vector<std::string> m;
    need(m, hw::ESTOP_KIND != hw::EstopKind::Unknown, "e-stop: type (latching switch or momentary + reset)");
    need(m, input_known(hw::ESTOP_PIN, hw::ESTOP_ACTIVE_LEVEL, hw::ESTOP_PULL), "e-stop: pin / active level / pull");
    if (hw::ESTOP_KIND == hw::EstopKind::MomentaryWithReset) {
      need(m, input_known(hw::ESTOP_RESET_PIN, hw::ESTOP_RESET_ACTIVE_LEVEL, hw::ESTOP_RESET_PULL),
           "e-stop: reset input pin / active level / pull");
    }
    c.estop.configured = m.empty();
    c.estop.requires_reset_input = hw::ESTOP_KIND == hw::EstopKind::MomentaryWithReset;
    missing.insert(missing.end(), m.begin(), m.end());
  }

  c.button_a.enabled = input_known(hw::BUTTON_A_PIN, hw::BUTTON_A_ACTIVE_LEVEL, hw::BUTTON_A_PULL);
  need(missing, c.button_a.enabled, "button A: pin / active level / pull");
  c.button_b.enabled = input_known(hw::BUTTON_B_PIN, hw::BUTTON_B_ACTIVE_LEVEL, hw::BUTTON_B_PULL);
  need(missing, c.button_b.enabled, "button B: pin / active level / pull");

  switch (hw::TOUCH_KIND) {
    case hw::TouchKind::DigitalModule:
      c.touch.enabled = pin_usable(hw::TOUCH_PIN) && (hw::TOUCH_ACTIVE_LEVEL == 0 || hw::TOUCH_ACTIVE_LEVEL == 1);
      need(missing, c.touch.enabled, "touch: pin / active level");
      break;
    case hw::TouchKind::Esp32Capacitive:
      c.touch.enabled = pin_usable(hw::TOUCH_PIN) && hw::TOUCH_THRESHOLD > 0;
      need(missing, c.touch.enabled, "touch: touch-capable pin / measured threshold");
      break;
    case hw::TouchKind::Unknown:
      missing.push_back("touch: sensor type (digital module or ESP32 capacitive pad)");
      break;
  }

  c.dial.invert = hw::DIAL_INVERT;
  switch (hw::DIAL_KIND) {
    case hw::DialKind::Encoder: {
      const bool ok = pin_usable(hw::ENCODER_A_PIN) && pin_usable(hw::ENCODER_B_PIN) &&
                      hw::ENCODER_PULL != hw::Pull::Unknown && hw::ENCODER_TRANSITIONS_PER_DETENT > 0;
      need(missing, ok, "dial (encoder): A/B pins / pull / transitions per detent");
      if (ok) {
        c.dial.source = morph::DialSource::Encoder;
        c.dial.encoder_transitions_per_detent = hw::ENCODER_TRANSITIONS_PER_DETENT;
      }
      break;
    }
    case hw::DialKind::JoystickX: {
      const bool ok = pin_usable(hw::JOYSTICK_X_PIN) && hw::JOYSTICK_X_MIN >= 0 &&
                      hw::JOYSTICK_X_MIN < hw::JOYSTICK_X_CENTER && hw::JOYSTICK_X_CENTER < hw::JOYSTICK_X_MAX;
      need(missing, ok, "dial (joystick X): ADC pin / calibrated min, center, max");
      if (ok) c.dial.source = morph::DialSource::JoystickX;
      break;
    }
    case hw::DialKind::None:
      break;
    case hw::DialKind::Unknown:
      missing.push_back("dial: source (rotary encoder, joystick X axis, or none)");
      break;
  }

  // No LED/LCD driver exists until the hardware type is confirmed.
  missing.push_back("led: type (e.g. WS2812 strip / RGB LED), pin, count; then a driver must be added");
  missing.push_back("lcd: type (e.g. 16x2 I2C), I2C address, SDA/SCL pins; then a driver must be added");

  // Arm.
  c.arm.pose_timeout_ms = hw::ARM_POSE_TIMEOUT_MS;
  if (hw::ARM_JOINTS.empty()) {
    missing.push_back("arm: no joints confirmed (type, pins, calibration, limits, pose targets per joint)");
  } else {
    std::vector<std::string> m;
    for (const hw::JointSpec& spec : hw::ARM_JOINTS) c.arm.joints.push_back(joint_config(spec, m));
    c.arm.enabled = m.empty();
    missing.insert(missing.end(), m.begin(), m.end());
    if (c.arm.enabled && !c.estop.configured) missing.push_back("arm: disabled until the e-stop is configured");
  }

  // ESC (only ever held at its stop pulse; the contract has no command to spin it).
  c.esc = morph::EscLimits{hw::ESC_STOP_PULSE_US, hw::ESC_FULL_PULSE_US, 0.0f,
                           static_cast<uint32_t>(hw::ESC_ARM_MS > 0 ? hw::ESC_ARM_MS : 0)};
  c.esc_enabled = pin_usable(hw::ESC_SIGNAL_PIN) && morph::valid(c.esc) && hw::ESC_ARM_MS >= 0;
  need(missing, c.esc_enabled, "brushless ESC: signal pin / stop pulse / full pulse / arming time (from ESC manual)");

  return out;
}

}  // namespace board
