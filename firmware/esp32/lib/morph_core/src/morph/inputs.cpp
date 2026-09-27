#include "morph/inputs.h"

namespace morph {

bool Debouncer::update(bool raw, uint32_t now_ms) {
  if (!started_) {
    started_ = true;
    state_ = candidate_ = raw;
    candidate_since_ = now_ms;
    return false;
  }
  if (raw != candidate_) {
    candidate_ = raw;
    candidate_since_ = now_ms;
  }
  if (candidate_ != state_ && static_cast<uint32_t>(now_ms - candidate_since_) >= stable_ms_) {
    state_ = candidate_;
    return true;
  }
  return false;
}

ButtonEdge ButtonTracker::update(bool raw_pressed, uint32_t now_ms) {
  if (debouncer_.update(raw_pressed, now_ms)) {
    if (debouncer_.state()) {
      armed_ = true;
      long_sent_ = false;
      pressed_at_ = now_ms;
      return ButtonEdge::Pressed;
    }
    if (armed_) {  // ignore the release of a button that was already held at boot
      armed_ = false;
      return ButtonEdge::Released;
    }
    return ButtonEdge::None;
  }
  if (armed_ && !long_sent_ && static_cast<uint32_t>(now_ms - pressed_at_) >= long_press_ms_) {
    long_sent_ = true;
    return ButtonEdge::LongPress;
  }
  return ButtonEdge::None;
}

bool TapDetector::update(bool raw_touched, uint32_t now_ms) {
  if (!debouncer_.update(raw_touched, now_ms)) return false;
  if (debouncer_.state()) {
    armed_ = true;
    touched_at_ = now_ms;
    return false;
  }
  const bool tap = armed_ && static_cast<uint32_t>(now_ms - touched_at_) <= max_tap_ms_;
  armed_ = false;
  return tap;
}

float normalize_axis(int raw, const AxisCalibration& cal) {
  float value = 0.0f;
  if (raw >= cal.raw_center) {
    const int span = cal.raw_max - cal.raw_center;
    value = span > 0 ? static_cast<float>(raw - cal.raw_center) / static_cast<float>(span) : 0.0f;
  } else {
    const int span = cal.raw_center - cal.raw_min;
    value = span > 0 ? -static_cast<float>(cal.raw_center - raw) / static_cast<float>(span) : 0.0f;
  }
  if (value > 1.0f) value = 1.0f;
  if (value < -1.0f) value = -1.0f;
  const float magnitude = value < 0 ? -value : value;
  if (magnitude <= cal.deadzone || cal.deadzone >= 1.0f) return 0.0f;
  float scaled = (magnitude - cal.deadzone) / (1.0f - cal.deadzone);
  if (value < 0) scaled = -scaled;
  return cal.inverted ? -scaled : scaled;
}

int JoystickDial::update(float x, uint32_t now_ms) {
  const int direction = x >= threshold_ ? 1 : (x <= -threshold_ ? -1 : 0);
  if (direction == 0) {
    direction_ = 0;
    return 0;
  }
  if (direction != direction_) {  // newly pushed (or flipped): one detent now
    direction_ = direction;
    next_at_ = now_ms + initial_delay_ms_;
    return direction;
  }
  if (static_cast<int32_t>(now_ms - next_at_) >= 0) {  // held: auto-repeat
    next_at_ = now_ms + repeat_ms_;
    return direction;
  }
  return 0;
}

int QuadratureDecoder::update(bool a, bool b) {
  // Index: previous state (2 bits) << 2 | new state (2 bits). +1 = one clockwise transition.
  static const int8_t kTable[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};
  const uint8_t now = static_cast<uint8_t>((a ? 2 : 0) | (b ? 1 : 0));
  if (!started_) {
    started_ = true;
    state_ = now;
    return 0;
  }
  partial_ += kTable[(state_ << 2) | now];
  state_ = now;
  if (per_detent_ <= 0) return 0;
  const int detents = partial_ / per_detent_;
  partial_ -= detents * per_detent_;
  return detents;
}

void DialAccumulator::add(int detents) {
  constexpr int kMaxPending = 100;
  pending_ += detents;
  if (pending_ > kMaxPending) pending_ = kMaxPending;
  if (pending_ < -kMaxPending) pending_ = -kMaxPending;
}

int DialAccumulator::poll(uint32_t now_ms) {
  if (pending_ == 0) return 0;
  if (sent_once_ && static_cast<uint32_t>(now_ms - last_sent_) < min_interval_ms_) return 0;
  int delta = pending_;
  if (delta > max_delta_) delta = max_delta_;
  if (delta < -max_delta_) delta = -max_delta_;
  pending_ -= delta;
  sent_once_ = true;
  last_sent_ = now_ms;
  return delta;
}

}  // namespace morph
