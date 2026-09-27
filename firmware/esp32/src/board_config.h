// Turns include/hardware_config.h into the core morph::Config, enabling a device only
// when every field it needs is known, and listing what is missing for the rest.
// Pure C++ (no Arduino): also compiled into the host tests.
#pragma once

#include <string>
#include <vector>

#include "hardware_config.h"
#include "morph/config.h"

namespace board {

struct BoardConfig {
  morph::Config config;
  std::vector<std::string> missing;  // "<device>: <what is missing>" for every disabled device
};

// Builds the configuration from the hw:: constants.
BoardConfig build();

// Building blocks (exposed for tests).
bool input_known(int pin, int active_level, hw::Pull pull);
bool pin_usable(int pin);  // a real GPIO number that is safe to use on the target chip
morph::JointConfig joint_config(const hw::JointSpec& spec, std::vector<std::string>& missing);

}  // namespace board
