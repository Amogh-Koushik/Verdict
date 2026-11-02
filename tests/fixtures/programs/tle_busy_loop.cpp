#include <iostream>

int main() {
    // volatile keeps -O2 from removing a loop that has no visible effect.
    volatile unsigned long long counter = 0;
    for (;;) {
        counter = counter + 1;
    }
}
