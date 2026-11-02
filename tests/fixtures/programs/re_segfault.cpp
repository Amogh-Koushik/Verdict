#include <iostream>

int main(int argc, char **) {
    // The address comes from argc (1 here, so 0), which the compiler can't see ahead of time.
    int *pointer = reinterpret_cast<int *>(static_cast<long>(argc - 1));
    std::cout << *pointer << "\n";
}
