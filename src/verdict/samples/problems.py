from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from random import Random

from verdict.samples.cases import (
    Case,
    connected_components_cases,
    count_distinct_cases,
    grid_path_cases,
    longest_increasing_cases,
    sum_of_two_numbers_cases,
)

# A reference solution in C++ and in Python for every sample problem, named after its slug.
SOLUTIONS_DIR = Path(__file__).parent / "solutions"


@dataclass(frozen=True)
class SampleProblem:
    slug: str
    title: str
    statement_md: str
    time_limit_ms: int
    is_public: bool
    make_cases: Callable[[Random], list[Case]]

    def cases(self) -> list[Case]:
        # Seeded by slug, so every run produces the same tests.
        return self.make_cases(Random(self.slug))  # noqa: S311  (test data, not secrets)

    def solution(self, suffix: str) -> Path:
        return SOLUTIONS_DIR / f"{self.slug.replace('-', '_')}{suffix}"


SUM_OF_TWO_NUMBERS = SampleProblem(
    slug="sum-of-two-numbers",
    title="Sum of two numbers",
    time_limit_ms=1000,
    is_public=True,
    make_cases=sum_of_two_numbers_cases,
    statement_md="""Print the sum of two integers.

## Input

One line with two integers *a* and *b*, where -10¹⁸ ≤ *a*, *b* ≤ 10¹⁸.

## Output

The value of *a* + *b*.

## Example

```
1 2
```

```
3
```
""",
)

COUNT_DISTINCT = SampleProblem(
    slug="count-distinct-values",
    title="Count distinct values",
    time_limit_ms=1000,
    is_public=True,
    make_cases=count_distinct_cases,
    statement_md="""Count how many different values a list holds.

## Input

The first line holds *n* (1 ≤ *n* ≤ 200 000). The second holds *n* integers, each
between 1 and 10⁹.

## Output

The number of distinct values among them.

## Example

```
5
3 1 3 2 1
```

```
3
```
""",
)

GRID_PATH = SampleProblem(
    slug="shortest-path-in-a-grid",
    title="Shortest path in a grid",
    time_limit_ms=1000,
    is_public=False,
    make_cases=grid_path_cases,
    statement_md="""A grid has free cells (`.`) and walls (`#`). You start on `S` and want to
reach `E`, moving one cell up, down, left or right at a time, never onto a wall or off the
grid. `S` and `E` are free cells.

## Input

The first line holds *n* and *m* (1 ≤ *n*, *m* ≤ 500, and the grid has at least 2 cells).
Then *n* lines of *m* characters each, from `.`, `#`, `S` and `E`. There is exactly one `S`
and one `E`.

## Output

The fewest moves needed to get from `S` to `E`, or `-1` if `E` can't be reached.

## Example

```
3 3
S..
.#.
..E
```

```
4
```
""",
)

LONGEST_INCREASING = SampleProblem(
    slug="longest-increasing-subsequence",
    title="Longest increasing subsequence",
    time_limit_ms=1000,
    is_public=False,
    make_cases=longest_increasing_cases,
    statement_md="""Find the length of the longest strictly increasing subsequence of a list: the
most values you can pick, keeping their order, so that each is larger than the one before.

## Input

The first line holds *n* (1 ≤ *n* ≤ 200 000). The second holds *n* integers, each between
-10⁹ and 10⁹.

## Output

The length of the longest strictly increasing subsequence.

## Example

```
8
3 1 4 1 5 9 2 6
```

```
4
```

One answer is 3, 4, 5, 9.
""",
)

CONNECTED_COMPONENTS = SampleProblem(
    slug="number-of-connected-components",
    title="Number of connected components",
    time_limit_ms=1000,
    is_public=False,
    make_cases=connected_components_cases,
    statement_md="""An undirected graph has *n* nodes, numbered from 1, and *m* edges. How many
connected components does it have? An edge may join a node to itself, and two nodes may be
joined more than once.

## Input

The first line holds *n* and *m* (1 ≤ *n* ≤ 200 000, 0 ≤ *m* ≤ 200 000). Each of the next
*m* lines holds an edge: two nodes *u* and *v*, with 1 ≤ *u*, *v* ≤ *n*.

## Output

The number of connected components.

## Example

```
6 3
1 2
2 3
4 5
```

```
3
```

The components are {1, 2, 3}, {4, 5} and {6}.
""",
)

# In contest order: labels A to E.
SAMPLE_PROBLEMS = (
    SUM_OF_TWO_NUMBERS,
    COUNT_DISTINCT,
    GRID_PATH,
    LONGEST_INCREASING,
    CONNECTED_COMPONENTS,
)
SAMPLE_CONTEST_SLUG = "sample-contest"
SAMPLE_CONTEST_TITLE = "Sample contest"
# Long enough after seeding to see the problems hidden, short enough to wait for.
SAMPLE_CONTEST_STARTS_AFTER = timedelta(minutes=10)
SAMPLE_CONTEST_LENGTH = timedelta(hours=2)
