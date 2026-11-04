from collections.abc import Callable
from functools import cache
from itertools import combinations, pairwise
from random import Random

import pytest

from verdict.samples.cases import (
    Case,
    count_components,
    longest_increasing,
    shortest_grid_path,
)
from verdict.samples.problems import SAMPLE_PROBLEMS, SampleProblem
from verdict.web.test_archive import MAX_FILE_BYTES, MAX_TESTS, MAX_TOTAL_BYTES

BY_SLUG = {sample.slug: sample for sample in SAMPLE_PROBLEMS}


@cache
def cases_of(slug: str) -> list[Case]:
    return BY_SLUG[slug].cases()


def brute_longest_increasing(values: list[int]) -> int:
    return max(
        (
            size
            for size in range(len(values) + 1)
            for picked in combinations(values, size)
            if all(a < b for a, b in pairwise(picked))
        ),
        default=0,
    )


def brute_grid_path(grid: list[str]) -> int:
    """Relax every cell's distance from its neighbours until nothing changes."""
    cells = {(r, c) for r, row in enumerate(grid) for c, char in enumerate(row) if char != "#"}
    distance = {cell: 0 if grid[cell[0]][cell[1]] == "S" else 10**9 for cell in cells}
    changed = True
    while changed:
        changed = False
        for row, column in cells:
            for neighbour in (
                (row + 1, column),
                (row - 1, column),
                (row, column + 1),
                (row, column - 1),
            ):
                if neighbour in cells and distance[neighbour] + 1 < distance[row, column]:
                    distance[row, column] = distance[neighbour] + 1
                    changed = True
    [end] = [cell for cell in cells if grid[cell[0]][cell[1]] == "E"]
    return distance[end] if distance[end] < 10**9 else -1


def brute_components(nodes: int, edges: list[tuple[int, int]]) -> int:
    neighbours: dict[int, set[int]] = {node: set() for node in range(1, nodes + 1)}
    for u, v in edges:
        neighbours[u].add(v)
        neighbours[v].add(u)
    seen: set[int] = set()
    components = 0
    for node in neighbours:
        if node not in seen:
            components += 1
            stack = [node]
            while stack:
                current = stack.pop()
                if current not in seen:
                    seen.add(current)
                    stack.extend(neighbours[current] - seen)
    return components


def test_longest_increasing_agrees_with_trying_every_subsequence() -> None:
    rng = Random(1)
    for _ in range(300):
        values = [rng.randint(-3, 3) for _ in range(rng.randint(1, 9))]
        assert longest_increasing(values) == brute_longest_increasing(values), values


def test_shortest_grid_path_agrees_with_relaxing_until_nothing_changes() -> None:
    rng = Random(2)
    for _ in range(300):
        rows, columns = rng.randint(1, 6), rng.randint(2, 6)
        cells = [["#" if rng.random() < 0.3 else "." for _ in range(columns)] for _ in range(rows)]
        start, end = rng.sample(range(rows * columns), 2)
        cells[start // columns][start % columns] = "S"
        cells[end // columns][end % columns] = "E"
        grid = ["".join(row) for row in cells]
        assert shortest_grid_path(grid) == brute_grid_path(grid), grid


def test_count_components_agrees_with_searching_from_every_node() -> None:
    rng = Random(3)
    for _ in range(300):
        nodes = rng.randint(1, 12)
        edges = [(rng.randint(1, nodes), rng.randint(1, nodes)) for _ in range(rng.randint(0, 12))]
        assert count_components(nodes, edges) == brute_components(nodes, edges), (nodes, edges)


def check_sum(given: str) -> None:
    a, b = map(int, given.split())
    assert all(abs(value) <= 10**18 for value in (a, b))


def check_array(low: int, high: int) -> Callable[[str], None]:
    def check(given: str) -> None:
        first, values, rest = given.split("\n", 2)
        numbers = list(map(int, values.split()))
        assert rest == ""
        assert 1 <= int(first) <= 200_000
        assert len(numbers) == int(first)
        assert all(low <= value <= high for value in numbers)

    return check


def check_grid(given: str) -> None:
    header, *rows = given.splitlines()
    height, width = map(int, header.split())
    assert 1 <= height <= 500
    assert 1 <= width <= 500
    assert height * width >= 2
    assert len(rows) == height
    assert all(len(row) == width and set(row) <= set(".#SE") for row in rows)
    text = "".join(rows)
    assert (text.count("S"), text.count("E")) == (1, 1)


def check_graph(given: str) -> None:
    header, *lines = given.splitlines()
    nodes, edges = map(int, header.split())
    assert 1 <= nodes <= 200_000
    assert 0 <= edges <= 200_000
    assert len(lines) == edges
    for line in lines:
        u, v = map(int, line.split())
        assert 1 <= u <= nodes
        assert 1 <= v <= nodes


INPUT_CHECKS: dict[str, Callable[[str], None]] = {
    "sum-of-two-numbers": check_sum,
    "count-distinct-values": check_array(1, 10**9),
    "shortest-path-in-a-grid": check_grid,
    "longest-increasing-subsequence": check_array(-(10**9), 10**9),
    "number-of-connected-components": check_graph,
}


@pytest.fixture(params=SAMPLE_PROBLEMS, ids=lambda sample: sample.slug)
def sample(request: pytest.FixtureRequest) -> SampleProblem:
    chosen: SampleProblem = request.param
    return chosen


def test_every_input_keeps_to_the_limits_in_the_statement(sample: SampleProblem) -> None:
    for idx, case in enumerate(cases_of(sample.slug), start=1):
        try:
            INPUT_CHECKS[sample.slug](case.given)
        except (AssertionError, ValueError) as error:
            raise AssertionError(f"test {idx} breaks the statement's limits") from error


def test_the_tests_fit_the_archive_limits(sample: SampleProblem) -> None:
    cases = cases_of(sample.slug)
    sizes = [len(text.encode()) for case in cases for text in (case.given, case.expected)]
    assert 1 <= len(cases) <= MAX_TESTS
    assert max(sizes) <= MAX_FILE_BYTES
    assert sum(sizes) <= MAX_TOTAL_BYTES


def test_the_statements_example_is_the_first_test(sample: SampleProblem) -> None:
    [first, *_] = cases_of(sample.slug)
    assert f"```\n{first.given}```" in sample.statement_md
    assert f"```\n{first.expected}```" in sample.statement_md


def test_the_tests_are_the_same_every_time(sample: SampleProblem) -> None:
    assert sample.cases() == cases_of(sample.slug)


def test_every_sample_has_a_reference_solution_in_both_languages(sample: SampleProblem) -> None:
    assert sample.solution(".cpp").is_file()
    assert sample.solution(".py").is_file()


def test_slugs_are_unique_and_two_problems_are_public() -> None:
    assert len(BY_SLUG) == 5
    assert [sample.slug for sample in SAMPLE_PROBLEMS if sample.is_public] == [
        "sum-of-two-numbers",
        "count-distinct-values",
    ]
