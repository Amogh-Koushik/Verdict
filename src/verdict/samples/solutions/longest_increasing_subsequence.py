import sys
from bisect import bisect_left


def main() -> None:
    tokens = sys.stdin.buffer.read().split()
    count = int(tokens[0])
    # tails[k] is the smallest value that ends an increasing subsequence of length k + 1.
    tails: list[int] = []
    for token in tokens[1 : 1 + count]:
        value = int(token)
        place = bisect_left(tails, value)
        if place == len(tails):
            tails.append(value)
        else:
            tails[place] = value
    print(len(tails))


main()
