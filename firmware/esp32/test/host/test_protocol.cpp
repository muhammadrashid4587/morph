// JSON parsing and the contract §3A/§3B wire format.
#include <string>

#include "check.h"
#include "morph/json.h"
#include "morph/protocol.h"

using namespace morph;

// --- json ------------------------------------------------------------------------------------

TEST(json_parses_scalar_fields) {
  JsonObject o;
  std::string err;
  CHECK(parse_json_object(R"( {"s":"hi","i":-42,"f":1.5,"e":2e3,"t":true,"n":null} )", o, err));
  CHECK_EQ(o.find("s")->text, std::string("hi"));
  CHECK_EQ(o.find("i")->integer, int64_t{-42});
  CHECK(o.find("f")->kind == JsonField::Kind::Number);
  CHECK(o.find("e")->kind == JsonField::Kind::Number);
  CHECK(o.find("t")->kind == JsonField::Kind::Bool && o.find("t")->boolean);
  CHECK(o.find("n")->kind == JsonField::Kind::Null);
  CHECK(o.find("missing") == nullptr);
}

TEST(json_decodes_escapes_and_unicode) {
  JsonObject o;
  std::string err;
  CHECK(parse_json_object(R"({"a":"q\"b\\s\/n\nt\tu\u0041\u00e9\ud83d\ude00"})", o, err));
  CHECK_EQ(o.find("a")->text, std::string("q\"b\\s/n\nt\tuA\xC3\xA9\xF0\x9F\x98\x80"));
}

TEST(json_skips_nested_values) {
  JsonObject o;
  std::string err;
  CHECK(parse_json_object(R"({"cmd":"x","extra":{"a":[1,{"b":null}],"c":"}"},"arr":[]})", o, err));
  CHECK(o.find("extra")->kind == JsonField::Kind::Other);
  CHECK_EQ(o.find("cmd")->text, std::string("x"));
}

TEST(json_rejects_malformed_input) {
  const char* bad[] = {
      "",          "[]",           "{",             "{\"a\":}",       "{\"a\":1,}",     "{\"a\" 1}",
      "{\"a\":01}", "{\"a\":1.}",  "{\"a\":tru}",   "{\"a\":\"x}",    "{\"a\":1} x",    "{\"a\":1,\"a\":2}",
      "{\"a\":\"\\q\"}", "{\"a\":\"\\ud83d\"}", "{\"a\":\"line\nbreak\"}", "{\"a\":NaN}", "{'a':1}",
      "{\"a\":[[[[[[[[[[1]]]]]]]]]]}",
  };
  for (const char* text : bad) {
    JsonObject o;
    std::string err;
    const bool ok = parse_json_object(text, o, err);
    if (ok || err.empty()) std::printf("    unexpectedly accepted: %s\n", text);
    CHECK(!ok);
    CHECK(!err.empty());
  }
}

TEST(json_integer_range) {
  JsonObject o;
  std::string err;
  CHECK(parse_json_object(R"({"max":9223372036854775807,"min":-9223372036854775808,"big":9223372036854775808})", o,
                          err));
  CHECK_EQ(o.find("max")->integer, INT64_MAX);
  CHECK_EQ(o.find("min")->integer, INT64_MIN);
  CHECK(o.find("big")->kind == JsonField::Kind::Number);  // out of range: not an integer
}

TEST(json_string_escaping_round_trips) {
  std::string out = "{\"k\":";
  append_json_string(out, std::string("a\"b\\c\n\x01\xC3\xA9", 9));
  out += "}";
  CHECK_EQ(out, std::string("{\"k\":\"a\\\"b\\\\c\\n\\u0001\xC3\xA9\"}"));
  JsonObject o;
  std::string err;
  CHECK(parse_json_object(out, o, err));
  CHECK_EQ(o.find("k")->text, std::string("a\"b\\c\n\x01\xC3\xA9", 9));
}

// --- commands (contract §3A examples, verbatim) -------------------------------------------------

TEST(contract_command_examples_parse) {
  ParsedCommand p = parse_command(R"({"cmd":"hello","version":1})");
  CHECK(p.ok && p.command.type == CommandType::Hello && p.command.version == 1);

  p = parse_command(R"({"cmd":"set_mode","mode":"robot_targeting"})");
  CHECK(p.ok && p.command.mode == Mode::RobotTargeting);

  p = parse_command(R"({"cmd":"set_led","color":"blue","pattern":"solid"})");
  CHECK(p.ok && p.command.color == LedColor::Blue && p.command.pattern == LedPattern::Solid);

  p = parse_command(R"({"cmd":"set_lcd","line1":"TARGET: BLUE?","line2":"A:yes B:no"})");
  CHECK(p.ok && p.command.line1 == "TARGET: BLUE?" && p.command.line2 == "A:yes B:no");

  p = parse_command(R"({"cmd":"arm_pose","pose":"point_blue"})");
  CHECK(p.ok && p.command.pose == ArmPose::PointBlue);

  p = parse_command(R"({"cmd":"stop","reason":"estop"})");
  CHECK(p.ok && p.command.reason == StopReason::Estop);

  p = parse_command(R"({"cmd":"heartbeat"})");
  CHECK(p.ok && p.command.type == CommandType::Heartbeat);
}

