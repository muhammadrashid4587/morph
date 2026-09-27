// Newline framing for the serial link. Bytes in, complete lines out.
// Memory is bounded: a runaway line is cut at kCapacity bytes (the parser then
// rejects it as "line too long", which stops the motors and is acked).
#pragma once

#include <cstddef>
#include <string>

namespace morph {

class LineBuffer {
 public:
  static constexpr size_t kCapacity = 256;  // > 128, so over-long lines are still detected as too long

  // Adds one received byte. Returns true when a complete line is available in line().
  bool push(char c) {
    if (c == '\n') {
      line_.swap(buf_);
      buf_.clear();
      return true;
    }
    if (buf_.size() < kCapacity) buf_.push_back(c);
    return false;
  }
  const std::string& line() const { return line_; }

 private:
  std::string buf_;
  std::string line_;
};

}  // namespace morph
