import sys
from collections import deque


def main() -> None:
    tokens = sys.stdin.buffer.read().split()
    rows, columns = int(tokens[0]), int(tokens[1])
    grid = b"".join(tokens[2 : 2 + rows])
    start, end = grid.index(b"S"), grid.index(b"E")
    wall = ord("#")
    distance = [-1] * (rows * columns)
    distance[start] = 0
    frontier = deque([start])
    while frontier and distance[end] == -1:
        cell = frontier.popleft()
        row, column = divmod(cell, columns)
        step = distance[cell] + 1
        for neighbour, inside in (
            (cell - columns, row > 0),
            (cell + columns, row < rows - 1),
            (cell - 1, column > 0),
            (cell + 1, column < columns - 1),
        ):
            if inside and grid[neighbour] != wall and distance[neighbour] == -1:
                distance[neighbour] = step
                frontier.append(neighbour)
    print(distance[end])


main()
