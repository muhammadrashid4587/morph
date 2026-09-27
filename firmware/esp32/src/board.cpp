#include "board.h"

#include <Arduino.h>
#include <esp_arduino_version.h>
#include <soc/soc_caps.h>

namespace {

constexpr uint32_t kPwmHz = 50;             // servo / ESC signal frequency
constexpr uint32_t kPwmPeriodUs = 20000;
constexpr uint8_t kPwmBits = 14;            // 14-bit works on every ESP32 variant at 50 Hz
constexpr uint32_t kPwmMaxDuty = (1u << kPwmBits) - 1;
constexpr uint32_t kStepPulseUs = 5;        // STEP high time; >= the minimum of common drivers (check datasheet)
constexpr uint32_t kDirSetupUs = 2;         // DIR must settle before a STEP edge
constexpr uint32_t kSlowSampleMs = 10;      // touchRead / analogRead rate

bool level_is(int pin, int active_level) { return digitalRead(pin) == (active_level ? HIGH : LOW); }

void configure_input(int pin, hw::Pull pull) {
  switch (pull) {
    case hw::Pull::Up: pinMode(pin, INPUT_PULLUP); break;
    case hw::Pull::Down: pinMode(pin, INPUT_PULLDOWN); break;
    default: pinMode(pin, INPUT); break;
  }
}

}  // namespace

// --- PWM ---------------------------------------------------------------------------------------

bool PwmOut::begin(int pin, int channel) {
  if (!board::pin_usable(pin)) return false;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  (void)channel;
  if (!ledcAttach(static_cast<uint8_t>(pin), kPwmHz, kPwmBits)) return false;
  ledcWrite(static_cast<uint8_t>(pin), 0);
#else
  ledcSetup(static_cast<uint8_t>(channel), kPwmHz, kPwmBits);
  ledcAttachPin(static_cast<uint8_t>(pin), static_cast<uint8_t>(channel));
  ledcWrite(static_cast<uint8_t>(channel), 0);
#endif
  pin_ = pin;
  channel_ = channel;
  last_us_ = 0;
  return true;
}

void PwmOut::write_pulse_us(int pulse_us) {
  if (pin_ < 0 || pulse_us == last_us_) return;
  if (pulse_us < 0 || pulse_us >= static_cast<int>(kPwmPeriodUs)) pulse_us = 0;
  const uint32_t duty = static_cast<uint32_t>(pulse_us) * kPwmMaxDuty / kPwmPeriodUs;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(static_cast<uint8_t>(pin_), duty);
#else
  ledcWrite(static_cast<uint8_t>(channel_), duty);
#endif
  last_us_ = pulse_us;
}

// --- Board ---------------------------------------------------------------------------------------

void Board::begin() {
  // 1. Motor outputs FIRST, straight into their safe states, for every pin that is known,
  //    even if the rest of that device is not configured yet.
  int channel = 0;
  for (const hw::JointSpec& spec : hw::ARM_JOINTS) {
    Joint j;
    j.spec = spec;
    if (spec.kind == hw::JointKind::Stepper) {
      j.en_known = board::pin_usable(spec.enable_pin) && (spec.enable_active_level == 0 || spec.enable_active_level == 1);
      if (j.en_known) {
        digitalWrite(spec.enable_pin, spec.enable_active_level ? LOW : HIGH);  // set level before driving the pin
        pinMode(spec.enable_pin, OUTPUT);
        digitalWrite(spec.enable_pin, spec.enable_active_level ? LOW : HIGH);  // driver DISABLED
      }
      for (int pin : {spec.step_pin, spec.dir_pin}) {
        if (board::pin_usable(pin)) {
          digitalWrite(pin, LOW);
          pinMode(pin, OUTPUT);
          digitalWrite(pin, LOW);
        }
      }
    } else {
      j.servo.begin(spec.servo_signal_pin, channel++);  // duty 0: no pulses
    }
    joints_.push_back(j);
  }
  if (cfg_.config.esc_enabled) {
    esc_.begin(hw::ESC_SIGNAL_PIN, channel++);
    esc_.write_pulse_us(hw::ESC_STOP_PULSE_US);  // an ESC must only ever see its stop pulse at boot
  }

  // 2. Inputs.
  if (cfg_.config.estop.configured) {
    configure_input(hw::ESTOP_PIN, hw::ESTOP_PULL);
    if (cfg_.config.estop.requires_reset_input) configure_input(hw::ESTOP_RESET_PIN, hw::ESTOP_RESET_PULL);
  }
  if (cfg_.config.button_a.enabled) configure_input(hw::BUTTON_A_PIN, hw::BUTTON_A_PULL);
  if (cfg_.config.button_b.enabled) configure_input(hw::BUTTON_B_PIN, hw::BUTTON_B_PULL);
  if (cfg_.config.touch.enabled && hw::TOUCH_KIND == hw::TouchKind::DigitalModule) {
    configure_input(hw::TOUCH_PIN, hw::Pull::None);
  }
  if (cfg_.config.dial.source == morph::DialSource::Encoder) {
    configure_input(hw::ENCODER_A_PIN, hw::ENCODER_PULL);
    configure_input(hw::ENCODER_B_PIN, hw::ENCODER_PULL);
    encoder_ = morph::QuadratureDecoder(hw::ENCODER_TRANSITIONS_PER_DETENT);
  }
  if (cfg_.config.dial.source == morph::DialSource::JoystickX) {
    joystick_cal_.raw_min = hw::JOYSTICK_X_MIN;
    joystick_cal_.raw_center = hw::JOYSTICK_X_CENTER;
    joystick_cal_.raw_max = hw::JOYSTICK_X_MAX;
  }
}

