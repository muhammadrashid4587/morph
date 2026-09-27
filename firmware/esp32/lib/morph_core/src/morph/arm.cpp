#include "morph/arm.h"

#include <cmath>

namespace morph {

ArmController::ArmController(const ArmConfig& config) : config_(config) {
  const auto home = static_cast<size_t>(ArmPose::Home);
  for (const JointConfig& j : config_.joints) {
    servos_.emplace_back(j.servo, j.pose_targets[home]);  // assumed to start at home
    StepperPlanner planner(j.stepper);
    planner.set_position(static_cast<int32_t>(std::lround(j.pose_targets[home])));
    steppers_.push_back(planner);
    powered_.push_back(false);
    JointOutput out;
    out.kind = j.kind;
    outputs_.push_back(out);
  }
}

bool ArmController::apply_targets(ArmPose pose, std::string& error) {
  const auto index = static_cast<size_t>(pose);
  for (size_t i = 0; i < config_.joints.size(); ++i) {  // validate everything before moving anything
    const JointConfig& j = config_.joints[i];
    const float t = j.pose_targets[index];
    const bool ok = j.kind == JointKind::Servo ? (t >= j.servo.min_deg && t <= j.servo.max_deg)
                                               : (t >= static_cast<float>(j.stepper.min_steps) &&
                                                  t <= static_cast<float>(j.stepper.max_steps));
    if (!ok) {
      error = "pose target outside joint limits: " + j.name;
      return false;
    }
  }
  for (size_t i = 0; i < config_.joints.size(); ++i) {
    const float t = config_.joints[i].pose_targets[index];
    if (config_.joints[i].kind == JointKind::Servo) {
      servos_[i].set_target(t);
    } else {
      steppers_[i].set_target(static_cast<int32_t>(std::lround(t)));
    }
  }
  return true;
}

void ArmController::power_on_all(uint32_t now_ms) {
  for (size_t i = 0; i < config_.joints.size(); ++i) {
    if (config_.joints[i].kind == JointKind::Servo) servos_[i].power_on(now_ms);
    powered_[i] = true;
  }
}

bool ArmController::start(ArmPose pose, uint32_t now_ms, std::string& error) {
  if (config_.joints.empty()) {
    error = "arm not available";
    return false;
  }
  if (!apply_targets(pose, error)) return false;
  error_ = false;
  moving_ = true;
  nodding_ = pose == ArmPose::Nod;
  nod_returning_ = false;
  if (!nodding_) current_pose_ = pose;  // a nod ends back at current_pose_
  started_ms_ = now_ms;
  power_on_all(now_ms);
  return true;
}

bool ArmController::stop_and_power_off() {
  const bool was_moving = moving_;
  for (size_t i = 0; i < config_.joints.size(); ++i) {
    servos_[i].power_off();
    steppers_[i].stop_now();
    powered_[i] = false;
    outputs_[i].enabled = false;
    outputs_[i].servo_pulse_us = 0;
    outputs_[i].step = 0;
  }
  moving_ = false;
  nodding_ = false;
  nod_returning_ = false;
  return was_moving;
}

bool ArmController::all_at_target() const {
  for (size_t i = 0; i < config_.joints.size(); ++i) {
    const bool done = config_.joints[i].kind == JointKind::Servo ? servos_[i].at_target() : steppers_[i].idle();
    if (!done) return false;
  }
  return true;
}

ArmController::Result ArmController::update(uint32_t now_ms, uint32_t now_us, std::string& error) {
  for (size_t i = 0; i < config_.joints.size(); ++i) {
    JointOutput& out = outputs_[i];
    out.step = 0;
    out.enabled = powered_[i];
    if (!powered_[i]) {
      out.servo_pulse_us = 0;
      continue;
    }
    if (config_.joints[i].kind == JointKind::Servo) {
      servos_[i].update(now_ms);
      out.servo_pulse_us = servos_[i].pulse_us();
    } else {
      out.step = steppers_[i].poll(now_us);
    }
  }
  if (!moving_) return Result::None;

  if (static_cast<uint32_t>(now_ms - started_ms_) > config_.pose_timeout_ms) {
    stop_and_power_off();
    error_ = true;
    error = "pose timeout";
    return Result::Failed;
  }
  if (!all_at_target()) return Result::None;
  if (nodding_ && !nod_returning_) {  // reached the nod pose: go back to where the arm was
    if (!apply_targets(current_pose_, error)) {
      stop_and_power_off();
      error_ = true;
      return Result::Failed;
    }
    nod_returning_ = true;
    return Result::None;
  }
  moving_ = false;
  nodding_ = false;
  nod_returning_ = false;
  return Result::Done;
}

ArmState ArmController::state() const {
  if (error_) return ArmState::Error;
  return moving_ ? ArmState::Moving : ArmState::Idle;
}

float ArmController::joint_position(size_t i) const {
  if (i >= config_.joints.size()) return 0.0f;
  return config_.joints[i].kind == JointKind::Servo ? servos_[i].position_deg()
                                                     : static_cast<float>(steppers_[i].position());
}

}  // namespace morph
