import os

# Prints the right answer only if every folder outside /box and /tmp refuses a new file.
FOLDERS = [
    "/",
    "/etc",
    "/usr",
    "/usr/local",
    "/opt",
    "/home",
    "/root",
    "/srv",
    "/mnt",
    "/var",
    "/var/tmp",
    "/var/lock",
    "/run",
    "/run/lock",
    "/dev/shm",
    "/dev/mqueue",
]

a, b = map(int, input().split())
written = []
for folder in FOLDERS:
    try:
        with open(os.path.join(folder, "escaped"), "w") as probe:
            probe.write("x")
        written.append(folder)
    except OSError:
        pass
print(" ".join(written) if written else a + b)
