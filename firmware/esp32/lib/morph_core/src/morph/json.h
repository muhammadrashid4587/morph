// Minimal, strict JSON for the MORPH serial contract (one flat object per line).
//
// Parses one top-level JSON object. Scalar fields (string, integer, other
// number, bool, null) are kept; nested objects/arrays are validated and
// skipped (recorded as Kind::Other), because the contract says unknown fields
// are ignored. Duplicate keys are rejected. No dynamic allocation beyond
// std::string / std::vector. Pure C++17: builds on the ESP32 and on a PC.
#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace morph {

struct JsonField {
  enum class Kind { String, Integer, Number, Bool, Null, Other };
  std::string key;
  Kind kind = Kind::Null;
  std::string text;      // Kind::String: decoded UTF-8 value
  int64_t integer = 0;   // Kind::Integer
  bool boolean = false;  // Kind::Bool
};

struct JsonObject {
  std::vector<JsonField> fields;
  const JsonField* find(std::string_view key) const;
};

// Parses exactly one JSON object (whitespace around it allowed).
// On failure returns false and sets `error` to a short reason.
bool parse_json_object(std::string_view text, JsonObject& out, std::string& error);

// Appends `value` as a JSON string literal (quotes included), escaping as needed.
void append_json_string(std::string& out, std::string_view value);

}  // namespace morph
