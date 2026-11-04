from bisect import bisect_left
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from random import Random

GRID_MOVES = ((1, 0), (-1, 0), (0, 1), (0, -1))


@dataclass(frozen=True)
class Case:
    given: str
    expected: str


def sum_of_two_numbers_cases(rng: Random) -> list[Case]:
    limit = 10**18
    pairs = [(1, 2), (-5, 3), (0, 0), (limit, limit), (-limit, -limit), (limit, -limit)]
    pairs += [(rng.randint(-limit, limit), rng.randint(-limit, limit)) for _ in range(4)]
    return [Case(f"{a} {b}\n", f"{a + b}\n") for a, b in pairs]


def count_distinct_cases(rng: Random) -> list[Case]:
    arrays = [
        [3, 1, 3, 2, 1],
        [7],
        [5] * 10,
        [rng.randint(1, 10) for _ in range(1000)],
        [rng.randint(1, 10**9) for _ in range(200_000)],
        [rng.randint(1, 1000) for _ in range(200_000)],
        list(range(200_000, 0, -1)),
    ]
    return [Case(_array_input(values), f"{len(set(values))}\n") for values in arrays]


def grid_path_cases(rng: Random) -> list[Case]:
    grids = [
        ["S..", ".#.", "..E"],
        ["SE"],
        ["S#.", "##.", "..E"],
        _open_grid(500, 500),
        _random_grid(rng, 500, 500, wall_share=0.25),
        _random_grid(rng, 500, 500, wall_share=0.35),
        _serpentine_grid(499, 500),
        _walled_in_grid(500, 500),
    ]
    return [Case(_grid_input(grid), f"{shortest_grid_path(grid)}\n") for grid in grids]


def longest_increasing_cases(rng: Random) -> list[Case]:
    limit = 10**9
    arrays = [
        [3, 1, 4, 1, 5, 9, 2, 6],
        [42],
        list(range(1, 200_001)),
        list(range(200_000, 0, -1)),
        [7] * 200_000,
        [rng.randint(-limit, limit) for _ in range(200_000)],
        [rng.randint(1, 100) for _ in range(200_000)],
        [index // 2 if index % 2 else limit - index for index in range(200_000)],
    ]
    return [Case(_array_input(values), f"{longest_increasing(values)}\n") for values in arrays]


def connected_components_cases(rng: Random) -> list[Case]:
    graphs = [
        (6, [(1, 2), (2, 3), (4, 5)]),
        (1, []),
        (5, []),
        (10, [(3, 3), (1, 2), (2, 1), (1, 2), (9, 10)]),
        (200_000, [(node, node + 1) for node in range(1, 200_000)]),
        (200_000, [(1, node) for node in range(2, 200_001)]),
        (200_000, _random_edges(rng, 200_000, 100_000)),
        (200_000, _random_edges(rng, 200_000, 200_000)),
    ]
    return [Case(_graph_input(n, edges), f"{count_components(n, edges)}\n") for n, edges in graphs]


def shortest_grid_path(grid: Sequence[str]) -> int:
    """Fewest moves from S to E through free cells, or -1 if E can't be reached."""
    rows, columns = len(grid), len(grid[0])
    start = next((r, c) for r in range(rows) for c in range(columns) if grid[r][c] == "S")
    distance = {start: 0}
    frontier = deque([start])
    while frontier:
        row, column = frontier.popleft()
        if grid[row][column] == "E":
            return distance[row, column]
        for d_row, d_column in GRID_MOVES:
            step = (row + d_row, column + d_column)
            if (
                0 <= step[0] < rows
                and 0 <= step[1] < columns
                and grid[step[0]][step[1]] != "#"
                and step not in distance
            ):
                distance[step] = distance[row, column] + 1
                frontier.append(step)
    return -1


def longest_increasing(values: Sequence[int]) -> int:
    """Length of the longest strictly increasing subsequence, by patience sorting."""
    tails: list[int] = []
    for value in values:
        place = bisect_left(tails, value)
        if place == len(tails):
            tails.append(value)
        else:
            tails[place] = value
    return len(tails)


def count_components(nodes: int, edges: Sequence[tuple[int, int]]) -> int:
    parent = list(range(nodes + 1))

    def root(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    components = nodes
    for u, v in edges:
        root_u, root_v = root(u), root(v)
        if root_u != root_v:
            parent[root_u] = root_v
            components -= 1
    return components


def _array_input(values: Sequence[int]) -> str:
    return f"{len(values)}\n{' '.join(map(str, values))}\n"


def _grid_input(grid: Sequence[str]) -> str:
    return f"{len(grid)} {len(grid[0])}\n" + "".join(f"{row}\n" for row in grid)


def _graph_input(nodes: int, edges: Sequence[tuple[int, int]]) -> str:
    return f"{nodes} {len(edges)}\n" + "".join(f"{u} {v}\n" for u, v in edges)


def _open_grid(rows: int, columns: int) -> list[str]:
    cells = [["."] * columns for _ in range(rows)]
    cells[0][0], cells[-1][-1] = "S", "E"
    return ["".join(row) for row in cells]


def _random_grid(rng: Random, rows: int, columns: int, wall_share: float) -> list[str]:
    cells = [
        ["#" if rng.random() < wall_share else "." for _ in range(columns)] for _ in range(rows)
    ]
    start, end = rng.sample(range(rows * columns), 2)
    cells[start // columns][start % columns] = "S"
    cells[end // columns][end % columns] = "E"
    return ["".join(row) for row in cells]


def _serpentine_grid(rows: int, columns: int) -> list[str]:
    """Walls on every odd row, each with one gap at alternating ends: the path crosses every row."""
    cells = [["."] * columns for _ in range(rows)]
    gap = columns - 1
    for row in range(1, rows, 2):
        cells[row] = ["#"] * columns
        cells[row][gap] = "."
        gap = columns - 1 - gap
    # `gap` now names the end of the last row away from the last wall's gap.
    cells[0][0], cells[-1][gap] = "S", "E"
    return ["".join(row) for row in cells]


def _walled_in_grid(rows: int, columns: int) -> list[str]:
    cells = [list(row) for row in _open_grid(rows, columns)]
    cells[-2][-1] = cells[-1][-2] = "#"
    return ["".join(row) for row in cells]


def _random_edges(rng: Random, nodes: int, count: int) -> list[tuple[int, int]]:
    return [(rng.randint(1, nodes), rng.randint(1, nodes)) for _ in range(count)]