morph::InputSample Board::read(uint32_t now_ms) {
  const morph::Config& c = cfg_.config;
  morph::InputSample in;
  if (c.estop.configured) {
    in.estop = level_is(hw::ESTOP_PIN, hw::ESTOP_ACTIVE_LEVEL);
    if (c.estop.requires_reset_input) in.estop_reset = level_is(hw::ESTOP_RESET_PIN, hw::ESTOP_RESET_ACTIVE_LEVEL);
  }
  if (c.button_a.enabled) in.button_a = level_is(hw::BUTTON_A_PIN, hw::BUTTON_A_ACTIVE_LEVEL);
  if (c.button_b.enabled) in.button_b = level_is(hw::BUTTON_B_PIN, hw::BUTTON_B_ACTIVE_LEVEL);
  if (c.dial.source == morph::DialSource::Encoder) {
    in.encoder_detents = encoder_.update(digitalRead(hw::ENCODER_A_PIN) == HIGH, digitalRead(hw::ENCODER_B_PIN) == HIGH);
  }

  // Slower analog/capacitive inputs, sampled every kSlowSampleMs and reused in between.
  if (static_cast<int32_t>(now_ms - next_slow_sample_ms_) >= 0) {
    next_slow_sample_ms_ = now_ms + kSlowSampleMs;
    if (c.touch.enabled) {
      if (hw::TOUCH_KIND == hw::TouchKind::DigitalModule) {
        touch_ = level_is(hw::TOUCH_PIN, hw::TOUCH_ACTIVE_LEVEL);
      } else {
#if SOC_TOUCH_SENSOR_NUM > 0
        const auto raw = static_cast<int>(touchRead(static_cast<uint8_t>(hw::TOUCH_PIN)));
#if CONFIG_IDF_TARGET_ESP32
        touch_ = raw < hw::TOUCH_THRESHOLD;  // classic ESP32: the reading DROPS when touched
#else
        touch_ = raw > hw::TOUCH_THRESHOLD;  // ESP32-S2/S3: the reading RISES when touched
#endif
#else
        touch_ = false;  // this chip has no capacitive touch peripheral
#endif
      }
    }
    if (c.dial.source == morph::DialSource::JoystickX) {
      joystick_x_ = morph::normalize_axis(analogRead(hw::JOYSTICK_X_PIN), joystick_cal_);
    }
  }
  in.touch = touch_;
  in.joystick_x = joystick_x_;
  return in;
}

void Board::apply(const morph::Outputs& out) {
  // Joints: the controller already forces everything off when motion is not allowed.
  for (size_t i = 0; i < joints_.size(); ++i) {
    Joint& j = joints_[i];
    const morph::JointOutput* o = i < out.joints.size() ? &out.joints[i] : nullptr;
    const bool enabled = o != nullptr && o->enabled && out.motion_allowed;
    if (j.spec.kind == hw::JointKind::Servo) {
      j.servo.write_pulse_us(enabled ? o->servo_pulse_us : 0);
      continue;
    }
    if (j.en_known) {
      const int active = j.spec.enable_active_level ? HIGH : LOW;
      digitalWrite(j.spec.enable_pin, enabled ? active : (active == HIGH ? LOW : HIGH));
    }
    if (!enabled || o->step == 0 || !board::pin_usable(j.spec.step_pin) || !board::pin_usable(j.spec.dir_pin)) {
      continue;
    }
    if (o->step != j.last_dir) {
      digitalWrite(j.spec.dir_pin, o->step > 0 ? HIGH : LOW);
      j.last_dir = o->step;
      delayMicroseconds(kDirSetupUs);
    }
    digitalWrite(j.spec.step_pin, HIGH);
    delayMicroseconds(kStepPulseUs);
    digitalWrite(j.spec.step_pin, LOW);
  }

  if (esc_.attached()) esc_.write_pulse_us(out.esc_pulse_us);

  if (out.led != last_led_) {
    led_.show(out.led);
    last_led_ = out.led;
  }
  if (out.lcd_line1 != last_lcd1_ || out.lcd_line2 != last_lcd2_) {  // only on change: LCD writes are slow
    lcd_.show(out.lcd_line1, out.lcd_line2);
    last_lcd1_ = out.lcd_line1;
    last_lcd2_ = out.lcd_line2;
  }
}

std::string Board::calibration_report() {
  std::string report;
  if (board::pin_usable(hw::JOYSTICK_X_PIN) && cfg_.config.dial.source != morph::DialSource::JoystickX) {
    report += "# calibrate joystick_x_raw=" + std::to_string(analogRead(hw::JOYSTICK_X_PIN));
  }
#if SOC_TOUCH_SENSOR_NUM > 0
  if (hw::TOUCH_KIND == hw::TouchKind::Esp32Capacitive && board::pin_usable(hw::TOUCH_PIN) &&
      !cfg_.config.touch.enabled) {
    if (!report.empty()) report += "\n";
    report += "# calibrate touch_raw=" + std::to_string(touchRead(static_cast<uint8_t>(hw::TOUCH_PIN)));
  }
#endif
  return report;
}
