// The firmware's real hardware_config.h (as committed) and serial framing.
#include <string>

#include "board_config.h"
#include "check.h"
#include "morph/controller.h"
#include "morph/line_buffer.h"

using namespace morph;

TEST(committed_hardware_config_enables_nothing) {
  const board::BoardConfig b = board::build();
  const Config& c = b.config;
  CHECK(!c.estop.configured);
  CHECK(!c.button_a.enabled && !c.button_b.enabled && !c.touch.enabled);
  CHECK(c.dial.source == DialSource::None);
  CHECK(!c.led_enabled && !c.lcd_enabled);
  CHECK(!c.arm.enabled && c.arm.joints.empty());
  CHECK(!c.esc_enabled);
  CHECK(capabilities(c).empty());
  CHECK(!b.missing.empty());
  bool mentions_estop = false;
  for (const std::string& m : b.missing) mentions_estop |= m.rfind("e-stop:", 0) == 0;
  CHECK(mentions_estop);
}

TEST(committed_firmware_never_allows_motion) {
  Controller ctl(board::build().config);
  ctl.begin(0);
  for (uint32_t ms = 0; ms < 3000; ++ms) {
    if (ms % 250 == 0) ctl.on_line(R"({"cmd":"heartbeat"})", ms);
    if (ms == 500) ctl.on_line(R"({"cmd":"arm_pose","pose":"point_blue"})", ms);
    ctl.tick(InputSample{}, ms, ms * 1000);
    CHECK(!ctl.outputs().motion_allowed);
    CHECK(ctl.outputs().joints.empty());
    CHECK_EQ(ctl.outputs().esc_pulse_us, 0);  // ESC pin not even driven
  }
}

TEST(pin_rules) {
  CHECK(!board::pin_usable(hw::UNKNOWN));
  CHECK(!board::pin_usable(6));  // classic ESP32 flash pins
  CHECK(!board::pin_usable(11));
  CHECK(board::pin_usable(4));
  CHECK(!board::input_known(4, hw::UNKNOWN, hw::Pull::Up));
  CHECK(!board::input_known(4, 0, hw::Pull::Unknown));
  CHECK(board::input_known(4, 0, hw::Pull::Up));
}

TEST(joint_spec_with_unknown_fields_is_reported) {
  const hw::JointSpec spec{"tilt", hw::JointKind::Servo, hw::UNKNOWN, 0, 0, 0, 0, 0, hw::UNKNOWN, hw::UNKNOWN,
                           hw::UNKNOWN, hw::UNKNOWN, 0, 0, 0, 0, {0, 0, 0, 0, 0}};
  std::vector<std::string> missing;
  board::joint_config(spec, missing);
  CHECK_EQ(missing.size(), size_t{2});  // signal pin + calibration/limits
}

TEST(line_buffer_frames_lines_and_bounds_memory) {
  LineBuffer rx;
  int lines = 0;
  for (char c : std::string("{\"cmd\":\"heartbeat\"}\r\n{\"a\":1}\n")) {
    if (rx.push(c)) {
      ++lines;
      if (lines == 1) CHECK_EQ(rx.line(), std::string("{\"cmd\":\"heartbeat\"}\r"));  // '\r' stripped later
    }
  }
  CHECK_EQ(lines, 2);
  for (int i = 0; i < 10000; ++i) CHECK(!rx.push('x'));
  CHECK(rx.push('\n'));
  CHECK_EQ(rx.line().size(), LineBuffer::kCapacity);
  CHECK(!parse_command(rx.line()).ok);  // rejected as too long
}
