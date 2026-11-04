import argparse
import secrets
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from verdict.core.accounts import create_user, find_user
from verdict.core.config import load_settings
from verdict.core.db import create_db_engine
from verdict.core.events import QUEUE_CHANNEL
from verdict.core.models import Problem, Submission
from verdict.core.verdicts import Language, Verdict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROGRAMS_DIR = PROJECT_ROOT / "tests" / "fixtures" / "programs"
PROBLEM_SLUG = "sum-of-two-numbers"
LOAD_TEST_USER = "load-test"
POLL_SECONDS = 0.5
ROUND_TIMEOUT_SECONDS = 30 * 60
WORKER_START_TIMEOUT_SECONDS = 60
# A contest-like mix, cycled through in this order: half accepted, the rest spread over the
# other verdicts. Each file's name starts with the verdict it gets.
MIX = [
    *["ac_sum.py"] * 6,
    *["ac_sum.cpp"] * 4,
    "wa_off_by_one.py",
    "wa_off_by_one.py",
    "wa_off_by_one.cpp",
    "re_exception.py",
    "re_exit_code.cpp",
    "ce_syntax_error.cpp",
    "ce_syntax_error.cpp",
    "tle_busy_loop.py",
    "tle_busy_loop.cpp",
    "mle_large_array.py",
]
LANGUAGES = {".py": Language.PYTHON, ".cpp": Language.CPP}
ROUND_STATS = text(
    """
    SELECT extract(epoch FROM max(judged_at) - min(created_at)) AS seconds,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM judged_at - created_at))
               AS median_seconds,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM judged_at - created_at))
               AS p95_seconds
    FROM submissions
    WHERE id = ANY(:ids)
    """
)


@dataclass(frozen=True)
class Program:
    name: str
    language: Language
    source: str
    verdict: Verdict


@dataclass(frozen=True)
class RoundResult:
    workers: int
    submissions: int
    seconds: float
    median_seconds: float
    p95_seconds: float
    wrong_verdicts: int

    @property
    def per_minute(self) -> float:
        return self.submissions / self.seconds * 60


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Measure judging throughput on the running Compose stack: queue a burst of "
            f"submissions to {PROBLEM_SLUG} (run `make seed` first) with 1, 2 and 4 workers."
        )
    )
    parser.add_argument("--submissions", type=int, default=100, help="per round (default 100)")
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4])
    args = parser.parse_args()

    programs = [load_program(name) for name in MIX]
    engine = create_db_engine(load_settings().database_url)
    results: list[RoundResult] = []
    try:
        problem_id, user_id = prepare(engine)
        # Only from here on, so a failure above leaves the running workers untouched.
        try:
            for workers in args.workers:
                start_workers(workers)
                result = run_round(engine, problem_id, user_id, programs, workers, args.submissions)
                print(f"{workers} worker(s): {result.submissions} judged in {result.seconds:.1f} s")
                results.append(result)
        finally:
            start_workers(1)
    except OperationalError:
        raise SystemExit("Can't reach Postgres; check DATABASE_URL (see the README).") from None
    finally:
        engine.dispose()
    print()
    print(format_table(results))


def load_program(name: str) -> Program:
    path = PROGRAMS_DIR / name
    verdict = Verdict(name.split("_", 1)[0].upper())
    return Program(name, LANGUAGES[path.suffix], path.read_text(encoding="utf-8"), verdict)


def prepare(engine: Engine) -> tuple[int, int]:
    with Session(engine) as db:
        problem = db.scalars(select(Problem).where(Problem.slug == PROBLEM_SLUG)).one_or_none()
        if problem is None or problem.tests_version == 0:
            raise SystemExit(f"The problem {PROBLEM_SLUG} isn't there; run `make seed` first.")
        user = find_user(db, LOAD_TEST_USER)
        if user is None:
            # Nobody logs in as this user, and this may be a public server.
            user = create_user(db, LOAD_TEST_USER, secrets.token_urlsafe(32))
        return problem.id, user.id


