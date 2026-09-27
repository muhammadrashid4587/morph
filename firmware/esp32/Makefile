# Host (PC) build of the device-independent firmware core: tests + simulator.
# No ESP32, no Arduino, no hardware needed. Requires a C++17 compiler (clang++ or g++).
#
#   make test   build and run the unit tests (with address/undefined-behavior sanitizers)
#   make sim    build the serial simulator: build/morph_sim
#   make clean

CXX      ?= c++
CXXFLAGS ?= -std=c++17 -Wall -Wextra -Wpedantic -Werror -Wshadow -Wconversion -Wno-sign-conversion -O1 -g
SANITIZE ?= -fsanitize=address,undefined -fno-omit-frame-pointer
INCLUDES := -Ilib/morph_core/src -Iinclude -Isrc -Itest/host
BUILD    := build

CORE_SRC  := $(wildcard lib/morph_core/src/morph/*.cpp)
BOARD_SRC := src/board_config.cpp
TEST_SRC  := $(wildcard test/host/*.cpp)
CORE_HDR  := $(wildcard lib/morph_core/src/morph/*.h) test/host/check.h include/hardware_config.h src/board_config.h

.PHONY: test sim clean

test: $(BUILD)/morph_tests
	./$(BUILD)/morph_tests

sim: $(BUILD)/morph_sim

$(BUILD)/morph_tests: $(CORE_SRC) $(BOARD_SRC) $(TEST_SRC) $(CORE_HDR)
	@mkdir -p $(BUILD)
	$(CXX) $(CXXFLAGS) $(SANITIZE) $(INCLUDES) $(CORE_SRC) $(BOARD_SRC) $(TEST_SRC) -o $@

$(BUILD)/morph_sim: $(CORE_SRC) $(BOARD_SRC) tools/morph_sim.cpp $(CORE_HDR)
	@mkdir -p $(BUILD)
	$(CXX) $(CXXFLAGS) $(INCLUDES) $(CORE_SRC) $(BOARD_SRC) tools/morph_sim.cpp -o $@

clean:
	rm -rf $(BUILD)
