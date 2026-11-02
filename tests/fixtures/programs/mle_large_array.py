# Repeating a byte writes every one of them, so the memory is really used.
data = b"x" * (512 * 1024 * 1024)
print(len(data))
