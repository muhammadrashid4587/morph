// Debounce, buttons, touch, joystick, encoder, dial, LED patterns, LCD text.
#include <cstdint>
#include <string>
#include <vector>

#include "check.h"
#include "morph/inputs.h"
#include "morph/outputs.h"

using namespace morph;

// Feeds `raw` for `ms` milliseconds (1 ms samples) and collects button edges.
static std::vector<ButtonEdge> hold(ButtonTracker& b, bool raw, uint32_t& t, uint32_t ms) {
  std::vector<ButtonEdge> edges;
  for (uint32_t i = 0; i < ms; ++i, ++t) {
    const ButtonEdge e = b.update(raw, t);
    if (e != ButtonEdge::None) edges.push_back(e);
  }
  return edges;
}

TEST(debouncer_ignores_bounces_and_accepts_stable_changes) {
  Debouncer d(30);
  CHECK(!d.update(false, 0));  // first sample initializes silently
  uint32_t t = 1;
  for (int i = 0; i < 20; ++i, ++t) CHECK(!d.update(i % 2 == 0, t));  // chatter shorter than 30 ms
  CHECK(!d.state());
  bool changed = false;
  for (int i = 0; i < 31; ++i, ++t) changed |= d.update(true, t);
  CHECK(changed);
  CHECK(d.state());
}

TEST(short_press_gives_pressed_then_released) {
  ButtonTracker b(30, 1000);
  uint32_t t = 0;
  CHECK(hold(b, false, t, 50).empty());
  CHECK_EQ(hold(b, true, t, 200), std::vector<ButtonEdge>{ButtonEdge::Pressed});
  CHECK_EQ(hold(b, false, t, 100), std::vector<ButtonEdge>{ButtonEdge::Released});
}

TEST(long_press_is_reported_once_after_one_second) {
  ButtonTracker b(30, 1000);
  uint32_t t = 0;
  hold(b, false, t, 10);
  const auto held = hold(b, true, t, 3000);
  CHECK_EQ(held, (std::vector<ButtonEdge>{ButtonEdge::Pressed, ButtonEdge::LongPress}));
  CHECK_EQ(hold(b, false, t, 100), std::vector<ButtonEdge>{ButtonEdge::Released});
}

TEST(long_press_timing_boundary) {
  ButtonTracker b(30, 1000);
  uint32_t t = 0;
  hold(b, false, t, 10);  // t = 10
  // Press starts at t=10, is accepted at t=40 (30 ms debounce); long_press is due at t=1040.
  CHECK_EQ(hold(b, true, t, 1030), std::vector<ButtonEdge>{ButtonEdge::Pressed});  // samples t=10..1039
  CHECK_EQ(hold(b, true, t, 1), std::vector<ButtonEdge>{ButtonEdge::LongPress});   // t=1040 exactly
}

TEST(button_held_at_boot_produces_no_events_until_pressed_again) {
  ButtonTracker b(30, 1000);
  uint32_t t = 0;
  CHECK(hold(b, true, t, 2000).empty());   // held since boot: no pressed, no long_press
  CHECK(hold(b, false, t, 100).empty());   // its release is not reported either
  CHECK_EQ(hold(b, true, t, 100), std::vector<ButtonEdge>{ButtonEdge::Pressed});
}

TEST(touch_tap_vs_long_touch) {
  TapDetector tap(30, 800);
  uint32_t t = 0;
  int taps = 0;
  auto feed = [&](bool raw, uint32_t ms) {
    for (uint32_t i = 0; i < ms; ++i, ++t) taps += tap.update(raw, t) ? 1 : 0;
  };
  feed(false, 50);
  feed(true, 200);
  feed(false, 100);
  CHECK_EQ(taps, 1);
  feed(true, 2000);  // resting a hand on the pad is not a tap
  feed(false, 100);
  CHECK_EQ(taps, 1);
  feed(true, 10);  // a 10 ms glitch is filtered by the debounce
  feed(false, 100);
  CHECK_EQ(taps, 1);
}

TEST(joystick_axis_normalization) {
  AxisCalibration cal;  // 0..4095, center 2048, deadzone 0.15
  CHECK_EQ(normalize_axis(2048, cal), 0.0f);
  CHECK_EQ(normalize_axis(2048 + 200, cal), 0.0f);  // inside the deadzone
  CHECK_EQ(normalize_axis(4095, cal), 1.0f);
  CHECK_EQ(normalize_axis(0, cal), -1.0f);
  CHECK_EQ(normalize_axis(9999, cal), 1.0f);  // clamped
  const float half = normalize_axis(2048 + (4095 - 2048) * 3 / 4, cal);
  CHECK(half > 0.6f && half < 0.8f);
  cal.inverted = true;
  CHECK_EQ(normalize_axis(4095, cal), -1.0f);
  AxisCalibration flat{2048, 2048, 2048, 0.1f, false};  // degenerate calibration never divides by zero
  CHECK_EQ(normalize_axis(100, flat), 0.0f);
}

