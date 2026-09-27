#include "morph/safety.h"

namespace morph {

bool EStopLatch::update(bool estop_input_active, bool reset_input_active, uint32_t now_ms) {
  if (estop_input_active) {
    inactive_tracking_ = false;
    if (!active_) {
      active_ = true;
      return true;
    }
    return false;
  }
  if (!active_) return false;

  if (!inactive_tracking_) {
    inactive_tracking_ = true;
    inactive_since_ = now_ms;
  }
  const bool settled = static_cast<uint32_t>(now_ms - inactive_since_) >= reset_stable_ms_;
  if (settled && (!requires_reset_input_ || reset_input_active)) {
    active_ = false;
    inactive_tracking_ = false;
    return true;
  }
  return false;
}

}  // namespace morph
