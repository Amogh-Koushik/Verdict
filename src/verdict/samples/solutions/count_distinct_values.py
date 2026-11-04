import sys


def main() -> None:
    tokens = sys.stdin.buffer.read().split()
    count = int(tokens[0])
    print(len(set(tokens[1 : 1 + count])))


main()
