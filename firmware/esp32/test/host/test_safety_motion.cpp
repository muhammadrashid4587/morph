// E-stop latch, heartbeat watchdog, servo/stepper/ESC motion math, arm poses.
// Limits below are TEST VALUES ONLY, not real hardware numbers.
#include <cmath>
#include <cstdint>
#include <string>

#include "check.h"
#include "morph/arm.h"
#include "morph/motion.h"
#include "morph/safety.h"

using namespace morph;

// --- e-stop / watchdog ------------------------------------------------------------------------

TEST(estop_engages_immediately_and_needs_a_stable_physical_reset) {
  EStopLatch latch(500, false);
  CHECK(!latch.update(false, false, 0));
  CHECK(latch.update(true, false, 10));  // one active sample is enough
  CHECK(latch.active());
  CHECK(!latch.update(false, false, 20));
  CHECK(!latch.update(true, false, 300));   // bounced back: reset timer restarts
  CHECK(!latch.update(false, false, 310));
  CHECK(!latch.update(false, false, 809));  // 499 ms inactive: still latched
  CHECK(latch.active());
  CHECK(latch.update(false, false, 810));  // 500 ms inactive: physically reset
  CHECK(!latch.active());
}

TEST(momentary_estop_requires_the_reset_input) {
  EStopLatch latch(500, true);
  latch.update(true, false, 0);
  latch.update(false, false, 100);  // button released: still latched
  CHECK(!latch.update(false, false, 5000));
  CHECK(latch.active());
  CHECK(!latch.update(true, true, 5100));  // reset while e-stop still active does nothing
  CHECK(latch.active());
  latch.update(false, false, 5200);
  CHECK(latch.update(false, true, 5800));
  CHECK(!latch.active());
}

TEST(watchdog_starts_lost_and_times_out_after_1000_ms) {
  LinkWatchdog w(1000);
  CHECK(!w.alive(0));
  CHECK(!w.ever_connected());
  w.on_activity(100);
  CHECK(w.alive(1099));
  CHECK(!w.alive(1100));
  w.on_activity(0xFFFFFF00u);  // wraps around the 32-bit millisecond clock
  CHECK(w.alive(0x00000100u));
}

// --- servo -----------------------------------------------------------------------------------------

static ServoLimits test_servo() { return ServoLimits{500, 2500, 20, 160, 90}; }

TEST(servo_is_off_until_powered_and_respects_limits) {
  ServoAxis s(test_servo(), 90);
  CHECK(!s.enabled());
  CHECK_EQ(s.pulse_us(), 0);
  CHECK(!s.set_target(10));   // below min_deg
  CHECK(!s.set_target(170));  // above max_deg
  CHECK(!s.set_target(std::nanf("")));
  CHECK_EQ(s.target_deg(), 90.0f);
  CHECK(s.set_target(160));
  s.power_on(0);
  CHECK_EQ(s.pulse_us(), 1500);  // 90 deg
}

TEST(servo_moves_at_the_speed_limit) {
  ServoAxis s(test_servo(), 90);
  s.set_target(135);
  s.power_on(0);
  for (uint32_t t = 10; t <= 250; t += 10) s.update(t);
  CHECK(std::fabs(s.position_deg() - (90 + 90 * 0.25f)) < 0.01f);  // 90 deg/s for 250 ms
  CHECK(!s.at_target());
  for (uint32_t t = 260; t <= 1000; t += 10) s.update(t);
  CHECK(s.at_target());
  CHECK_EQ(s.pulse_us(), 2000);
}

TEST(servo_stall_does_not_jump) {
  ServoAxis s(test_servo(), 90);
  s.set_target(160);
  s.power_on(0);
  s.update(5000);  // 5 s without an update counts as at most 100 ms of motion
  CHECK(std::fabs(s.position_deg() - 99.0f) < 0.01f);
}

TEST(servo_power_off_stops_pulses_and_freezes_target) {
  ServoAxis s(test_servo(), 90);
  s.set_target(160);
  s.power_on(0);
  s.update(100);
  s.power_off();
  CHECK(!s.enabled());
  CHECK_EQ(s.pulse_us(), 0);
  CHECK(s.at_target());  // no pending motion when powered again
}

// --- stepper ------------------------------------------------------------------------------------------

static StepperLimits test_stepper() { return StepperLimits{800.0f, 2000.0f, -5000, 5000}; }

struct StepRun {
  int steps = 0;
  uint32_t min_interval_us = UINT32_MAX;
  uint32_t end_us = 0;
};

