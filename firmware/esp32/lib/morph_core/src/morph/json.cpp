#include "morph/json.h"

#include <cstdio>

namespace morph {
namespace {

constexpr int kMaxDepth = 8;

class Parser {
 public:
  Parser(std::string_view text, std::string& error) : s_(text), error_(error) {}

  bool parse_object(JsonObject& out) {
    skip_ws();
    if (!expect('{')) return fail("expected '{'");
    skip_ws();
    if (peek() == '}') {
      ++i_;
      return finish();
    }
    while (true) {
      JsonField field;
      skip_ws();
      if (peek() != '"') return fail("expected key");
      if (!parse_string(field.key)) return false;
      if (out.find(field.key) != nullptr) return fail("duplicate key");
      skip_ws();
      if (!expect(':')) return fail("expected ':'");
      skip_ws();
      if (!parse_value(field, 1)) return false;
      out.fields.push_back(std::move(field));
      skip_ws();
      if (peek() == ',') {
        ++i_;
        continue;
      }
      if (expect('}')) return finish();
      return fail("expected ',' or '}'");
    }
  }

 private:
  bool finish() {
    skip_ws();
    if (i_ != s_.size()) return fail("trailing characters");
    return true;
  }

  bool parse_value(JsonField& field, int depth) {
    const char c = peek();
    if (c == '"') {
      field.kind = JsonField::Kind::String;
      return parse_string(field.text);
    }
    if (c == '{' || c == '[') {
      field.kind = JsonField::Kind::Other;
      return skip_container(depth);
    }
    if (c == 't' || c == 'f') {
      field.kind = JsonField::Kind::Bool;
      field.boolean = (c == 't');
      return literal(c == 't' ? "true" : "false");
    }
    if (c == 'n') {
      field.kind = JsonField::Kind::Null;
      return literal("null");
    }
    return parse_number(field);
  }

  bool literal(std::string_view word) {
    if (s_.substr(i_, word.size()) != word) return fail("invalid literal");
    i_ += word.size();
    return true;
  }

  bool parse_number(JsonField& field) {
    const size_t start = i_;
    bool negative = false;
    if (peek() == '-') {
      negative = true;
      ++i_;
    }
    if (!is_digit(peek())) return fail("invalid value");
    bool integral = true;
    bool overflow = false;
    uint64_t magnitude = 0;
    if (peek() == '0') {
      ++i_;
      if (is_digit(peek())) return fail("leading zero");
    } else {
      while (is_digit(peek())) {
        const uint64_t digit = static_cast<uint64_t>(peek() - '0');
        if (magnitude > (UINT64_MAX - digit) / 10) overflow = true;
        magnitude = magnitude * 10 + digit;
        ++i_;
      }
    }
    if (peek() == '.') {
      integral = false;
      ++i_;
      if (!is_digit(peek())) return fail("invalid number");
      while (is_digit(peek())) ++i_;
    }
    if (peek() == 'e' || peek() == 'E') {
      integral = false;
      ++i_;
      if (peek() == '+' || peek() == '-') ++i_;
      if (!is_digit(peek())) return fail("invalid number");
      while (is_digit(peek())) ++i_;
    }
    const uint64_t limit = negative ? (uint64_t{1} << 63) : static_cast<uint64_t>(INT64_MAX);
    if (integral && !overflow && magnitude <= limit) {
      field.kind = JsonField::Kind::Integer;
      field.integer = negative ? static_cast<int64_t>(0 - magnitude) : static_cast<int64_t>(magnitude);
    } else {
      field.kind = JsonField::Kind::Number;  // fractional, exponent, or out of int64 range
    }
    return i_ > start;
  }

