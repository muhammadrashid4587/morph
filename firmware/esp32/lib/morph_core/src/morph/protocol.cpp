#include "morph/protocol.h"

#include <array>
#include <utility>

#include "morph/json.h"

namespace morph {
namespace {

template <typename Enum, size_t N>
bool lookup(const std::array<std::pair<const char*, Enum>, N>& table, std::string_view name, Enum& out) {
  for (const auto& entry : table) {
    if (name == entry.first) {
      out = entry.second;
      return true;
    }
  }
  return false;
}

template <typename Enum, size_t N>
const char* name_of(const std::array<std::pair<const char*, Enum>, N>& table, Enum value) {
  for (const auto& entry : table) {
    if (entry.second == value) return entry.first;
  }
  return "?";
}

const std::array<std::pair<const char*, CommandType>, 7> kCommands{{
    {"hello", CommandType::Hello},
    {"set_mode", CommandType::SetMode},
    {"set_led", CommandType::SetLed},
    {"set_lcd", CommandType::SetLcd},
    {"arm_pose", CommandType::ArmPose},
    {"stop", CommandType::Stop},
    {"heartbeat", CommandType::Heartbeat},
}};
const std::array<std::pair<const char*, Mode>, 5> kModes{{
    {"idle", Mode::Idle},
    {"robot_targeting", Mode::RobotTargeting},
    {"music", Mode::Music},
    {"presentation", Mode::Presentation},
    {"teach", Mode::Teach},
}};
const std::array<std::pair<const char*, LedColor>, 5> kColors{{
    {"blue", LedColor::Blue},
    {"yellow", LedColor::Yellow},
    {"green", LedColor::Green},
    {"white", LedColor::White},
    {"red", LedColor::Red},
}};
const std::array<std::pair<const char*, LedPattern>, 4> kPatterns{{
    {"solid", LedPattern::Solid},
    {"pulse", LedPattern::Pulse},
    {"blink", LedPattern::Blink},
    {"off", LedPattern::Off},
}};
const std::array<std::pair<const char*, ArmPose>, kArmPoseCount> kPoses{{
    {"home", ArmPose::Home},
    {"point_blue", ArmPose::PointBlue},
    {"point_yellow", ArmPose::PointYellow},
    {"point_green", ArmPose::PointGreen},
    {"nod", ArmPose::Nod},
}};
const std::array<std::pair<const char*, StopReason>, 3> kReasons{{
    {"estop", StopReason::Estop},
    {"timeout", StopReason::Timeout},
    {"manual", StopReason::Manual},
}};
const std::array<std::pair<const char*, ButtonState>, 3> kButtonStates{{
    {"pressed", ButtonState::Pressed},
    {"released", ButtonState::Released},
    {"long_press", ButtonState::LongPress},
}};
const std::array<std::pair<const char*, ArmState>, 3> kArmStates{{
    {"idle", ArmState::Idle},
    {"moving", ArmState::Moving},
    {"error", ArmState::Error},
}};

// Reads a required string field whose value must be one of `table`.
template <typename Enum, size_t N>
bool read_enum(const JsonObject& obj, const char* key, const std::array<std::pair<const char*, Enum>, N>& table,
               Enum& out, std::string& error) {
  const JsonField* f = obj.find(key);
  if (f == nullptr) {
    error = std::string("missing field '") + key + "'";
    return false;
  }
  if (f->kind != JsonField::Kind::String || !lookup(table, f->text, out)) {
    error = std::string("invalid ") + key;
    return false;
  }
  return true;
}

bool read_string(const JsonObject& obj, const char* key, std::string& out, std::string& error) {
  const JsonField* f = obj.find(key);
  if (f == nullptr || f->kind != JsonField::Kind::String) {
    error = std::string("field '") + key + "' must be a string";
    return false;
  }
  out = f->text;
  return true;
}

// Longest prefix of at most `max_bytes` that does not split a UTF-8 character.
std::string_view utf8_prefix(std::string_view s, size_t max_bytes) {
  if (s.size() <= max_bytes) return s;
  size_t n = max_bytes;
  while (n > 0 && (static_cast<unsigned char>(s[n]) & 0xC0) == 0x80) --n;  // s[n] continues a character
  return s.substr(0, n);
}

}  // namespace

ParsedCommand parse_command(std::string_view line) {
  ParsedCommand result;
  if (line.size() + 1 > kMaxLineBytes) {
    result.error = "line too long";
    return result;
  }
  JsonObject obj;
  std::string json_error;
  if (!parse_json_object(line, obj, json_error)) {
    result.error = "malformed json: " + json_error;
    return result;
  }
  const JsonField* cmd = obj.find("cmd");
  if (cmd == nullptr || cmd->kind != JsonField::Kind::String || cmd->text.empty()) {
    result.error = "missing cmd";
    return result;
  }
  result.cmd = cmd->text;
  Command& c = result.command;
  if (!lookup(kCommands, cmd->text, c.type)) {
    result.error = "unknown cmd";
    return result;
  }

  std::string& err = result.error;
  bool ok = true;
  switch (c.type) {
    case CommandType::Hello: {
      const JsonField* v = obj.find("version");
      if (v == nullptr || v->kind != JsonField::Kind::Integer) {
        err = "field 'version' must be an integer";
        ok = false;
      } else {
        c.version = v->integer;
      }
      break;
    }
    case CommandType::SetMode:
      ok = read_enum(obj, "mode", kModes, c.mode, err);
      break;
    case CommandType::SetLed:
      ok = read_enum(obj, "color", kColors, c.color, err) && read_enum(obj, "pattern", kPatterns, c.pattern, err);
      break;
    case CommandType::SetLcd:
      ok = read_string(obj, "line1", c.line1, err) && read_string(obj, "line2", c.line2, err);
      break;
    case CommandType::ArmPose:
      ok = read_enum(obj, "pose", kPoses, c.pose, err);
      break;
    case CommandType::Stop:
      ok = read_enum(obj, "reason", kReasons, c.reason, err);
      break;
    case CommandType::Heartbeat:
      break;
  }
  result.ok = ok;
  return result;
}

const char* to_string(CommandType v) { return name_of(kCommands, v); }
const char* to_string(Mode v) { return name_of(kModes, v); }
const char* to_string(LedColor v) { return name_of(kColors, v); }
const char* to_string(LedPattern v) { return name_of(kPatterns, v); }
const char* to_string(ArmPose v) { return name_of(kPoses, v); }
const char* to_string(StopReason v) { return name_of(kReasons, v); }
const char* to_string(ButtonState v) { return name_of(kButtonStates, v); }
const char* to_string(ArmState v) { return name_of(kArmStates, v); }

std::string encode_hello(const std::vector<std::string>& capabilities) {
  std::string out = "{\"event\":\"hello\",\"version\":" + std::to_string(kProtocolVersion) + ",\"capabilities\":[";
  for (size_t i = 0; i < capabilities.size(); ++i) {
    if (i) out.push_back(',');
    append_json_string(out, capabilities[i]);
  }
  out += "]}";
  return out;
}

std::string encode_button(ButtonId id, ButtonState state) {
  return std::string("{\"event\":\"button\",\"id\":\"") + (id == ButtonId::A ? "a" : "b") + "\",\"state\":\"" +
         to_string(state) + "\"}";
}

std::string encode_dial(int delta) {
  if (delta > 10) delta = 10;
  if (delta < -10) delta = -10;
  return "{\"event\":\"dial\",\"delta\":" + std::to_string(delta) + "}";
}

std::string encode_touch_tapped() { return "{\"event\":\"touch\",\"state\":\"tapped\"}"; }

std::string encode_estop(bool active) {
  return std::string("{\"event\":\"estop\",\"active\":") + (active ? "true" : "false") + "}";
}

std::string encode_status(ArmState arm, bool estop_active) {
  return std::string("{\"event\":\"status\",\"arm\":\"") + to_string(arm) + "\",\"safety\":\"" +
         (estop_active ? "estop" : "ok") + "\"}";
}

std::string encode_ack(std::string_view cmd, bool ok, std::string_view message) {
  // Shorten message (then cmd) until the line fits; escaping can only grow text, so re-encode each time.
  std::string_view c = utf8_prefix(cmd, 32);
  std::string_view m = message;
  while (true) {
    std::string out = "{\"event\":\"ack\",\"cmd\":";
    append_json_string(out, c);
    out += ok ? ",\"ok\":true,\"message\":" : ",\"ok\":false,\"message\":";
    append_json_string(out, m);
    out.push_back('}');
    if (out.size() + 1 <= kMaxLineBytes) return out;
    if (!m.empty()) {
      m = utf8_prefix(m, m.size() - 1);
    } else {
      c = utf8_prefix(c, c.size() - 1);
    }
  }
}

}  // namespace morph
