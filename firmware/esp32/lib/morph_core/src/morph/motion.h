// Device-independent motion math for hobby servos, stepper drivers and
// brushless ESCs. No pins here: these compute what the drivers should output.
// Every class starts OFF and has an immediate, unconditional stop.
#pragma once

#include <cstdint>

namespace morph {

// --- Hobby servo -----------------------------------------------------------------------

struct ServoLimits {
  int pulse_us_at_0deg = 0;    // servo calibration (UNKNOWN until measured)
  int pulse_us_at_180deg = 0;  // servo calibration (UNKNOWN until measured)
  float min_deg = 0;           // mechanical joint limits (UNKNOWN until measured)
  float max_deg = 0;
  float max_speed_dps = 0;     // rate limit for every move, degrees per second
};
bool valid(const ServoLimits& l);

class ServoAxis {
 public:
  // `assumed_start_deg`: where the servo is assumed to be at boot. Hobby servos have no
  // position feedback, so the first powered pulse drives the horn there.
  ServoAxis(const ServoLimits& limits, float assumed_start_deg);

  bool set_target(float deg);  // false (and no change) if outside [min_deg, max_deg]
  void power_on(uint32_t now_ms);
  void power_off();            // stop pulses; target frozen at current position
  void update(uint32_t now_ms);  // move toward target at max_speed_dps

  bool enabled() const { return enabled_; }
  bool at_target() const { return position_ == target_; }
  float position_deg() const { return position_; }
  float target_deg() const { return target_; }
  int pulse_us() const;  // 0 while disabled

 private:
  ServoLimits limits_;
  float position_;
  float target_;
  bool enabled_ = false;
  uint32_t last_ms_ = 0;
};

// --- Stepper (STEP/DIR/EN driver) ---------------------------------------------------------

struct StepperLimits {
  float max_speed_sps = 0;  // steps per second
  float accel_sps2 = 0;     // steps per second^2
  int32_t min_steps = 0;    // software travel limits (UNKNOWN until measured)
  int32_t max_steps = 0;
};
bool valid(const StepperLimits& l);

// Constant-acceleration step scheduler (polled). poll() returns +1/-1 when one
// STEP pulse in that direction should be issued now, else 0.
class StepperPlanner {
 public:
  explicit StepperPlanner(const StepperLimits& limits) : limits_(limits) {}

  bool set_target(int32_t target);  // false if outside the travel limits
  void stop_now();                  // e-stop: no more steps, no deceleration
  int poll(uint32_t now_us);
  void set_position(int32_t steps);  // e.g. after homing; only while idle

  int32_t position() const { return position_; }
  int32_t target() const { return target_; }
  float speed_sps() const { return speed_; }
  bool idle() const { return speed_ == 0.0f && position_ == target_; }

 private:
  StepperLimits limits_;
  int32_t position_ = 0;
  int32_t target_ = 0;
  int direction_ = 0;
  float speed_ = 0.0f;
  uint32_t next_step_us_ = 0;
};

// --- Brushless motor through an ESC (RC PWM) -----------------------------------------------

struct EscLimits {
  int stop_pulse_us = 0;      // pulse that means "motor stopped" for this ESC (UNKNOWN)
  int full_pulse_us = 0;      // pulse for full throttle (UNKNOWN)
  float max_throttle = 0.0f;  // software cap, 0..1
  uint32_t arm_ms = 0;        // stop pulse held this long before any throttle (ESC arming)
};
bool valid(const EscLimits& l);

class EscOutput {
 public:
  explicit EscOutput(const EscLimits& limits) : limits_(limits) {}
  // Pulse to send now. Anything other than an allowed, armed, positive throttle -> stop pulse.
  int pulse_us(float throttle, bool allowed, uint32_t now_ms);

 private:
  EscLimits limits_;
  bool arming_ = false;
  uint32_t arming_since_ = 0;
};

}  // namespace morph
