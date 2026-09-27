// MORPH ESP32 firmware entry point (Arduino framework).
//
// All behavior lives in the device-independent core (lib/morph_core, host-tested);
// this file only wires it to the serial port and the Board hardware layer.
// Serial protocol: docs/MORPH_INTEGRATION_CONTRACT.md §3 (one JSON object per line).
// Diagnostic lines start with '#': the Pi ignores lines that do not start with '{'.
#include <Arduino.h>

#include <string>

#include "board.h"
#include "board_config.h"
#include "hardware_config.h"
#include "morph/controller.h"
#include "morph/line_buffer.h"

namespace {

const board::BoardConfig g_cfg = board::build();  // constexpr inputs only: safe at static init
Board g_board(g_cfg);                             // constructor touches no hardware
morph::Controller g_ctl(g_cfg.config);            // starts with every motor output OFF
morph::LineBuffer g_rx;
uint32_t g_next_calibration_ms = 0;

void print_diagnostic(const std::string& text) {
  std::string line = text.substr(0, morph::kMaxLineBytes - 1);  // keep the 128-byte line rule
  Serial.println(line.c_str());
}

void flush_tx() {
  std::string line;
  while (g_ctl.pop_line(line)) Serial.println(line.c_str());  // println adds "\r\n"; the Pi strips '\r'
}

}  // namespace

void setup() {
  g_board.begin();  // FIRST: every known motor pin into its safe (off) state
  Serial.begin(hw::SERIAL_BAUD);
  print_diagnostic("# MORPH ESP32 firmware, protocol v1. Motors OFF until e-stop configured and Pi connected.");
  for (const std::string& m : g_cfg.missing) print_diagnostic("# disabled: " + m);
  g_ctl.begin(millis());
  flush_tx();
}

void loop() {
  int budget = 128;  // bounded work per loop keeps stepper timing and inputs responsive
  while (budget-- > 0 && Serial.available() > 0) {
    if (g_rx.push(static_cast<char>(Serial.read()))) g_ctl.on_line(g_rx.line(), millis());
  }

  const uint32_t now_ms = millis();
  const morph::InputSample in = g_board.read(now_ms);
  g_ctl.tick(in, now_ms, micros());
  g_board.apply(g_ctl.outputs());
  flush_tx();

  if (static_cast<int32_t>(now_ms - g_next_calibration_ms) >= 0) {  // raw readings for bench calibration
    g_next_calibration_ms = now_ms + 500;
    const std::string report = g_board.calibration_report();
    if (!report.empty()) print_diagnostic(report);
  }
}
