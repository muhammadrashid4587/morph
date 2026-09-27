// End-to-end controller behavior with simulated time: the safety rules and
// the contract's one-ack-per-cmd rule. Hardware values are TEST VALUES ONLY.
#include <cstdint>
#include <string>
#include <vector>

#include "check.h"
#include "morph/controller.h"
#include "morph/json.h"

using namespace morph;

static Config full_config() {
  Config c;
  c.led_enabled = true;
  c.lcd_enabled = true;
  c.button_a.enabled = true;
  c.button_b.enabled = true;
  c.touch.enabled = true;
  c.dial.source = DialSource::Encoder;
  c.estop.configured = true;
  c.arm.enabled = true;
  JointConfig tilt;
  tilt.name = "tilt";
  tilt.kind = JointKind::Servo;
  tilt.servo = ServoLimits{500, 2500, 20, 160, 90};
  tilt.pose_targets = {90, 60, 60, 60, 120};
  c.arm.joints = {tilt};
  c.esc_enabled = true;
  c.esc = EscLimits{1000, 2000, 0.3f, 2000};
  return c;
}

struct Rig {
  Controller ctl;
  InputSample in;
  uint32_t ms = 0;
  std::vector<std::string> sent;

  explicit Rig(const Config& c) : ctl(c) {
    ctl.begin(ms);
    drain();
  }
  void drain() {
    std::string line;
    while (ctl.pop_line(line)) sent.push_back(line);
  }
  void send(const std::string& line) {
    ctl.on_line(line, ms);
    drain();
  }
  void run(uint32_t duration_ms, bool heartbeat = true) {
    for (uint32_t end = ms + duration_ms; ms < end; ++ms) {
      if (heartbeat && ms % 250 == 0) ctl.on_line(R"({"cmd":"heartbeat"})", ms);
      ctl.tick(in, ms, ms * 1000);
      drain();
    }
  }
  std::vector<std::string> take() {
    std::vector<std::string> out;
    out.swap(sent);
    return out;
  }
  int count(const std::string& needle) const {
    int n = 0;
    for (const std::string& s : sent) n += s.find(needle) != std::string::npos;
    return n;
  }
  bool motors_off() const {
    const Outputs& o = ctl.outputs();
    for (const JointOutput& j : o.joints) {
      if (j.enabled || j.servo_pulse_us != 0 || j.step != 0) return false;
    }
    return !o.motion_allowed && (o.esc_pulse_us == 0 || o.esc_pulse_us == 1000);
  }
};

static bool contains(const std::vector<std::string>& lines, const std::string& needle) {
  for (const std::string& s : lines) {
    if (s.find(needle) != std::string::npos) return true;
  }
  return false;
}

TEST(boot_sends_hello_and_motors_are_off_until_the_pi_speaks) {
  Rig r(full_config());
  CHECK_EQ(r.sent.front(),
           std::string(R"({"event":"hello","version":1,"capabilities":["led","lcd","button_a","button_b","dial","arm"]})"));
  r.run(3000, /*heartbeat=*/false);
  CHECK(r.motors_off());
  CHECK_EQ(r.ctl.outputs().lcd_line2, std::string("WAITING FOR PI  "));
  CHECK_EQ(r.ctl.outputs().esc_pulse_us, 1000);  // ESC only ever gets its stop pulse
  r.send(R"({"cmd":"arm_pose","pose":"point_blue"})");  // the first line also brings the link up
  r.run(10);
  CHECK(r.ctl.outputs().motion_allowed);
}

TEST(nothing_is_advertised_when_nothing_is_configured) {
  Rig r{Config{}};
  CHECK_EQ(r.sent.front(), std::string(R"({"event":"hello","version":1,"capabilities":[]})"));
  r.run(100);
  r.send(R"({"cmd":"arm_pose","pose":"home"})");
  CHECK(contains(r.sent, R"({"event":"ack","cmd":"arm_pose","ok":false,"message":"arm not available"})"));
  CHECK(r.motors_off());
}

TEST(arm_is_refused_without_an_estop_even_if_joints_are_configured) {
  Config c = full_config();
  c.estop.configured = false;
  Rig r(c);
  CHECK(!contains(r.sent, "\"arm\""));
  r.run(100);
  r.send(R"({"cmd":"arm_pose","pose":"point_blue"})");
  r.run(100);
  CHECK(contains(r.sent, "arm not available"));
  CHECK(r.motors_off());
}

TEST(arm_pose_moves_and_is_acked_once_when_done) {
  Rig r(full_config());
  r.run(100);
  r.take();
  r.send(R"({"cmd":"arm_pose","pose":"point_blue"})");
  CHECK(r.sent.empty());  // no ack until the motion ends
  r.run(100);
  CHECK(r.ctl.outputs().joints[0].enabled);
  CHECK(r.ctl.arm_state() == ArmState::Moving);
  r.run(1000);
  CHECK_EQ(r.count(R"("cmd":"arm_pose")"), 1);
  CHECK(contains(r.sent, R"({"event":"ack","cmd":"arm_pose","ok":true,"message":""})"));
  CHECK_EQ(r.ctl.outputs().joints[0].servo_pulse_us, 1167);  // 500 + 2000 * 60/180 = 1166.7, rounded
}