static StepRun run_planner(StepperPlanner& p, uint32_t start_us, uint32_t limit_us, uint32_t dt_us = 20) {
  StepRun r;
  uint32_t last = 0;
  bool have_last = false;
  for (uint32_t t = start_us; t < limit_us; t += dt_us) {
    const int s = p.poll(t);
    if (s != 0) {
      ++r.steps;
      if (have_last && t - last < r.min_interval_us) r.min_interval_us = t - last;
      last = t;
      have_last = true;
    }
    if (p.idle()) {
      r.end_us = t;
      break;
    }
  }
  return r;
}

TEST(stepper_reaches_target_exactly_within_speed_limit) {
  StepperPlanner p(test_stepper());
  CHECK(p.idle());
  CHECK(p.set_target(1000));
  const StepRun r = run_planner(p, 0, 5000000);
  CHECK(p.idle());
  CHECK_EQ(p.position(), 1000);
  CHECK_EQ(r.steps, 1000);
  CHECK(r.min_interval_us + 20 >= static_cast<uint32_t>(1e6f / 800.0f));  // never faster than max speed
  // Trapezoid: 1000 steps at 800 sps with 2000 sps^2 needs 0.4 s ramps -> ~1.65 s total.
  CHECK(r.end_us > 1500000 && r.end_us < 1900000);
}

TEST(stepper_moves_backwards_and_short_moves) {
  StepperPlanner p(test_stepper());
  p.set_target(-3);
  run_planner(p, 0, 1000000);
  CHECK_EQ(p.position(), -3);
  p.set_target(-2);
  run_planner(p, 1000000, 2000000);
  CHECK_EQ(p.position(), -2);
}

TEST(stepper_rejects_targets_outside_travel_limits) {
  StepperPlanner p(test_stepper());
  CHECK(!p.set_target(5001));
  CHECK(!p.set_target(-5001));
  CHECK_EQ(p.target(), 0);
}

TEST(stepper_stop_now_issues_no_more_steps) {
  StepperPlanner p(test_stepper());
  p.set_target(4000);
  run_planner(p, 0, 300000);
  CHECK(p.speed_sps() > 0);
  p.stop_now();
  CHECK(p.idle());
  int extra = 0;
  for (uint32_t t = 300000; t < 400000; t += 20) extra += p.poll(t) != 0;
  CHECK_EQ(extra, 0);
}

TEST(stepper_reversal_decelerates_then_returns) {
  StepperPlanner p(test_stepper());
  p.set_target(3000);
  run_planner(p, 0, 400000);  // moving fast toward +3000
  const int32_t turned_at = p.position();
  p.set_target(0);
  run_planner(p, 400000, 10000000);
  CHECK(p.idle());
  CHECK_EQ(p.position(), 0);
  CHECK(turned_at > 0);
}

TEST(stepper_does_not_burst_after_a_stalled_loop) {
  StepperPlanner p(test_stepper());
  p.set_target(2000);
  run_planner(p, 0, 500000);  // cruising
  int burst = 0;
  const uint32_t late = 900000;  // loop stalled for 400 ms
  for (uint32_t t = late; t < late + 1000; t += 20) burst += p.poll(t) != 0;
  CHECK(burst <= 2);  // at most ~one interval's worth, not hundreds of catch-up steps
}

// --- ESC ---------------------------------------------------------------------------------------------------

static EscLimits test_esc() { return EscLimits{1000, 2000, 0.3f, 2000}; }

TEST(esc_outputs_stop_pulse_unless_allowed_armed_and_throttled) {
  EscOutput esc(test_esc());
  CHECK_EQ(esc.pulse_us(0.5f, false, 0), 1000);   // not allowed
  CHECK_EQ(esc.pulse_us(0.5f, true, 100), 1000);  // arming: stop pulse held for 2 s
  CHECK_EQ(esc.pulse_us(0.5f, true, 2099), 1000);
  CHECK_EQ(esc.pulse_us(0.5f, true, 2100), 1300);  // capped at max_throttle 0.3
  CHECK_EQ(esc.pulse_us(0.1f, true, 2200), 1100);
  CHECK_EQ(esc.pulse_us(-1.0f, true, 2300), 1000);
  CHECK_EQ(esc.pulse_us(std::nanf(""), true, 2400), 1000);
  CHECK_EQ(esc.pulse_us(0.2f, false, 2500), 1000);  // disallowed: stop, and arming restarts
  CHECK_EQ(esc.pulse_us(0.2f, true, 2600), 1000);
}

TEST(limit_validation_rejects_unknown_zero_values) {
  CHECK(!valid(ServoLimits{}));
  CHECK(!valid(StepperLimits{}));
  CHECK(!valid(EscLimits{}));
  CHECK(valid(test_servo()));
  CHECK(valid(test_stepper()));
  CHECK(valid(test_esc()));
}

