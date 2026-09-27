// Arm poses (contract: home, point_blue, point_yellow, point_green, nod) over
// servo and/or stepper joints. "nod" moves to the nod pose and back to where
// the arm was, then completes. The caller decides when motion is allowed
// (e-stop, heartbeat) and must call stop_and_power_off() otherwise.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "morph/config.h"

namespace morph {

struct JointOutput {
  JointKind kind = JointKind::Servo;
  bool enabled = false;    // servo: pulses on; stepper: driver EN active
  int servo_pulse_us = 0;  // servo only; 0 when disabled
  int step = 0;            // stepper only: issue one STEP pulse now in this direction (-1/+1), 0 = none
};

class ArmController {
 public:
  explicit ArmController(const ArmConfig& config);

  // Starts a pose (powering joints on). False with `error` if a target is outside limits.
  bool start(ArmPose pose, uint32_t now_ms, std::string& error);
  // Immediately stops all joints and powers them off. Returns true if a pose was in progress.
  bool stop_and_power_off();

  enum class Result { None, Done, Failed };
  // Advances motion. Call often (stepper timing uses now_us). Fills joint outputs.
  Result update(uint32_t now_ms, uint32_t now_us, std::string& error);

  ArmState state() const;
  bool moving() const { return moving_; }
  const std::vector<JointOutput>& outputs() const { return outputs_; }
  size_t joint_count() const { return config_.joints.size(); }
  float joint_position(size_t i) const;  // degrees or steps

 private:
  bool apply_targets(ArmPose pose, std::string& error);
  bool all_at_target() const;
  void power_on_all(uint32_t now_ms);

  ArmConfig config_;
  std::vector<ServoAxis> servos_;          // one per joint (unused for stepper joints)
  std::vector<StepperPlanner> steppers_;   // one per joint (unused for servo joints)
  std::vector<bool> powered_;
  std::vector<JointOutput> outputs_;
  bool moving_ = false;
  bool error_ = false;
  bool nodding_ = false;
  bool nod_returning_ = false;
  ArmPose current_pose_ = ArmPose::Home;  // last completed non-nod pose
  uint32_t started_ms_ = 0;
};

}  // namespace morph