TEST(heartbeat_loss_stops_motors_and_shows_pi_lost) {
  Config c = full_config();
  c.arm.joints[0].servo.max_speed_dps = 20;  // slow nod (~3 s) so it is still moving when the Pi goes silent
  Rig r(c);
  r.run(100);
  r.send(R"({"cmd":"arm_pose","pose":"nod"})");
  r.run(200);
  CHECK(r.ctl.outputs().joints[0].enabled);
  r.take();
  r.run(1200, /*heartbeat=*/false);  // the Pi goes silent
  CHECK(r.motors_off());
  CHECK(!r.ctl.link_alive());
  CHECK_EQ(r.ctl.outputs().lcd_line1, std::string("PI LOST         "));
  CHECK(contains(r.sent, R"({"event":"ack","cmd":"arm_pose","ok":false,"message":"pi lost"})"));
  r.run(500);  // heartbeats resume: link back, but nothing moves until a new command
  CHECK(r.ctl.link_alive());
  CHECK(!r.ctl.outputs().joints[0].enabled);
}

TEST(invalid_commands_stop_motors_and_get_one_error_ack) {
  const char* invalid[] = {
      R"({"cmd":"dance"})", R"({"cmd":"set_led","color":"purple","pattern":"solid"})", "garbage", R"({"no":"cmd"})",
  };
  for (const char* line : invalid) {
    Rig r(full_config());
    r.run(100);
    r.send(R"({"cmd":"arm_pose","pose":"point_green"})");
    r.run(100);
    CHECK(r.ctl.outputs().joints[0].enabled);
    r.take();
    r.send(line);
    r.run(1);
    CHECK(!r.ctl.outputs().joints[0].enabled);  // powered off (the link itself is still fine)
    CHECK_EQ(r.ctl.outputs().joints[0].servo_pulse_us, 0);
    // The interrupted arm_pose gets its single ack (ok:false), and the bad line gets one error ack.
    CHECK_EQ(r.count(R"("cmd":"arm_pose","ok":false,"message":"invalid command")"), 1);
    CHECK_EQ(r.count(R"("ok":false)"), 2);
  }
}

TEST(estop_latches_blocks_motion_and_only_a_physical_reset_clears_it) {
  Rig r(full_config());
  r.run(100);
  r.send(R"({"cmd":"arm_pose","pose":"point_yellow"})");
  r.run(100);
  r.take();
  r.in.estop = true;
  r.run(1);
  CHECK(r.motors_off());
  CHECK(contains(r.sent, R"({"event":"estop","active":true})"));
  CHECK(contains(r.sent, R"({"event":"ack","cmd":"arm_pose","ok":false,"message":"estop"})"));
  CHECK_EQ(r.ctl.outputs().lcd_line1, std::string("E-STOP          "));
  CHECK(r.ctl.outputs().led == color_rgb(LedColor::Red));

  r.in.estop = false;  // switch released, but not yet stable
  r.run(100);
  r.send(R"({"cmd":"stop","reason":"estop"})");  // the Pi cannot clear it
  r.send(R"({"cmd":"arm_pose","pose":"home"})");
  CHECK(contains(r.sent, R"({"event":"ack","cmd":"stop","ok":true,"message":""})"));
  CHECK(contains(r.sent, R"({"event":"ack","cmd":"arm_pose","ok":false,"message":"estop active"})"));
  CHECK(r.ctl.estop_active());
  r.run(600);  // inactive for >= 500 ms: physically reset
  CHECK(!r.ctl.estop_active());
  CHECK(contains(r.sent, R"({"event":"estop","active":false})"));
  CHECK(!r.ctl.outputs().joints[0].enabled);  // still off until the Pi commands a pose
  CHECK(r.ctl.outputs().motion_allowed);
}

TEST(estop_held_at_boot_is_reported_and_blocks_motion) {
  Rig r(full_config());
  r.in.estop = true;
  r.run(10);
  CHECK(contains(r.sent, R"({"event":"estop","active":true})"));
  r.send(R"({"cmd":"arm_pose","pose":"home"})");
  CHECK(contains(r.sent, "estop active"));
  CHECK(r.motors_off());
}

TEST(stop_command_powers_off_and_acks) {
  Rig r(full_config());
  r.run(100);
  r.send(R"({"cmd":"arm_pose","pose":"point_green"})");
  r.run(50);
  r.take();
  r.send(R"({"cmd":"stop","reason":"manual"})");
  CHECK_EQ(r.sent, (std::vector<std::string>{R"({"event":"ack","cmd":"arm_pose","ok":false,"message":"stopped"})",
                                             R"({"event":"ack","cmd":"stop","ok":true,"message":""})"}));
  r.run(1);
  CHECK(!r.ctl.outputs().joints[0].enabled);
}

