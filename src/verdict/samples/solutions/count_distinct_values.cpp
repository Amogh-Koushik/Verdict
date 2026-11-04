#include <algorithm>
#include <iostream>
#include <vector>

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int n;
    std::cin >> n;
    std::vector<int> values(n);
    for (int& value : values) std::cin >> value;
    std::sort(values.begin(), values.end());
    std::cout << std::unique(values.begin(), values.end()) - values.begin() << "\n";
}
