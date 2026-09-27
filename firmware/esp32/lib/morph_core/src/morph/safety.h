// Safety primitives: a latched emergency stop and the Pi heartbeat watchdog.
#pragma once

#include <cstdint>

namespace morph {

// Latched E-Stop.
//
// Engages immediately when the e-stop input is active (debounce is NOT applied
// to engaging: one active sample stops the motors). Once engaged it stays
// engaged until a *physical* reset:
//   - the e-stop input has read inactive continuously for reset_stable_ms, and
//   - if requires_reset_input (e.g. a momentary e-stop button), the separate
//     reset input is pressed while the e-stop input is inactive.
// Nothing sent over serial can clear it (contract: "The Pi cannot clear an e-stop").
class EStopLatch {
 public:
  explicit EStopLatch(uint32_t reset_stable_ms = 500, bool requires_reset_input = false)
      : reset_stable_ms_(reset_stable_ms), requires_reset_input_(requires_reset_input) {}

  // Returns true when active() changed on this sample.
  bool update(bool estop_input_active, bool reset_input_active, uint32_t now_ms);
  bool active() const { return active_; }

 private:
  uint32_t reset_stable_ms_;
  bool requires_reset_input_;
  bool active_ = false;
  bool inactive_tracking_ = false;
  uint32_t inactive_since_ = 0;
};

// Contract §3A heartbeat: if no valid line arrives for timeout_ms, the link is lost.
// The link starts lost (motors stay off until the Pi has spoken).
class LinkWatchdog {
 public:
  explicit LinkWatchdog(uint32_t timeout_ms = 1000) : timeout_ms_(timeout_ms) {}
  void on_activity(uint32_t now_ms) {
    seen_ = true;
    last_ = now_ms;
  }
  bool alive(uint32_t now_ms) const {
    return seen_ && static_cast<uint32_t>(now_ms - last_) < timeout_ms_;
  }
  bool ever_connected() const { return seen_; }

 private:
  uint32_t timeout_ms_;
  bool seen_ = false;
  uint32_t last_ = 0;
};

}  // namespace morph
