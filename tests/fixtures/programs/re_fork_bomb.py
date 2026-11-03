import os

# Every copy keeps forking until the sandbox's process limit makes fork fail.
while True:
    os.fork()
