#include "check.h"

namespace check {
std::vector<Test>& registry() {
  static std::vector<Test> tests;
  return tests;
}
int failures = 0;
}  // namespace check

int main() {
  int failed_tests = 0;
  for (const check::Test& t : check::registry()) {
    const int before = check::failures;
    t.fn();
    if (check::failures != before) {
      ++failed_tests;
      std::printf("FAILED %s\n", t.name);
    }
  }
  const size_t total = check::registry().size();
  std::printf("%zu tests, %zu passed, %d failed (%d failed checks)\n", total, total - failed_tests, failed_tests,
              check::failures);
  return failed_tests == 0 ? 0 : 1;
}