TEST(new_pose_preempts_the_old_one_with_one_ack_each) {
  Rig r(full_config());
  r.run(100);
  r.take();
  r.send(R"({"cmd":"arm_pose","pose":"point_green"})");
  r.run(50);
  r.send(R"({"cmd":"arm_pose","pose":"home"})");
  r.run(2000);
  CHECK_EQ(r.count(R"("cmd":"arm_pose")"), 2);
  CHECK(contains(r.sent, R"("message":"preempted")"));
  CHECK(contains(r.sent, R"({"event":"ack","cmd":"arm_pose","ok":true,"message":""})"));
}

TEST(every_non_heartbeat_command_gets_exactly_one_ack) {
  Rig r(full_config());
  r.run(100);
  r.take();
  const char* cmds[] = {
      R"({"cmd":"hello","version":1})",         R"({"cmd":"set_mode","mode":"presentation"})",
      R"({"cmd":"set_led","color":"blue","pattern":"pulse"})", R"({"cmd":"set_lcd","line1":"A","line2":"B"})",
      R"({"cmd":"stop","reason":"timeout"})",
  };
  for (const char* c : cmds) r.send(c);
  r.send(R"({"cmd":"heartbeat"})");
  int acks = 0;
  for (const std::string& s : r.sent) acks += s.find(R"("event":"ack")") != std::string::npos;
  CHECK_EQ(acks, 5);
  CHECK_EQ(r.ctl.mode(), Mode::Presentation);
  CHECK(contains(r.sent, R"({"event":"hello","version":1,)"));  // hello is answered with hello + ack
}

TEST(hello_with_unsupported_version_is_answered_but_nacked) {
  Rig r(full_config());
  r.take();
  r.send(R"({"cmd":"hello","version":2})");
  CHECK_EQ(r.sent.size(), size_t{2});
  CHECK(contains(r.sent, R"("cmd":"hello","ok":false,"message":"unsupported protocol version")"));
}

TEST(led_and_lcd_follow_the_pi_and_nack_when_not_configured) {
  Rig r(full_config());
  r.run(100);
  r.send(R"({"cmd":"set_led","color":"green","pattern":"solid"})");
  r.send(R"({"cmd":"set_lcd","line1":"TARGET: GREEN? and more","line2":"A:yes B:no"})");
  r.run(1);
  CHECK(r.ctl.outputs().led == color_rgb(LedColor::Green));
  CHECK_EQ(r.ctl.outputs().lcd_line1, std::string("TARGET: GREEN? a"));
  CHECK_EQ(r.ctl.outputs().lcd_line2, std::string("A:yes B:no      "));

  Rig bare{Config{}};
  bare.send(R"({"cmd":"set_led","color":"green","pattern":"solid"})");
  CHECK(contains(bare.sent, R"({"event":"ack","cmd":"set_led","ok":false,"message":"led not configured"})"));
}

TEST(buttons_touch_and_dial_produce_contract_events) {
  Rig r(full_config());
  r.run(100);
  r.take();
  r.in.button_a = true;
  r.run(1200);
  r.in.button_a = false;
  r.run(100);
  CHECK_EQ(r.count(R"({"event":"button","id":"a","state":"pressed"})"), 1);
  CHECK_EQ(r.count(R"({"event":"button","id":"a","state":"long_press"})"), 1);
  CHECK_EQ(r.count(R"({"event":"button","id":"a","state":"released"})"), 1);
  r.in.touch = true;
  r.run(100);
  r.in.touch = false;
  r.run(100);
  CHECK_EQ(r.count(R"({"event":"touch","state":"tapped"})"), 1);
  r.in.encoder_detents = 3;
  r.run(1);
  r.in.encoder_detents = 0;
  r.run(100);
  CHECK_EQ(r.count(R"({"event":"dial","delta":3})"), 1);
}

TEST(status_is_sent_every_500_ms) {
  Rig r(full_config());
  r.take();
  r.run(2000);  // t = 0..1999 ms: status at 500, 1000, 1500
  CHECK_EQ(r.count(R"({"event":"status","arm":"idle","safety":"ok"})"), 3);
}

TEST(every_line_sent_is_valid_compact_json_under_128_bytes) {
  Rig r(full_config());
  r.send(R"({"cmd":")" + std::string(80, 'x') + "\"}");
  r.send(std::string(300, '{'));
  r.send(R"({"cmd":"set_lcd","line1":"ééé","line2":"\"quoted\""})");
  r.in.estop = true;
  r.run(20);
  for (const std::string& line : r.sent) {
    JsonObject o;
    std::string err;
    CHECK(parse_json_object(line, o, err));
    CHECK(line.size() + 1 <= kMaxLineBytes);
    CHECK(line.find('\n') == std::string::npos);
    CHECK(o.find("event") != nullptr);
  }
}
