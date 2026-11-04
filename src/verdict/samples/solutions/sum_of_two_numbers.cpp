#include <iostream>

int main() {
    long long a, b;
    std::cin >> a >> b;
    // |a + b| <= 2 * 10^18 fits in a signed 64-bit integer.
    std::cout << a + b << "\n";
}
