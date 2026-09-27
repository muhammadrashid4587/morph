// Tiny dependency-free test harness for the host (PC) build of morph_core.
#pragma once

#include <cstdio>
#include <ostream>
#include <sstream>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

namespace check {

struct Test {
  const char* name;
  void (*fn)();
};
std::vector<Test>& registry();
extern int failures;

struct Register {
  Register(const char* name, void (*fn)()) { registry().push_back({name, fn}); }
};

template <typename T, typename = void>
struct printable : std::false_type {};
template <typename T>
struct printable<T, std::void_t<decltype(std::declval<std::ostream&>() << std::declval<const T&>())>>
    : std::true_type {};

template <typename T>
std::string show(const T& value) {
  if constexpr (std::is_enum_v<T>) {
    return "enum(" + std::to_string(static_cast<long long>(value)) + ")";
  } else if constexpr (printable<T>::value) {
    std::ostringstream os;
    os << value;
    return os.str();
  } else {
    return "<unprintable>";
  }
}

}  // namespace check

#define TEST(name)                                                    \
  static void name();                                                 \
  static const check::Register register_##name(#name, name);          \
  static void name()

#define CHECK(cond)                                                                 \
  do {                                                                              \
    if (!(cond)) {                                                                  \
      ++check::failures;                                                            \
      std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                 \
    }                                                                               \
  } while (0)

#define CHECK_EQ(actual, expected)                                                               \
  do {                                                                                           \
    const auto& check_a_ = (actual);                                                             \
    const auto& check_e_ = (expected);                                                           \
    if (!(check_a_ == check_e_)) {                                                               \
      ++check::failures;                                                                         \
      std::printf("  FAIL %s:%d: %s == %s\n    actual:   %s\n    expected: %s\n", __FILE__,    \
                  __LINE__, #actual, #expected, check::show(check_a_).c_str(),                   \
                  check::show(check_e_).c_str());                                                \
    }                                                                                            \
  } while (0)
