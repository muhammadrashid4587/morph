// Device-independent input processing: debouncing, button/touch events,
// joystick normalization, rotary-encoder decoding and dial rate limiting.
// Callers pass already-polarity-corrected booleans (true = pressed/touched)
// and timestamps; all arithmetic is wrap-safe on uint32_t millisecond clocks.
#pragma once

#include <cstdint>

namespace morph {

// A raw boolean that must hold a new value for `stable_ms` before it is accepted.
// The first sample initializes the state without reporting a change (no events at boot).
class Debouncer {
 public:
  explicit Debouncer(uint32_t stable_ms = 30) : stable_ms_(stable_ms) {}
  // Returns true when the debounced state changed on this sample.
  bool update(bool raw, uint32_t now_ms);
  bool state() const { return state_; }

 private:
  uint32_t stable_ms_;
  bool started_ = false;
  bool state_ = false;
  bool candidate_ = false;
  uint32_t candidate_since_ = 0;
};

enum class ButtonEdge { None, Pressed, Released, LongPress };

// Contract §3 button semantics: pressed, then long_press once held >= long_press_ms,
// then released. A button already held at boot produces no events until released and pressed again.
class ButtonTracker {
 public:
  explicit ButtonTracker(uint32_t debounce_ms = 30, uint32_t long_press_ms = 1000)
      : debouncer_(debounce_ms), long_press_ms_(long_press_ms) {}
  ButtonEdge update(bool raw_pressed, uint32_t now_ms);
  bool pressed() const { return armed_; }

 private:
  Debouncer debouncer_;
  uint32_t long_press_ms_;
  bool armed_ = false;  // a press we reported and have not released yet
  bool long_sent_ = false;
  uint32_t pressed_at_ = 0;
};

// A touch pad "tap": touched, then released within max_tap_ms (like Button A's short press).
class TapDetector {
 public:
  explicit TapDetector(uint32_t debounce_ms = 30, uint32_t max_tap_ms = 800)
      : debouncer_(debounce_ms), max_tap_ms_(max_tap_ms) {}
  bool update(bool raw_touched, uint32_t now_ms);  // true on a tap

 private:
  Debouncer debouncer_;
  uint32_t max_tap_ms_;
  bool armed_ = false;
  uint32_t touched_at_ = 0;
};

struct AxisCalibration {
  int raw_min = 0;
  int raw_center = 2048;
  int raw_max = 4095;
  float deadzone = 0.15f;  // fraction of travel around center reported as 0
  bool inverted = false;
};

// Maps a raw ADC reading to [-1, 1] with a center deadzone (output rescaled beyond it).
float normalize_axis(int raw, const AxisCalibration& cal);

// Turns a joystick axis into dial detents: one detent when pushed past `threshold`,
// then auto-repeat every `repeat_ms` after `initial_delay_ms` while held.
class JoystickDial {
 public:
  JoystickDial(float threshold = 0.6f, uint32_t initial_delay_ms = 400, uint32_t repeat_ms = 150)
      : threshold_(threshold), initial_delay_ms_(initial_delay_ms), repeat_ms_(repeat_ms) {}
  int update(float x, uint32_t now_ms);  // detents this sample: -1, 0 or +1

 private:
  float threshold_;
  uint32_t initial_delay_ms_;
  uint32_t repeat_ms_;
  int direction_ = 0;
  uint32_t next_at_ = 0;
};

// Quadrature (A/B) rotary encoder decoder. Invalid (skipped) transitions are ignored.
class QuadratureDecoder {
 public:
  explicit QuadratureDecoder(int transitions_per_detent = 4) : per_detent_(transitions_per_detent) {}
  int update(bool a, bool b);  // detents completed by this sample (+ = clockwise)

 private:
  int per_detent_;
  bool started_ = false;
  uint8_t state_ = 0;
  int partial_ = 0;
};

// Contract §3B dial event: at most one event per min_interval_ms, delta limited to ±max_delta.
// Detents beyond the limit are carried into the next event (bounded to avoid runaway).
class DialAccumulator {
 public:
  explicit DialAccumulator(uint32_t min_interval_ms = 50, int max_delta = 10)
      : min_interval_ms_(min_interval_ms), max_delta_(max_delta) {}
  void add(int detents);
  // Returns the delta to send now, or 0 when there is nothing to send / it is too soon.
  int poll(uint32_t now_ms);
  int pending() const { return pending_; }

 private:
  uint32_t min_interval_ms_;
  int max_delta_;
  int pending_ = 0;
  bool sent_once_ = false;
  uint32_t last_sent_ = 0;
};

}  // namespace morph
