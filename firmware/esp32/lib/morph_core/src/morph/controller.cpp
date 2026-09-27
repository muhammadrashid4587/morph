#include "morph/controller.h"

namespace morph {

Controller::Controller(const Config& config)
    : config_(config),
      arm_usable_(arm_usable(config)),
      arm_(arm_usable_ ? config.arm : ArmConfig{}),  // an unusable arm gets no joints at all
      estop_(config.estop.reset_stable_ms, config.estop.requires_reset_input),
      link_(config.heartbeat_timeout_ms),
      button_a_(config.button_a.debounce_ms, config.button_a.long_press_ms),
      button_b_(config.button_b.debounce_ms, config.button_b.long_press_ms),
      touch_(config.touch.debounce_ms, config.touch.max_tap_ms),
      joystick_dial_(config.dial.joystick_threshold, config.dial.joystick_initial_delay_ms,
                     config.dial.joystick_repeat_ms),
      esc_(config.esc) {
  out_.joints = arm_.outputs();  // all disabled
  refresh_outputs(0);
}

void Controller::begin(uint32_t now_ms) {
  emit(encode_hello(capabilities(config_)));
  next_status_ms_ = now_ms + config_.status_period_ms;
  refresh_outputs(now_ms);
}

bool Controller::pop_line(std::string& out) {
  if (tx_.empty()) return false;
  out = std::move(tx_.front());
  tx_.pop_front();
  return true;
}

void Controller::safety_stop(const char* reason) {
  arm_.stop_and_power_off();
  if (pose_ack_pending_) {
    pose_ack_pending_ = false;
    emit(encode_ack("arm_pose", false, reason));
  }
}

void Controller::on_line(std::string_view line, uint32_t now_ms) {
  while (!line.empty() && (line.back() == '\r' || line.back() == ' ' || line.back() == '\t')) {
    line.remove_suffix(1);
  }
  while (!line.empty() && (line.front() == ' ' || line.front() == '\t')) line.remove_prefix(1);
  if (line.empty()) return;

  const ParsedCommand parsed = parse_command(line);
  if (parsed.has_cmd()) link_.on_activity(now_ms);  // only well-formed JSON with a cmd keeps the link alive
  link_alive_ = link_.alive(now_ms);

  if (!parsed.ok) {
    safety_stop("invalid command");  // motors OFF on any invalid command
    emit(encode_ack(parsed.cmd, false, parsed.error));
    refresh_outputs(now_ms);
    return;
  }
  handle_command(parsed.command, parsed.cmd, now_ms);
  refresh_outputs(now_ms);
}

void Controller::handle_command(const Command& c, const std::string& cmd, uint32_t now_ms) {
  switch (c.type) {
    case CommandType::Heartbeat:
      return;  // no ack for heartbeat
    case CommandType::Hello: {
      emit(encode_hello(capabilities(config_)));
      const bool supported = c.version == kProtocolVersion;
      emit(encode_ack(cmd, supported, supported ? "" : "unsupported protocol version"));
      return;
    }
    case CommandType::SetMode:
      mode_ = c.mode;
      emit(encode_ack(cmd, true, ""));
      return;
    case CommandType::SetLed:
      led_color_ = c.color;
      led_pattern_ = c.pattern;
      emit(encode_ack(cmd, config_.led_enabled, config_.led_enabled ? "" : "led not configured"));
      return;
    case CommandType::SetLcd:
      pi_line1_ = c.line1;
      pi_line2_ = c.line2;
      emit(encode_ack(cmd, config_.lcd_enabled, config_.lcd_enabled ? "" : "lcd not configured"));
      return;
    case CommandType::Stop:
      safety_stop("stopped");
      emit(encode_ack(cmd, true, ""));  // does NOT clear a latched e-stop
      return;
    case CommandType::ArmPose: {
      if (!arm_usable_) {
        emit(encode_ack(cmd, false, "arm not available"));
        return;
      }
      if (estop_.active()) {
        emit(encode_ack(cmd, false, "estop active"));
        return;
      }
      if (pose_ack_pending_) {  // a new pose replaces the one in flight; each cmd still gets one ack
        pose_ack_pending_ = false;
        emit(encode_ack("arm_pose", false, "preempted"));
      }
      std::string error;
      if (!arm_.start(c.pose, now_ms, error)) {
        arm_.stop_and_power_off();
        emit(encode_ack(cmd, false, error));
        return;
      }
      pose_ack_pending_ = true;  // acked when the motion finishes or fails (contract §3A)
      return;
    }
  }
}

void Controller::poll_inputs(const InputSample& in, uint32_t now_ms) {
  const struct {
    const ButtonConfig& cfg;
    ButtonTracker& tracker;
    bool raw;
    ButtonId id;
  } buttons[] = {{config_.button_a, button_a_, in.button_a, ButtonId::A},
                 {config_.button_b, button_b_, in.button_b, ButtonId::B}};
  for (const auto& b : buttons) {
    if (!b.cfg.enabled) continue;
    switch (b.tracker.update(b.raw, now_ms)) {
      case ButtonEdge::Pressed: emit(encode_button(b.id, ButtonState::Pressed)); break;
      case ButtonEdge::Released: emit(encode_button(b.id, ButtonState::Released)); break;
      case ButtonEdge::LongPress: emit(encode_button(b.id, ButtonState::LongPress)); break;
      case ButtonEdge::None: break;
    }
  }
  if (config_.touch.enabled && touch_.update(in.touch, now_ms)) emit(encode_touch_tapped());

  int detents = 0;
  if (config_.dial.source == DialSource::Encoder) detents = in.encoder_detents;
  if (config_.dial.source == DialSource::JoystickX) detents = joystick_dial_.update(in.joystick_x, now_ms);
  if (config_.dial.invert) detents = -detents;
  if (config_.dial.source != DialSource::None) {
    dial_.add(detents);
    const int delta = dial_.poll(now_ms);
    if (delta != 0) emit(encode_dial(delta));
  }
}

void Controller::tick(const InputSample& in, uint32_t now_ms, uint32_t now_us) {
  // 1. Safety first: e-stop latch and heartbeat.
  if (config_.estop.configured && estop_.update(in.estop, in.estop_reset, now_ms)) {
    emit(encode_estop(estop_.active()));
  }
  const bool was_alive = link_alive_;
  link_alive_ = link_.alive(now_ms);

  const bool allowed = arm_usable_ && config_.estop.configured && !estop_.active() && link_alive_;
  if (!allowed) {
    const char* reason = estop_.active() ? "estop" : (was_alive && !link_alive_ ? "pi lost" : "not allowed");
    safety_stop(reason);
  }

  // 2. Inputs -> events.
  poll_inputs(in, now_ms);

  // 3. Motion (only while allowed; otherwise everything was just powered off).
  std::string error;
  const ArmController::Result result = arm_.update(now_ms, now_us, error);
  if (result != ArmController::Result::None && pose_ack_pending_) {
    pose_ack_pending_ = false;
    emit(encode_ack("arm_pose", result == ArmController::Result::Done, error));
  }

  // 4. Periodic status (link-alive signal for the Pi).
  if (static_cast<int32_t>(now_ms - next_status_ms_) >= 0) {
    emit(encode_status(arm_.state(), estop_.active()));
    next_status_ms_ = now_ms + config_.status_period_ms;
  }

  out_.motion_allowed = allowed;
  refresh_outputs(now_ms);
}

void Controller::refresh_outputs(uint32_t now_ms) {
  // LCD: local safety screens override whatever the Pi asked for.
  if (estop_.active()) {
    out_.lcd_line1 = lcd_line("E-STOP");
    out_.lcd_line2 = lcd_line("RESET ON ROBOT");
  } else if (!link_alive_ && link_.ever_connected()) {
    out_.lcd_line1 = lcd_line("PI LOST");
    out_.lcd_line2 = lcd_line("");
  } else if (!link_.ever_connected()) {
    out_.lcd_line1 = lcd_line("MORPH ESP32");
    out_.lcd_line2 = lcd_line("WAITING FOR PI");
  } else {
    out_.lcd_line1 = lcd_line(pi_line1_);
    out_.lcd_line2 = lcd_line(pi_line2_);
  }

  // LED: same override order.
  if (estop_.active()) {
    out_.led = led_output(LedColor::Red, LedPattern::Solid, now_ms);
  } else if (!link_alive_ && link_.ever_connected()) {
    out_.led = led_output(LedColor::Red, LedPattern::Blink, now_ms);
  } else if (!link_.ever_connected()) {
    out_.led = led_output(LedColor::White, LedPattern::Blink, now_ms);
  } else {
    out_.led = led_output(led_color_, led_pattern_, now_ms);
  }

  out_.joints = arm_.outputs();
  if (!out_.motion_allowed) {
    for (JointOutput& j : out_.joints) {  // belt and braces: never output motion when not allowed
      j.enabled = false;
      j.servo_pulse_us = 0;
      j.step = 0;
    }
  }
  // The contract has no brushless-motor command, so the ESC only ever receives its stop pulse.
  out_.esc_pulse_us = config_.esc_enabled ? esc_.pulse_us(0.0f, false, now_ms) : 0;
}

}  // namespace morph
