#include "morph/motion.h"

#include <cmath>

namespace morph {

// --- servo ---------------------------------------------------------------------------------

bool valid(const ServoLimits& l) {
  return l.pulse_us_at_0deg >= 400 && l.pulse_us_at_0deg <= 2600 && l.pulse_us_at_180deg >= 400 &&
         l.pulse_us_at_180deg <= 2600 && l.pulse_us_at_0deg != l.pulse_us_at_180deg && l.min_deg >= 0 &&
         l.max_deg <= 180 && l.min_deg < l.max_deg && l.max_speed_dps > 0;
}

ServoAxis::ServoAxis(const ServoLimits& limits, float assumed_start_deg) : limits_(limits) {
  float start = assumed_start_deg;
  if (start < limits.min_deg) start = limits.min_deg;
  if (start > limits.max_deg) start = limits.max_deg;
  position_ = target_ = start;
}

bool ServoAxis::set_target(float deg) {
  if (!(deg >= limits_.min_deg && deg <= limits_.max_deg)) return false;  // also rejects NaN
  target_ = deg;
  return true;
}

void ServoAxis::power_on(uint32_t now_ms) {
  if (!enabled_) {
    enabled_ = true;
    last_ms_ = now_ms;
  }
}

void ServoAxis::power_off() {
  enabled_ = false;
  target_ = position_;
}

void ServoAxis::update(uint32_t now_ms) {
  if (!enabled_) return;
  uint32_t dt_ms = now_ms - last_ms_;
  last_ms_ = now_ms;
  if (dt_ms > 100) dt_ms = 100;  // a stalled loop must not turn into one big jump
  const float step = limits_.max_speed_dps * static_cast<float>(dt_ms) / 1000.0f;
  const float error = target_ - position_;
  if (std::fabs(error) <= step) {
    position_ = target_;
  } else {
    position_ += error > 0 ? step : -step;
  }
}

int ServoAxis::pulse_us() const {
  if (!enabled_) return 0;
  const float span = static_cast<float>(limits_.pulse_us_at_180deg - limits_.pulse_us_at_0deg);
  return static_cast<int>(std::lround(static_cast<float>(limits_.pulse_us_at_0deg) + span * position_ / 180.0f));
}

// --- stepper -------------------------------------------------------------------------------

bool valid(const StepperLimits& l) {
  return l.max_speed_sps > 0 && l.accel_sps2 > 0 && l.min_steps < l.max_steps;
}

bool StepperPlanner::set_target(int32_t target) {
  if (target < limits_.min_steps || target > limits_.max_steps) return false;
  target_ = target;
  return true;
}

void StepperPlanner::stop_now() {
  target_ = position_;
  speed_ = 0.0f;
  direction_ = 0;
}

void StepperPlanner::set_position(int32_t steps) {
  if (!idle()) return;
  position_ = target_ = steps;
}

int StepperPlanner::poll(uint32_t now_us) {
  if (idle()) return 0;
  const float a = limits_.accel_sps2;
  if (speed_ == 0.0f) {  // start from rest toward the target: first step now
    if (position_ == target_) return 0;
    direction_ = target_ > position_ ? 1 : -1;
    speed_ = std::sqrt(2.0f * a);
    if (speed_ > limits_.max_speed_sps) speed_ = limits_.max_speed_sps;
    next_step_us_ = now_us;
  }
  if (static_cast<int32_t>(now_us - next_step_us_) < 0) return 0;

  const int step = direction_;
  position_ += step;

  // Speed for the next step, as v^2 (each step may change v^2 by at most 2a = constant acceleration).
  const int32_t ahead = (target_ - position_) * direction_;  // steps left in this direction
  const float v2_now = speed_ * speed_;
  float v2;
  if (ahead > 0) {
    // Fastest of: accelerate, the speed from which we can still stop exactly on target
    // (v^2 = 2a * steps left), and the speed limit. Arrives at the target at sqrt(2a).
    v2 = v2_now + 2.0f * a;
    const float brake_v2 = 2.0f * a * static_cast<float>(ahead);
    const float max_v2 = limits_.max_speed_sps * limits_.max_speed_sps;
    if (brake_v2 < v2) v2 = brake_v2;
    if (max_v2 < v2) v2 = max_v2;
  } else {
    // On target (normal arrival: v^2 <= 2a, so this stops) or past it because the target
    // moved behind us mid-move: brake at the acceleration limit, then turn around.
    v2 = v2_now - 2.0f * a;
  }
  if (v2 <= 1e-3f) {
    speed_ = 0.0f;  // stopped; the next poll restarts toward the (possibly new) target
    direction_ = 0;
    return step;
  }
  speed_ = std::sqrt(v2);

  const uint32_t interval = static_cast<uint32_t>(1e6f / speed_);
  if (static_cast<int32_t>(now_us - next_step_us_) > static_cast<int32_t>(interval)) {
    next_step_us_ = now_us + interval;  // we were late: do not burst to catch up
  } else {
    next_step_us_ += interval;
  }
  return step;
}

// --- ESC -----------------------------------------------------------------------------------

bool valid(const EscLimits& l) {
  return l.stop_pulse_us >= 800 && l.stop_pulse_us <= 2200 && l.full_pulse_us >= 800 &&
         l.full_pulse_us <= 2200 && l.full_pulse_us != l.stop_pulse_us && l.max_throttle >= 0.0f &&
         l.max_throttle <= 1.0f;
}

int EscOutput::pulse_us(float throttle, bool allowed, uint32_t now_ms) {
  if (!allowed) {
    arming_ = false;
    return limits_.stop_pulse_us;
  }
  if (!arming_) {
    arming_ = true;
    arming_since_ = now_ms;
  }
  if (static_cast<uint32_t>(now_ms - arming_since_) < limits_.arm_ms) return limits_.stop_pulse_us;
  if (!(throttle > 0.0f)) return limits_.stop_pulse_us;  // also rejects NaN
  if (throttle > limits_.max_throttle) throttle = limits_.max_throttle;
  const float span = static_cast<float>(limits_.full_pulse_us - limits_.stop_pulse_us);
  return static_cast<int>(std::lround(static_cast<float>(limits_.stop_pulse_us) + span * throttle));
}

}  // namespace morph
