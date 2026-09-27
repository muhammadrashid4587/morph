// MORPH Pi <-> ESP32 serial protocol, exactly as in docs/MORPH_INTEGRATION_CONTRACT.md §3A/§3B.
//
// Pi -> ESP32 commands: hello, set_mode, set_led, set_lcd, arm_pose, stop, heartbeat.
// ESP32 -> Pi events:   hello, button, dial, touch, estop, status, ack.
// One compact JSON object per line, at most 128 bytes per line (newline included).
// Unknown fields are ignored; an unknown cmd gets ack ok:false.
#pragma once

#include <string>
#include <string_view>
#include <vector>

namespace morph {

constexpr int kProtocolVersion = 1;
constexpr size_t kMaxLineBytes = 128;  // including the trailing '\n'

enum class CommandType { Hello, SetMode, SetLed, SetLcd, ArmPose, Stop, Heartbeat };
enum class Mode { Idle, RobotTargeting, Music, Presentation, Teach };
enum class LedColor { Blue, Yellow, Green, White, Red };
enum class LedPattern { Solid, Pulse, Blink, Off };
enum class ArmPose { Home, PointBlue, PointYellow, PointGreen, Nod };
constexpr int kArmPoseCount = 5;
enum class StopReason { Estop, Timeout, Manual };
enum class ButtonId { A, B };
enum class ButtonState { Pressed, Released, LongPress };
enum class ArmState { Idle, Moving, Error };

struct Command {
  CommandType type = CommandType::Heartbeat;
  long long version = 0;  // hello
  Mode mode = Mode::Idle;
  LedColor color = LedColor::White;
  LedPattern pattern = LedPattern::Off;
  std::string line1, line2;  // set_lcd (raw; the display layer sanitizes/truncates)
  ArmPose pose = ArmPose::Home;
  StopReason reason = StopReason::Manual;
};

struct ParsedCommand {
  bool ok = false;
  Command command;
  std::string cmd;    // the "cmd" value when it could be read ("" otherwise), echoed in the ack
  std::string error;  // why the line was rejected
  bool has_cmd() const { return !cmd.empty(); }
};

// Parses and validates one received line (without the trailing newline).
ParsedCommand parse_command(std::string_view line);

const char* to_string(CommandType);
const char* to_string(Mode);
const char* to_string(LedColor);
const char* to_string(LedPattern);
const char* to_string(ArmPose);
const char* to_string(StopReason);
const char* to_string(ButtonState);
const char* to_string(ArmState);

// Event encoders: compact JSON without the newline; always fit kMaxLineBytes - 1.
std::string encode_hello(const std::vector<std::string>& capabilities);
std::string encode_button(ButtonId id, ButtonState state);
std::string encode_dial(int delta);  // delta clamped to -10..10
std::string encode_touch_tapped();
std::string encode_estop(bool active);
std::string encode_status(ArmState arm, bool estop_active);
std::string encode_ack(std::string_view cmd, bool ok, std::string_view message);

}  // namespace morph
