// MORPH ESP32 hardware configuration (pin map and device parameters).
//
// EVERY value here must come from the hardware team (Sahil). Nothing is guessed:
// UNKNOWN means "not confirmed", and a device stays DISABLED until all of its
// fields are filled in. Motors (arm joints, ESC) additionally require a
// configured e-stop. At boot the firmware prints "# ..." lines on serial listing
// exactly which fields are still missing (the Pi ignores lines not starting with '{').
//
// Electrical rules (read before wiring):
//   - Never power a servo, brushless motor or stepper from an ESP32 pin. ESP32 pins
//     carry 3.3 V control SIGNALS only (a few mA). Motors need their own supply,
//     with its ground connected to the ESP32 ground.
//   - Brushless motors only through a suitable ESC; steppers only through their driver.
//   - Every input on an ESP32 pin must stay within 0..3.3 V (e.g. power the joystick
//     module from 3.3 V, not 5 V).
#pragma once

#include <array>
#include <cstdint>

namespace hw {

constexpr int UNKNOWN = -1;

// Logic level (0 = LOW, 1 = HIGH) at which an input is "active" (pressed / touched / engaged).
enum class Pull { Unknown, None, Up, Down };  // internal resistor to enable on an input pin

// ---- Board & serial -------------------------------------------------------------------------
// platformio.ini builds for a generic ESP32 DevKit (board = esp32dev). UNKNOWN: confirm the board.
constexpr long SERIAL_BAUD = 115200;  // contract §7 proposes 115200; the Pi must use the same value

// ---- E-Stop (required before ANY motor can be enabled) ---------------------------------------
enum class EstopKind {
  Unknown,
  MaintainedSwitch,    // latching mushroom switch: stays pressed until twisted/pulled to reset
  MomentaryWithReset,  // momentary button: also needs a separate physical reset input
};
constexpr EstopKind ESTOP_KIND = EstopKind::Unknown;
constexpr int ESTOP_PIN = UNKNOWN;
constexpr int ESTOP_ACTIVE_LEVEL = UNKNOWN;  // level when the e-stop is ENGAGED (NC wiring recommended: open = engaged)
constexpr Pull ESTOP_PULL = Pull::Unknown;
constexpr int ESTOP_RESET_PIN = UNKNOWN;  // only for MomentaryWithReset
constexpr int ESTOP_RESET_ACTIVE_LEVEL = UNKNOWN;
constexpr Pull ESTOP_RESET_PULL = Pull::Unknown;

// ---- Buttons A and B (contract: button_a, button_b) --------------------------------------------
constexpr int BUTTON_A_PIN = UNKNOWN;
constexpr int BUTTON_A_ACTIVE_LEVEL = UNKNOWN;
constexpr Pull BUTTON_A_PULL = Pull::Unknown;
constexpr int BUTTON_B_PIN = UNKNOWN;
constexpr int BUTTON_B_ACTIVE_LEVEL = UNKNOWN;
constexpr Pull BUTTON_B_PULL = Pull::Unknown;

// ---- Touch sensor (contract: touch "tapped") -----------------------------------------------------
enum class TouchKind {
  Unknown,
  DigitalModule,    // touch module with a digital output (e.g. a TTP223-style board)
  Esp32Capacitive,  // bare pad on an ESP32 touch-capable pin, read with touchRead()
};
constexpr TouchKind TOUCH_KIND = TouchKind::Unknown;
constexpr int TOUCH_PIN = UNKNOWN;
constexpr int TOUCH_ACTIVE_LEVEL = UNKNOWN;  // DigitalModule only
constexpr int TOUCH_THRESHOLD = UNKNOWN;     // Esp32Capacitive only: readings below this = touched (measure it)

// ---- Dial (contract: dial events) ---------------------------------------------------------------------
enum class DialKind { Unknown, None, Encoder, JoystickX };
constexpr DialKind DIAL_KIND = DialKind::Unknown;
constexpr bool DIAL_INVERT = false;  // set true if turning clockwise / pushing right comes out negative

// Rotary encoder (DialKind::Encoder)
constexpr int ENCODER_A_PIN = UNKNOWN;
constexpr int ENCODER_B_PIN = UNKNOWN;
constexpr Pull ENCODER_PULL = Pull::Unknown;
constexpr int ENCODER_TRANSITIONS_PER_DETENT = UNKNOWN;  // usually 4 or 2; depends on the encoder model

// Joystick module (DialKind::JoystickX uses the X axis as the dial). Calibrate with the raw readings
// printed at boot: stick released = center, pushed fully left/right = min/max.
constexpr int JOYSTICK_X_PIN = UNKNOWN;  // must be an ADC1 pin
constexpr int JOYSTICK_X_MIN = UNKNOWN;
constexpr int JOYSTICK_X_CENTER = UNKNOWN;
constexpr int JOYSTICK_X_MAX = UNKNOWN;

// ---- LED (contract: set_led color/pattern) ----------------------------------------------------------
// No LED driver is implemented until the type is confirmed (WS2812 strip? common-anode RGB LED? ...).
enum class LedKind { Unknown };
constexpr LedKind LED_KIND = LedKind::Unknown;
constexpr int LED_PIN = UNKNOWN;
constexpr int LED_COUNT = UNKNOWN;

// ---- LCD (contract: set_lcd, 16 characters per line) ----------------------------------------------------
// No LCD driver is implemented until the type is confirmed (e.g. 16x2 character LCD with an I2C backpack?).
enum class LcdKind { Unknown };
constexpr LcdKind LCD_KIND = LcdKind::Unknown;
constexpr int LCD_I2C_ADDRESS = UNKNOWN;
constexpr int LCD_SDA_PIN = UNKNOWN;
constexpr int LCD_SCL_PIN = UNKNOWN;

// ---- Arm joints (contract: arm_pose home / point_blue / point_yellow / point_green / nod) -----------------
enum class JointKind { Servo, Stepper };

struct JointSpec {
  const char* name;
  JointKind kind;
  // Servo joints: signal pin and calibration.
  int servo_signal_pin;
  int servo_pulse_us_at_0deg;
  int servo_pulse_us_at_180deg;
  float servo_min_deg;  // mechanical limits of the joint
  float servo_max_deg;
  float servo_max_speed_dps;
  // Stepper joints: driver pins and motion limits.
  int step_pin;
  int dir_pin;
  int enable_pin;
  int enable_active_level;  // level on EN that ENABLES the driver (often LOW; confirm for the driver used)
  float stepper_max_speed_sps;
  float stepper_accel_sps2;
  int32_t stepper_min_steps;  // software travel limits relative to the power-on (home) position
  int32_t stepper_max_steps;
  // Target for each pose in order: home, point_blue, point_yellow, point_green, nod
  // (degrees for servos, steps for steppers). At power-on the joint is ASSUMED to be at "home".
  std::array<float, 5> pose_targets;
};

// No joints are confirmed. Add one entry per joint once Sahil has confirmed and measured it, e.g.
//   {"base", JointKind::Stepper, UNKNOWN, 0, 0, 0, 0, 0, <STEP>, <DIR>, <EN>, <EN level>,
//    <max sps>, <accel>, <min steps>, <max steps>, {<home>, <blue>, <yellow>, <green>, <nod>}},
constexpr std::array<JointSpec, 0> ARM_JOINTS{};
constexpr uint32_t ARM_POSE_TIMEOUT_MS = 8000;

// ---- Brushless motor ESC ------------------------------------------------------------------------------------
// The contract has no command that spins this motor, so the firmware only ever sends the ESC's
// stop pulse. It is configured here so the signal is held safely once wired.
constexpr int ESC_SIGNAL_PIN = UNKNOWN;
constexpr int ESC_STOP_PULSE_US = UNKNOWN;  // from the ESC manual (often ~1000 us unidirectional, ~1500 us bidirectional)
constexpr int ESC_FULL_PULSE_US = UNKNOWN;
constexpr int ESC_ARM_MS = UNKNOWN;  // how long the ESC needs the stop pulse to arm

}  // namespace hw
