import argparse
import getpass
from collections.abc import Sequence
from pathlib import Path

import docker
from sqlalchemy import func, select

from verdict.core.accounts import AccountError, create_user
from verdict.core.config import Settings, load_settings
from verdict.core.db import create_db_engine, create_session_factory
from verdict.core.logs import configure_logging
from verdict.core.models import Problem, ProblemTest, problem_tests_dir
from verdict.core.verdicts import Language
from verdict.judge.runner import JudgeJob, JudgeResult, judge
from verdict.judge.worker import worker_id

SOURCE_SUFFIXES = {".cpp": Language.CPP, ".cc": Language.CPP, ".py": Language.PYTHON}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m verdict.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create_admin_parser = commands.add_parser("create-admin", help="create an administrator")
    create_admin_parser.add_argument("username")
    try_run_parser = commands.add_parser(
        "try-run", help="judge a source file against a problem's tests, without the queue"
    )
    try_run_parser.add_argument("file", type=Path)
    try_run_parser.add_argument("problem_slug")
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings.log_level)
    if args.command == "create-admin":
        create_admin(settings, args.username)
    elif args.command == "try-run":
        print(describe(try_run(settings, args.file, args.problem_slug)))


def create_admin(settings: Settings, username: str) -> None:
    password = getpass.getpass("Password: ")
    if getpass.getpass("Repeat password: ") != password:
        raise SystemExit("The two passwords don't match.")
    engine = create_db_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as db:
            create_user(db, username, password, is_admin=True)
    except AccountError as error:
        raise SystemExit(str(error)) from None
    finally:
        engine.dispose()
    print(f"Created administrator {username}.")


def try_run(settings: Settings, path: Path, problem_slug: str) -> JudgeResult:
    language = language_for_file(path)
    source = path.read_text(encoding="utf-8")
    engine = create_db_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as db:
            problem = db.scalars(select(Problem).where(Problem.slug == problem_slug)).one_or_none()
            if problem is None:
                raise SystemExit(f"No problem has the slug {problem_slug!r}.")
            if problem.tests_version == 0:
                raise SystemExit(f"{problem_slug} has no tests yet.")
            test_count = db.scalar(
                select(func.count())
                .select_from(ProblemTest)
                .where(
                    ProblemTest.problem_id == problem.id,
                    ProblemTest.version == problem.tests_version,
                )
            )
            job = JudgeJob(
                language=language,
                source=source,
                time_limit_ms=problem.time_limit_ms_for(language, settings.python_time_multiplier),
                memory_limit_mb=problem.memory_limit_mb,
                tests_dir=problem_tests_dir(
                    settings.problems_dir, problem.id, problem.tests_version
                ),
                test_count=test_count or 0,
                submission=f"try-run:{problem_slug}",
            )
    finally:
        engine.dispose()
    return judge(docker.from_env(), job, worker_id=f"try-run-{worker_id()}")


def language_for_file(path: Path) -> Language:
    language = SOURCE_SUFFIXES.get(path.suffix.lower())
    if language is None:
        raise SystemExit(f"Can't tell the language of {path.name}; name it .cpp or .py.")
    return language


def describe(result: JudgeResult) -> str:
    lines = [f"Verdict: {result.verdict.value}"]
    if result.failed_test is not None:
        lines.append(f"Failed test: {result.failed_test}")
    if result.time_ms is not None:
        lines.append(f"Time: {result.time_ms} ms")
    if result.memory_kb is not None:
        lines.append(f"Memory: {result.memory_kb} KB")
    if result.compile_output:
        lines.append("Compiler output:\n" + result.compile_output)
    return "\n".join(lines)


if __name__ == "__main__":
    main()
