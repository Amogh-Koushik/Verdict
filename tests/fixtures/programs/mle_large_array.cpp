#include <iostream>
#include <vector>

int main() {
    // Filling every byte makes the memory real; an untouched allocation wouldn't count.
    std::vector<char> data(512u * 1024 * 1024, 1);
    std::cout << static_cast<int>(data[12345]) << "\n";
}