TEST(every_enum_value_in_the_contract_is_accepted) {
  for (const char* m : {"idle", "robot_targeting", "music", "presentation", "teach"}) {
    CHECK(parse_command(std::string(R"({"cmd":"set_mode","mode":")") + m + "\"}").ok);
  }
  for (const char* c : {"blue", "yellow", "green", "white", "red"}) {
    for (const char* p : {"solid", "pulse", "blink", "off"}) {
      CHECK(parse_command(std::string(R"({"cmd":"set_led","color":")") + c + R"(","pattern":")" + p + "\"}").ok);
    }
  }
  for (const char* pose : {"home", "point_blue", "point_yellow", "point_green", "nod"}) {
    CHECK(parse_command(std::string(R"({"cmd":"arm_pose","pose":")") + pose + "\"}").ok);
  }
  for (const char* r : {"estop", "timeout", "manual"}) {
    CHECK(parse_command(std::string(R"({"cmd":"stop","reason":")") + r + "\"}").ok);
  }
}

TEST(unknown_fields_are_ignored) {
  const ParsedCommand p = parse_command(R"({"cmd":"set_led","color":"red","pattern":"blink","seq":7,"x":{"y":[1]}})");
  CHECK(p.ok);
}

TEST(invalid_commands_are_rejected_with_the_cmd_echoed) {
  struct Case {
    const char* line;
    const char* cmd;
  } cases[] = {
      {R"({"cmd":"dance"})", "dance"},                                  // unknown cmd
      {R"({"cmd":"set_led","color":"purple","pattern":"solid"})", "set_led"},
      {R"({"cmd":"set_led","color":"red"})", "set_led"},                 // missing pattern
      {R"({"cmd":"set_mode","mode":"TEACH"})", "set_mode"},              // case-sensitive
      {R"({"cmd":"set_lcd","line1":"only one"})", "set_lcd"},
      {R"({"cmd":"set_lcd","line1":1,"line2":"x"})", "set_lcd"},
      {R"({"cmd":"arm_pose","pose":"point_red"})", "arm_pose"},
      {R"({"cmd":"stop"})", "stop"},
      {R"({"cmd":"hello","version":"1"})", "hello"},
      {R"({"cmd":"hello"})", "hello"},
  };
  for (const Case& c : cases) {
    const ParsedCommand p = parse_command(c.line);
    CHECK(!p.ok);
    CHECK_EQ(p.cmd, std::string(c.cmd));
    CHECK(!p.error.empty());
  }
}

TEST(lines_without_a_cmd_are_rejected) {
  for (const char* line : {"hello", "{}", R"({"cmd":5})", R"({"cmd":""})", R"({"event":"ack"})", "{\"cmd\":"}) {
    const ParsedCommand p = parse_command(line);
    CHECK(!p.ok);
    CHECK(!p.has_cmd());
  }
}

TEST(lines_over_128_bytes_are_rejected) {
  std::string line = R"({"cmd":"set_lcd","line1":")" + std::string(100, 'x') + R"(","line2":""})";
  CHECK(line.size() + 1 > kMaxLineBytes);
  const ParsedCommand p = parse_command(line);
  CHECK(!p.ok);
  CHECK_EQ(p.error, std::string("line too long"));
}

// --- events (contract §3B examples, verbatim) ----------------------------------------------------

TEST(contract_event_examples_are_encoded_exactly) {
  CHECK_EQ(encode_hello({"led", "lcd", "button_a", "button_b", "dial", "arm"}),
           std::string(R"({"event":"hello","version":1,"capabilities":["led","lcd","button_a","button_b","dial","arm"]})"));
  CHECK_EQ(encode_hello({}), std::string(R"({"event":"hello","version":1,"capabilities":[]})"));
  CHECK_EQ(encode_button(ButtonId::A, ButtonState::Pressed), std::string(R"({"event":"button","id":"a","state":"pressed"})"));
  CHECK_EQ(encode_button(ButtonId::B, ButtonState::LongPress),
           std::string(R"({"event":"button","id":"b","state":"long_press"})"));
  CHECK_EQ(encode_dial(1), std::string(R"({"event":"dial","delta":1})"));
  CHECK_EQ(encode_touch_tapped(), std::string(R"({"event":"touch","state":"tapped"})"));
  CHECK_EQ(encode_estop(true), std::string(R"({"event":"estop","active":true})"));
  CHECK_EQ(encode_estop(false), std::string(R"({"event":"estop","active":false})"));
  CHECK_EQ(encode_status(ArmState::Idle, false), std::string(R"({"event":"status","arm":"idle","safety":"ok"})"));
  CHECK_EQ(encode_status(ArmState::Moving, true), std::string(R"({"event":"status","arm":"moving","safety":"estop"})"));
  CHECK_EQ(encode_ack("set_led", true, ""), std::string(R"({"event":"ack","cmd":"set_led","ok":true,"message":""})"));
}

TEST(dial_delta_is_clamped_to_contract_range) {
  CHECK_EQ(encode_dial(25), std::string(R"({"event":"dial","delta":10})"));
  CHECK_EQ(encode_dial(-99), std::string(R"({"event":"dial","delta":-10})"));
}

TEST(ack_always_fits_one_line_and_stays_valid_json) {
  const std::string long_msg(300, 'm');
  const std::string long_cmd = std::string(40, 'c') + "\xC3\xA9\xC3\xA9";  // multi-byte at the cut point
  const std::string ack = encode_ack(long_cmd, false, long_msg + "\"quotes\"");
  CHECK(ack.size() + 1 <= kMaxLineBytes);
  JsonObject o;
  std::string err;
  CHECK(parse_json_object(ack, o, err));
  CHECK_EQ(o.find("event")->text, std::string("ack"));
  CHECK(o.find("ok")->kind == JsonField::Kind::Bool && !o.find("ok")->boolean);
  const std::string utf8_cut = encode_ack(std::string(31, 'c') + "\xC3\xA9", true, "");
  CHECK(utf8_cut.find('\xC3') == std::string::npos);  // the 2-byte char at byte 31..32 is dropped whole
}