  bool parse_string(std::string& out) {
    if (!expect('"')) return fail("expected string");
    out.clear();
    while (i_ < s_.size()) {
      const char c = s_[i_++];
      if (c == '"') return true;
      if (static_cast<unsigned char>(c) < 0x20) return fail("control character in string");
      if (c != '\\') {
        out.push_back(c);
        continue;
      }
      if (i_ >= s_.size()) break;
      const char e = s_[i_++];
      switch (e) {
        case '"': out.push_back('"'); break;
        case '\\': out.push_back('\\'); break;
        case '/': out.push_back('/'); break;
        case 'b': out.push_back('\b'); break;
        case 'f': out.push_back('\f'); break;
        case 'n': out.push_back('\n'); break;
        case 'r': out.push_back('\r'); break;
        case 't': out.push_back('\t'); break;
        case 'u': {
          uint32_t cp = 0;
          if (!hex4(cp)) return false;
          if (cp >= 0xD800 && cp <= 0xDBFF) {  // surrogate pair
            uint32_t low = 0;
            if (s_.substr(i_, 2) != "\\u") return fail("lone surrogate");
            i_ += 2;
            if (!hex4(low)) return false;
            if (low < 0xDC00 || low > 0xDFFF) return fail("lone surrogate");
            cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
          } else if (cp >= 0xDC00 && cp <= 0xDFFF) {
            return fail("lone surrogate");
          }
          append_utf8(out, cp);
          break;
        }
        default:
          return fail("invalid escape");
      }
    }
    return fail("unterminated string");
  }

  bool hex4(uint32_t& value) {
    if (i_ + 4 > s_.size()) return fail("invalid \\u escape");
    value = 0;
    for (int k = 0; k < 4; ++k) {
      const char h = s_[i_++];
      value <<= 4;
      if (h >= '0' && h <= '9') value |= static_cast<uint32_t>(h - '0');
      else if (h >= 'a' && h <= 'f') value |= static_cast<uint32_t>(h - 'a' + 10);
      else if (h >= 'A' && h <= 'F') value |= static_cast<uint32_t>(h - 'A' + 10);
      else return fail("invalid \\u escape");
    }
    return true;
  }

  static void append_utf8(std::string& out, uint32_t cp) {
    if (cp < 0x80) {
      out.push_back(static_cast<char>(cp));
    } else if (cp < 0x800) {
      out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
      out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else if (cp < 0x10000) {
      out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
      out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
      out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else {
      out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
      out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
      out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
      out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    }
  }

  // Validates and skips a nested object or array (contents are ignored).
  bool skip_container(int depth) {
    if (depth > kMaxDepth) return fail("nesting too deep");
    const char open = s_[i_++];
    const char close = (open == '{') ? '}' : ']';
    skip_ws();
    if (peek() == close) {
      ++i_;
      return true;
    }
    while (true) {
      skip_ws();
      if (open == '{') {
        std::string ignored_key;
        if (peek() != '"' || !parse_string(ignored_key)) return fail("expected key");
        skip_ws();
        if (!expect(':')) return fail("expected ':'");
        skip_ws();
      }
      JsonField ignored;
      if (!parse_value(ignored, depth + 1)) return false;
      skip_ws();
      if (peek() == ',') {
        ++i_;
        continue;
      }
      if (expect(close)) return true;
      return fail("unterminated container");
    }
  }

  static bool is_digit(char c) { return c >= '0' && c <= '9'; }
  char peek() const { return i_ < s_.size() ? s_[i_] : '\0'; }
  bool expect(char c) {
    if (peek() != c) return false;
    ++i_;
    return true;
  }
  void skip_ws() {
    while (i_ < s_.size() && (s_[i_] == ' ' || s_[i_] == '\t' || s_[i_] == '\n' || s_[i_] == '\r')) ++i_;
  }
  bool fail(const char* reason) {
    if (error_.empty()) error_ = reason;
    return false;
  }

  std::string_view s_;
  size_t i_ = 0;
  std::string& error_;
};

}  // namespace

const JsonField* JsonObject::find(std::string_view key) const {
  for (const JsonField& f : fields) {
    if (f.key == key) return &f;
  }
  return nullptr;
}

bool parse_json_object(std::string_view text, JsonObject& out, std::string& error) {
  out.fields.clear();
  error.clear();
  Parser parser(text, error);
  return parser.parse_object(out);
}

void append_json_string(std::string& out, std::string_view value) {
  out.push_back('"');
  for (const char c : value) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (static_cast<unsigned char>(c) < 0x20) {
          char buf[8];
          std::snprintf(buf, sizeof buf, "\\u%04x", static_cast<unsigned>(static_cast<unsigned char>(c)));
          out += buf;
        } else {
          out.push_back(c);
        }
    }
  }
  out.push_back('"');
}

}  // namespace morph
