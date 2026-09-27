#include "morph/config.h"

namespace morph {

std::vector<std::string> arm_problems(const Config& config) {
  std::vector<std::string> problems;
  if (!config.arm.enabled) {
    problems.push_back("arm not enabled");
    return problems;
  }
  if (!config.estop.configured) problems.push_back("no e-stop configured (motors require an e-stop)");
  if (config.arm.joints.empty()) problems.push_back("no joints configured");
  for (const JointConfig& j : config.arm.joints) {
    const bool ok = j.kind == JointKind::Servo ? valid(j.servo) : valid(j.stepper);
    if (!ok) problems.push_back("joint '" + j.name + "' has invalid/unknown limits");
    for (float t : j.pose_targets) {
      const bool in_range = j.kind == JointKind::Servo
                                ? (t >= j.servo.min_deg && t <= j.servo.max_deg)
                                : (t >= static_cast<float>(j.stepper.min_steps) &&
                                   t <= static_cast<float>(j.stepper.max_steps));
      if (!in_range) {
        problems.push_back("joint '" + j.name + "' has a pose target outside its limits");
        break;
      }
    }
  }
  return problems;
}

bool arm_usable(const Config& config) { return arm_problems(config).empty(); }

std::vector<std::string> capabilities(const Config& config) {
  std::vector<std::string> caps;
  if (config.led_enabled) caps.push_back("led");
  if (config.lcd_enabled) caps.push_back("lcd");
  if (config.button_a.enabled) caps.push_back("button_a");
  if (config.button_b.enabled) caps.push_back("button_b");
  if (config.dial.source != DialSource::None) caps.push_back("dial");
  if (arm_usable(config)) caps.push_back("arm");
  return caps;
}

}  // namespace morph