// --- arm ------------------------------------------------------------------------------------------------------

static ArmConfig test_arm() {
  ArmConfig arm;
  arm.enabled = true;
  arm.pose_timeout_ms = 8000;
  JointConfig base;
  base.name = "base";
  base.kind = JointKind::Stepper;
  base.stepper = test_stepper();
  base.pose_targets = {0, -400, 0, 400, 0};  // home, blue, yellow, green, nod
  JointConfig tilt;
  tilt.name = "tilt";
  tilt.kind = JointKind::Servo;
  tilt.servo = test_servo();
  tilt.pose_targets = {90, 60, 60, 60, 120};
  arm.joints = {base, tilt};
  return arm;
}

static ArmController::Result run_arm(ArmController& arm, uint32_t& ms, uint32_t max_ms) {
  std::string err;
  for (uint32_t end = ms + max_ms; ms < end; ++ms) {
    for (uint32_t us = 0; us < 1000; us += 50) {
      const ArmController::Result r = arm.update(ms, ms * 1000 + us, err);
      if (r != ArmController::Result::None) return r;
    }
  }
  return ArmController::Result::None;
}

TEST(arm_moves_all_joints_to_a_pose) {
  ArmController arm(test_arm());
  CHECK(arm.state() == ArmState::Idle);
  CHECK(!arm.outputs()[0].enabled && !arm.outputs()[1].enabled);  // off at boot
  std::string err;
  uint32_t ms = 0;
  CHECK(arm.start(ArmPose::PointGreen, ms, err));
  CHECK(arm.state() == ArmState::Moving);
  CHECK(run_arm(arm, ms, 5000) == ArmController::Result::Done);
  CHECK_EQ(arm.joint_position(0), 400.0f);
  CHECK_EQ(arm.joint_position(1), 60.0f);
  CHECK(arm.state() == ArmState::Idle);
  CHECK(arm.outputs()[1].enabled);  // holds the pose
}

TEST(arm_nod_goes_down_and_back) {
  ArmController arm(test_arm());
  std::string err;
  uint32_t ms = 0;
  arm.start(ArmPose::PointBlue, ms, err);
  run_arm(arm, ms, 5000);
  arm.start(ArmPose::Nod, ms, err);
  float max_tilt_seen = 0;
  std::string e;
  ArmController::Result r = ArmController::Result::None;
  for (uint32_t end = ms + 5000; ms < end && r == ArmController::Result::None; ++ms) {
    r = arm.update(ms, ms * 1000, e);
    if (arm.joint_position(1) > max_tilt_seen) max_tilt_seen = arm.joint_position(1);
  }
  CHECK(r == ArmController::Result::Done);
  CHECK_EQ(max_tilt_seen, 120.0f);          // reached the nod pose...
  CHECK_EQ(arm.joint_position(1), 60.0f);      // ...and came back to point_blue
  CHECK_EQ(arm.joint_position(0), -400.0f);
}

TEST(arm_stop_powers_everything_off) {
  ArmController arm(test_arm());
  std::string err;
  uint32_t ms = 0;
  arm.start(ArmPose::PointGreen, ms, err);
  run_arm(arm, ms, 100);
  CHECK(arm.stop_and_power_off());
  CHECK(arm.state() == ArmState::Idle);
  arm.update(ms, ms * 1000, err);
  for (const JointOutput& j : arm.outputs()) {
    CHECK(!j.enabled);
    CHECK_EQ(j.servo_pulse_us, 0);
    CHECK_EQ(j.step, 0);
  }
}

TEST(arm_pose_timeout_fails_and_powers_off) {
  ArmConfig cfg = test_arm();
  cfg.pose_timeout_ms = 50;  // far too short for the move
  ArmController arm(cfg);
  std::string err;
  uint32_t ms = 0;
  arm.start(ArmPose::PointGreen, ms, err);
  CHECK(run_arm(arm, ms, 1000) == ArmController::Result::Failed);
  CHECK(arm.state() == ArmState::Error);
  CHECK(!arm.outputs()[0].enabled);
}

TEST(arm_rejects_out_of_limit_pose_without_moving) {
  ArmConfig cfg = test_arm();
  cfg.joints[1].pose_targets[1] = 175;  // point_blue beyond the servo's 160 deg limit
  ArmController arm(cfg);
  std::string err;
  CHECK(!arm.start(ArmPose::PointBlue, 0, err));
  CHECK(!err.empty());
  CHECK(arm.state() == ArmState::Idle);
  CHECK(!arm.outputs()[0].enabled);
}
