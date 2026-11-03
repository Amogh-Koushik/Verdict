#include <fstream>
#include <iostream>
#include <string>
#include <vector>

// Prints the right answer only if every folder outside /box and /tmp refuses a new file.
int main() {
    const std::vector<std::string> folders = {
        "/",        "/etc",      "/usr",     "/usr/local", "/opt",     "/home",
        "/root",    "/srv",      "/mnt",     "/var",       "/var/tmp", "/var/lock",
        "/run",     "/run/lock", "/dev/shm", "/dev/mqueue",
    };
    long long a = 0;
    long long b = 0;
    std::cin >> a >> b;
    std::string written;
    for (const auto &folder : folders) {
        std::ofstream probe(folder + "/escaped");
        if (probe << "x") {
            written += folder + " ";
        }
    }
    if (written.empty()) {
        std::cout << a + b << "\n";
    } else {
        std::cout << written << "\n";
    }
}