def start_workers(count: int) -> None:
    """Replace the worker containers with `count` fresh ones and wait until each is ready."""
    docker(
        "compose", "up", "--detach", "--no-deps", "--force-recreate",
        "--scale", f"worker={count}", "worker",
    )  # fmt: skip
    deadline = time.monotonic() + WORKER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if ready_workers() == count:
            return
        time.sleep(POLL_SECONDS)
    raise SystemExit(f"{count} worker(s) did not start within {WORKER_START_TIMEOUT_SECONDS} s.")


def ready_workers() -> int:
    container_ids = docker("compose", "ps", "--quiet", "--status", "running", "worker").split()
    return sum("worker started" in docker("logs", container_id) for container_id in container_ids)


def docker(*args: str) -> str:
    """Run the Docker CLI from the project root and return what it printed."""
    finished = subprocess.run(  # noqa: S603  (fixed arguments built here, no shell)
        ["docker", *args],  # noqa: S607  (docker is found on PATH, as `make` finds it)
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    # Containers log to stderr, so both streams are returned.
    return finished.stdout + finished.stderr


def run_round(
    engine: Engine,
    problem_id: int,
    user_id: int,
    programs: list[Program],
    workers: int,
    count: int,
) -> RoundResult:
    chosen = [programs[index % len(programs)] for index in range(count)]
    ids = queue_burst(engine, problem_id, user_id, chosen)
    wait_until_judged(engine, ids)
    with engine.connect() as connection:
        stats = connection.execute(ROUND_STATS, {"ids": ids}).one()
        rows = connection.execute(
            select(Submission.id, Submission.verdict).where(Submission.id.in_(ids))
        ).all()
    verdicts = {row.id: row.verdict for row in rows}
    wrong = sum(
        verdicts[id_] is not program.verdict for id_, program in zip(ids, chosen, strict=True)
    )
    return RoundResult(
        workers=workers,
        submissions=count,
        seconds=float(stats.seconds),
        median_seconds=float(stats.median_seconds),
        p95_seconds=float(stats.p95_seconds),
        wrong_verdicts=wrong,
    )


def queue_burst(
    engine: Engine, problem_id: int, user_id: int, programs: list[Program]
) -> list[int]:
    """Insert every submission in one transaction; they share created_at, which starts the clock."""
    with Session(engine) as db:
        submissions = [
            Submission(
                user_id=user_id,
                problem_id=problem_id,
                language=program.language,
                source=program.source,
            )
            for program in programs
        ]
        db.add_all(submissions)
        db.flush()
        db.execute(text("SELECT pg_notify(:channel, '')"), {"channel": QUEUE_CHANNEL})
        db.commit()
        return [submission.id for submission in submissions]


def wait_until_judged(engine: Engine, ids: list[int]) -> None:
    pending = text(
        "SELECT count(*) FROM submissions WHERE id = ANY(:ids) AND status IN ('queued', 'judging')"
    )
    deadline = time.monotonic() + ROUND_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            if connection.execute(pending, {"ids": ids}).scalar_one() == 0:
                return
        time.sleep(POLL_SECONDS)
    raise SystemExit(f"Not every submission was judged within {ROUND_TIMEOUT_SECONDS} s.")


def format_table(results: list[RoundResult]) -> str:
    header = ("Workers", "Submissions", "Wall time", "Throughput", "Median", "p95", "Wrong")
    rows = [
        (
            str(result.workers),
            str(result.submissions),
            f"{result.seconds:.1f} s",
            f"{result.per_minute:.1f}/min",
            f"{result.median_seconds:.1f} s",
            f"{result.p95_seconds:.1f} s",
            str(result.wrong_verdicts),
        )
        for result in results
    ]
    widths = [max(len(cell) for cell in column) for column in zip(header, *rows, strict=True)]
    lines = [header, *rows]
    return "\n".join(
        "  ".join(cell.rjust(width) for cell, width in zip(line, widths, strict=True))
        for line in lines
    )


if __name__ == "__main__":
    main()
