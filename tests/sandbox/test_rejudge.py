import io
import re
import zipfile
from collections.abc import Callable
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.integration.contests import add_contest, add_contest_submission
from tests.integration.forms import csrf_token_from, log_in_as_admin
from tests.sandbox.conftest import RunningWorker, wait_until
from verdict.core.accounts import create_user
from verdict.core.config import Settings
from verdict.core.models import ContestProblem, Problem, Submission
from verdict.core.verdicts import SubmissionStatus, Verdict
from verdict.web.test_archive import store_test_archive

CORRECT = "a, b = map(int, input().split())\nprint(a + b)\n"
# Right only while both numbers are positive, which the first tests happen to be.
POSITIVE_ONLY = "a, b = map(int, input().split())\nprint(abs(a) + abs(b))\n"
FIRST_TESTS = [("1 2\n", "3\n"), ("5 7\n", "12\n")]
SECOND_TESTS = [*FIRST_TESTS, ("-4 1\n", "-3\n")]


def zip_of(tests: list[tuple[str, str]]) -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        for idx, (given, expected) in enumerate(tests, start=1):
            zipped.writestr(f"{idx}.in", given)
            zipped.writestr(f"{idx}.out", expected)
    return archive.getvalue()


def all_judged_against(db: Session, version: int) -> bool:
    db.expire_all()
    submissions = db.scalars(select(Submission)).all()
    return all(
        submission.status is SubmissionStatus.DONE and submission.tests_version == version
        for submission in submissions
    )


def cell_of(table: str, username: str) -> str:
    row = next(row for row in table.split("<tr") if f"<td>{username}</td>" in row)
    match = re.search(r'<td class="cell[^"]*">([^<]*)', row)
    assert match, f"no cell for {username}"
    return match.group(1)


def test_rejudging_after_new_tests_judges_every_submission_again_and_updates_the_scoreboard(
    client: TestClient,
    db: Session,
    app_settings: Settings,
    start_worker: Callable[..., RunningWorker],
) -> None:
    contest = add_contest(db, starts_in=-timedelta(minutes=30))
    problem = Problem(slug="sum", title="Sum")
    db.add(problem)
    db.flush()
    db.add(ContestProblem(contest_id=contest.id, label="A", problem_id=problem.id))
    store_test_archive(db, problem.id, io.BytesIO(zip_of(FIRST_TESTS)), app_settings.problems_dir)
    alice, bob = (create_user(db, name, "a long enough password") for name in ("alice", "bob"))
    for user, source, minute in ((alice, CORRECT, 5), (bob, POSITIVE_ONLY, 7)):
        add_contest_submission(
            db, contest, user, problem, minute=minute, verdict=None, source=source
        )
    start_worker()
    log_in_as_admin(client, db)
    table_url = "/contests/weekly/scoreboard/table"

    assert wait_until(lambda: all_judged_against(db, 1), timeout_seconds=30)
    before = client.get(table_url).text
    assert (cell_of(before, "alice"), cell_of(before, "bob")) == ("+", "+")

    tests_page = f"/admin/problems/{problem.id}/tests"
    uploaded = client.post(
        tests_page,
        data={"csrf_token": csrf_token_from(client.get(tests_page))},
        files={"archive": ("tests.zip", zip_of(SECOND_TESTS), "application/zip")},
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    rejudged = client.post(
        f"/admin/problems/{problem.id}/rejudge",
        data={"csrf_token": csrf_token_from(client.get(tests_page))},
        follow_redirects=False,
    )
    assert rejudged.headers["location"] == f"{tests_page}?rejudged=2"

    assert wait_until(lambda: all_judged_against(db, 2), timeout_seconds=30)
    results = {
        submission.user_id: (submission.verdict, submission.failed_test)
        for submission in db.scalars(select(Submission))
    }
    assert results == {alice.id: (Verdict.ACCEPTED, None), bob.id: (Verdict.WRONG_ANSWER, 3)}
    after = client.get(table_url).text
    assert (cell_of(after, "alice"), cell_of(after, "bob")) == ("+", "-1")