TEST(joystick_dial_single_step_and_auto_repeat) {
  JoystickDial dial(0.6f, 400, 150);
  int total = 0;
  uint32_t t = 0;
  for (; t < 100; ++t) total += dial.update(0.0f, t);
  CHECK_EQ(total, 0);
  for (uint32_t i = 0; i < 100; ++i, ++t) total += dial.update(0.9f, t);
  CHECK_EQ(total, 1);  // one detent immediately, repeat not yet
  for (uint32_t i = 0; i < 600; ++i, ++t) total += dial.update(0.9f, t);  // t=200..799
  CHECK_EQ(total, 3);  // first repeat 400 ms after the push (t=500), then every 150 ms (t=650)
  for (uint32_t i = 0; i < 50; ++i, ++t) total += dial.update(0.0f, t);
  for (uint32_t i = 0; i < 10; ++i, ++t) total += dial.update(-0.95f, t);
  CHECK_EQ(total, 2);  // released, then one step the other way
}

TEST(quadrature_decoder_counts_detents_both_ways) {
  QuadratureDecoder q(4);
  q.update(false, false);
  // Clockwise Gray sequence: 00 -> 10 -> 11 -> 01 -> 00 (one detent).
  int d = 0;
  for (int rep = 0; rep < 3; ++rep) {
    d += q.update(true, false);
    d += q.update(true, true);
    d += q.update(false, true);
    d += q.update(false, false);
  }
  CHECK_EQ(d, 3);
  for (int rep = 0; rep < 2; ++rep) {
    d += q.update(false, true);
    d += q.update(true, true);
    d += q.update(true, false);
    d += q.update(false, false);
  }
  CHECK_EQ(d, 1);
  d += q.update(true, true);  // invalid double-transition is ignored
  d += q.update(false, false);
  CHECK_EQ(d, 1);
}

TEST(dial_accumulator_rate_limit_clamp_and_carry) {
  DialAccumulator acc(50, 10);
  acc.add(3);
  CHECK_EQ(acc.poll(1000), 3);
  acc.add(2);
  CHECK_EQ(acc.poll(1020), 0);  // too soon: at most one event per 50 ms
  CHECK_EQ(acc.poll(1050), 2);
  acc.add(25);
  CHECK_EQ(acc.poll(1100), 10);
  CHECK_EQ(acc.poll(1150), 10);
  CHECK_EQ(acc.poll(1200), 5);
  CHECK_EQ(acc.poll(1250), 0);
  acc.add(1000);  // runaway input is bounded
  CHECK_EQ(acc.pending(), 100);
}

// --- LED / LCD ------------------------------------------------------------------------------------

TEST(led_patterns) {
  const Rgb red = color_rgb(LedColor::Red);
  CHECK(led_output(LedColor::Red, LedPattern::Solid, 12345) == red);
  CHECK(led_output(LedColor::Red, LedPattern::Off, 12345) == Rgb{});
  CHECK(led_output(LedColor::Red, LedPattern::Blink, 1000) == red);
  CHECK(led_output(LedColor::Red, LedPattern::Blink, 1000 + kBlinkPeriodMs / 2) == Rgb{});
  // Pulse stays between ~10% and 100% and actually varies.
  int lo = 255, hi = 0;
  for (uint32_t t = 0; t < kPulsePeriodMs; t += 10) {
    const int r = led_output(LedColor::Red, LedPattern::Pulse, t).r;
    lo = r < lo ? r : lo;
    hi = r > hi ? r : hi;
  }
  CHECK(lo >= 20 && lo <= 30);
  CHECK_EQ(hi, 255);
  CHECK(color_rgb(LedColor::Blue) != color_rgb(LedColor::Green));
}

TEST(lcd_lines_are_16_printable_ascii_characters) {
  CHECK_EQ(lcd_line("TARGET: BLUE?"), std::string("TARGET: BLUE?   "));
  CHECK_EQ(lcd_line("THIS LINE IS TOO LONG FOR 16"), std::string("THIS LINE IS TOO"));
  CHECK_EQ(lcd_line(""), std::string(16, ' '));
  CHECK_EQ(lcd_line("caf\xC3\xA9 \xF0\x9F\x98\x80!"), std::string("caf? ?!") + std::string(9, ' '));
  CHECK_EQ(lcd_line("tab\there\n"), std::string("tab?here?") + std::string(7, ' '));
}
