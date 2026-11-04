#include <algorithm>
#include <iostream>
#include <vector>

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int n;
    std::cin >> n;
    // tails[k] is the smallest value that ends an increasing subsequence of length k + 1.
    std::vector<int> tails;
    for (int i = 0; i < n; ++i) {
        int value;
        std::cin >> value;
        auto place = std::lower_bound(tails.begin(), tails.end(), value);
        if (place == tails.end()) {
            tails.push_back(value);
        } else {
            *place = value;
        }
    }
    std::cout << tails.size() << "\n";
}
