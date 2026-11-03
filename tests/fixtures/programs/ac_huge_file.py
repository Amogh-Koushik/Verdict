# Prints the right answer only if the sandbox stops the file well before 100 MB.
a, b = map(int, input().split())
block = b"x" * (1024 * 1024)
try:
    with open("/box/huge", "wb") as huge:
        for _ in range(100):
            huge.write(block)
            huge.flush()
    print("wrote it all")
except OSError:
    print(a + b)
