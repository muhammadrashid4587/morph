// Host simulator for the MORPH ESP32 firmware core: talk to it exactly like the Pi would.
//
//   make sim && ./build/morph_sim            (uses the committed hardware_config.h: nothing enabled)
//   ./build/morph_sim --demo-config          (TEST VALUES: e-stop, buttons, dial and a 1-joint arm enabled)
//
// stdin:  contract command lines, e.g. {"cmd":"arm_pose","pose":"point_blue"}
//         plus simulator directives (not protocol):
//           !press a | !release a | !press b | !release b | !touch | !estop on | !estop off
//           !dial N | !wait MS | !quiet (stop auto-heartbeat) | !heartbeat (resume)
// stdout: exactly what the ESP32 would send (JSON lines).
// stderr: simulated outputs whenever they change (motion allowed, joints, LED, LCD).
//
// Time is simulated (1 ms ticks); the Pi's 250 ms heartbeat is sent automatically unless !quiet.
#include <cstdio>
#include <iostream>
#include <sstream>
#include <string>

#include "board_config.h"
#include "morph/controller.h"

using namespace morph;

namespace {

Config demo_config() {  // TEST VALUES ONLY. Not real hardware numbers.
  Config c;
  c.led_enabled = c.lcd_enabled = true;
  c.button_a.enabled = c.button_b.enabled = c.touch.enabled = true;
  c.dial.source = DialSource::Encoder;
  c.estop.configured = true;
  c.arm.enabled = true;
  JointConfig tilt;
  tilt.name = "demo_tilt";
  tilt.kind = JointKind::Servo;
  tilt.servo = ServoLimits{500, 2500, 20, 160, 90};
  tilt.pose_targets = {90, 60, 75, 45, 120};
  c.arm.joints = {tilt};
  return c;
}

struct Sim {
  Controller ctl;
  InputSample in;
  uint32_t ms = 0;
  bool heartbeat = true;
  std::string last_state;

  explicit Sim(const Config& c) : ctl(c) { ctl.begin(0); }

  void flush() {
    std::string line;
    while (ctl.pop_line(line)) std::printf("%s\n", line.c_str());
    std::fflush(stdout);
    std::ostringstream s;
    const Outputs& o = ctl.outputs();
    s << "[sim t=" << ms << "ms] motion_allowed=" << o.motion_allowed << " estop=" << ctl.estop_active()
      << " link=" << ctl.link_alive() << " lcd=\"" << o.lcd_line1 << "|" << o.lcd_line2 << "\" led=("
      << int(o.led.r) << "," << int(o.led.g) << "," << int(o.led.b) << ")";
    for (size_t i = 0; i < o.joints.size(); ++i) {
      s << " joint" << i << "=" << (o.joints[i].enabled ? "ON" : "off");
    }
    s << " arm=" << to_string(ctl.arm_state());
    const std::string key = s.str().substr(s.str().find(']'));  // ignore the timestamp when comparing
    if (key != last_state) {  // print only when something changes
      std::fprintf(stderr, "%s", s.str().c_str());
      for (size_t i = 0; i < o.joints.size(); ++i) std::fprintf(stderr, " joint%zu_pos=%.1f", i, ctl.arm().joint_position(i));
      std::fprintf(stderr, "\n");
      last_state = key;
    }
  }

  void advance(uint32_t duration) {
    for (uint32_t end = ms + duration; ms < end; ++ms) {
      if (heartbeat && ms % 250 == 0) ctl.on_line(R"({"cmd":"heartbeat"})", ms);
      ctl.tick(in, ms, ms * 1000);
      in.encoder_detents = 0;
      flush();
    }
  }

  void directive(const std::string& d) {
    std::istringstream is(d.substr(1));
    std::string cmd, arg;
    is >> cmd >> arg;
    if (cmd == "press" || cmd == "release") (arg == "b" ? in.button_b : in.button_a) = (cmd == "press");
    else if (cmd == "touch") { in.touch = true; advance(100); in.touch = false; }
    else if (cmd == "estop") in.estop = (arg == "on");
    else if (cmd == "dial") in.encoder_detents = std::stoi(arg.empty() ? "1" : arg);
    else if (cmd == "wait") advance(static_cast<uint32_t>(std::stoul(arg.empty() ? "0" : arg)));
    else if (cmd == "quiet") heartbeat = false;
    else if (cmd == "heartbeat") heartbeat = true;
    else std::fprintf(stderr, "[sim] unknown directive: %s\n", d.c_str());
  }
};

}  // namespace

int main(int argc, char** argv) {
  const bool demo = argc > 1 && std::string(argv[1]) == "--demo-config";
  const board::BoardConfig board_cfg = board::build();
  if (!demo) {
    for (const std::string& m : board_cfg.missing) std::fprintf(stderr, "[sim] disabled: %s\n", m.c_str());
  } else {
    std::fprintf(stderr, "[sim] --demo-config: TEST VALUES, not real hardware\n");
  }
  Sim sim(demo ? demo_config() : board_cfg.config);
  sim.flush();
  sim.advance(1);

  std::string line;
  while (std::getline(std::cin, line)) {
    if (line.empty()) continue;
    if (line[0] == '!') {
      sim.directive(line);
    } else {
      sim.ctl.on_line(line, sim.ms);
    }
    sim.advance(10);  // let 10 ms pass after every input line
  }
  sim.advance(1);
  return 0;
}
