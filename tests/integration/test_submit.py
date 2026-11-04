import time
from collections.abc import Iterator
from dataclasses import replace

import httpx2
import psycopg
import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from tests.integration.forms import csrf_token_from, log_in, log_in_as_admin, register
from verdict.core.config import Settings
from verdict.core.events import QUEUE_CHANNEL
from verdict.core.models import MAX_SOURCE_BYTES, Problem, Submission, User
from verdict.core.verdicts import Language, QueuePriority, SubmissionStatus
from verdict.web.main import create_app

PASSWORD = "a long enough password"
SOLUTION = "a, b = map(int, input().split())\nprint(a + b)\n"


@pytest.fixture
def problem(db: Session) -> Problem:
    problem = Problem(slug="sum", title="Sum", is_public=True, tests_version=1)
    db.add(problem)
    db.commit()
    return problem


def wait_for_room_in_window(window_seconds: int, needed_seconds: float = 3) -> None:
    """Sleep into the next fixed window if the current one ends too soon for the test's requests."""
    left = window_seconds - time.time() % window_seconds
    if left < needed_seconds:
        time.sleep(left)


def submit(
    client: TestClient,
    slug: str = "sum",
    source: str = SOLUTION,
    language: str = "python",
    files: dict[str, tuple[str, bytes]] | None = None,
) -> httpx2.Response:
    csrf_token = csrf_token_from(client.get(f"/problems/{slug}"))
    return client.post(
        f"/problems/{slug}/submit",
        data={"csrf_token": csrf_token, "language": language, "source": source},
        files=files,
        follow_redirects=False,
    )


def submission_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Submission)) or 0


@pytest.fixture
def contestant(client: TestClient, problem: Problem) -> TestClient:
    register(client, "alice", PASSWORD)
    return client


def test_a_valid_submission_is_queued_and_shown_to_its_author(
    contestant: TestClient, db: Session
) -> None:
    response = submit(contestant)
    assert response.status_code == 303
    submission = db.scalars(select(Submission)).one()
    assert response.headers["location"] == f"/submissions/{submission.id}"
    assert submission.status is SubmissionStatus.QUEUED
    assert submission.language is Language.PYTHON
    assert submission.source == SOLUTION
    assert submission.contest_id is None
    page = contestant.get(f"/submissions/{submission.id}")
    assert page.status_code == 200
    assert "Queued" in page.text
    assert "print(a + b)" in page.text


def test_queueing_a_submission_notifies_listening_workers(
    contestant: TestClient, engine: Engine
) -> None:
    conninfo = make_url(engine.url).set(drivername="postgresql").render_as_string(False)
    with psycopg.connect(conninfo, autocommit=True) as listener:
        listener.execute(f"LISTEN {QUEUE_CHANNEL}")
        response = submit(contestant)
        payloads = [note.payload for note in listener.notifies(timeout=2, stop_after=1)]
    assert payloads == [response.headers["location"].rsplit("/", 1)[1]]


def test_an_uploaded_file_is_used_instead_of_the_text_box(
    contestant: TestClient, db: Session
) -> None:
    upload = b"#include <iostream>\nint main() { std::cout << 3; }\n"
    files = {"source_file": ("a.cpp", upload)}
    response = submit(contestant, source="", language="cpp", files=files)
    assert response.status_code == 303
    assert db.scalars(select(Submission.source)).one() == upload.decode()


@pytest.mark.parametrize(
    ("source", "language", "status_code", "message"),
    [
        ("x" * (MAX_SOURCE_BYTES + 1), "python", 413, "at most 64 KB"),
        ("   \n", "python", 400, "Paste your solution"),
        (SOLUTION, "ruby", 400, "Choose C++ or Python"),
        ("print(1)\x00", "python", 400, "NUL"),
    ],
    ids=["over 64 KB", "blank", "unknown language", "NUL byte"],
)
def test_an_invalid_submission_is_refused_and_the_text_is_kept(
    contestant: TestClient, db: Session, source: str, language: str, status_code: int, message: str
) -> None:
    response = submit(contestant, source=source, language=language)
    assert response.status_code == status_code
    assert message in response.text
    assert submission_count(db) == 0


def test_a_source_of_exactly_64_kb_is_accepted(contestant: TestClient) -> None:
    assert submit(contestant, source="#" * MAX_SOURCE_BYTES).status_code == 303


def test_a_file_that_is_not_utf8_is_refused(contestant: TestClient, db: Session) -> None:
    response = submit(contestant, source="", files={"source_file": ("a.py", b"\xff\xfe")})
    assert response.status_code == 400
    assert "UTF-8" in response.text
    assert submission_count(db) == 0


def test_a_problem_without_tests_cannot_be_submitted_to(
    contestant: TestClient, db: Session, problem: Problem
) -> None:
    problem.tests_version = 0
    db.commit()
    response = submit(contestant)
    assert response.status_code == 400
    assert "no tests yet" in response.text


def test_a_hidden_problem_cannot_be_submitted_to(
    contestant: TestClient, db: Session, problem: Problem
) -> None:
    csrf_token = csrf_token_from(contestant.get("/problems/sum"))
    problem.is_public = False
    db.commit()
    response = contestant.post(
        "/problems/sum/submit",
        data={"csrf_token": csrf_token, "language": "python", "source": SOLUTION},
    )
    assert response.status_code == 404
    assert submission_count(db) == 0


def test_submitting_without_the_form_token_is_rejected(contestant: TestClient, db: Session) -> None:
    response = contestant.post(
        "/problems/sum/submit", data={"language": "python", "source": SOLUTION}
    )
    assert response.status_code == 403
    assert submission_count(db) == 0


def test_a_logged_out_visitor_is_sent_to_log_in(client: TestClient, problem: Problem) -> None:
    csrf_token = csrf_token_from(client.get("/register"))
    response = client.post(
        "/problems/sum/submit",
        data={"csrf_token": csrf_token, "language": "python", "source": SOLUTION},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_a_second_submission_within_ten_seconds_is_refused(
    contestant: TestClient, db: Session
) -> None:
    wait_for_room_in_window(10)
    assert submit(contestant).status_code == 303
    refused = submit(contestant)
    assert refused.status_code == 429
    assert 1 <= int(refused.headers["retry-after"]) <= 10
    assert "too quickly" in refused.text
    assert submission_count(db) == 1


def test_the_sixty_first_submission_in_an_hour_is_refused(
    contestant: TestClient, db: Session, redis: Redis
) -> None:
    wait_for_room_in_window(3600)
    user_id = db.scalars(select(User.id).where(User.username == "alice")).one()
    hour_window = int(time.time() // 3600)
    redis.set(f"ratelimit:submit:{user_id}:3600:{hour_window}", 60)
    refused = submit(contestant)
    assert refused.status_code == 429
    assert submission_count(db) == 0


@pytest.fixture
def busy_client(
    app_settings: Settings, engine: Engine, redis: Redis, problem: Problem
) -> Iterator[TestClient]:
    with TestClient(create_app(replace(app_settings, queue_limit=2))) as test_client:
        register(test_client, "bob", PASSWORD)
        yield test_client


def test_a_full_queue_refuses_with_503_and_retry_after(
    busy_client: TestClient, db: Session, problem: Problem, redis: Redis
) -> None:
    bob = db.scalars(select(User.id).where(User.username == "bob")).one()
    db.add_all(
        Submission(user_id=bob, problem_id=problem.id, language=Language.PYTHON, source="1")
        for _ in range(2)
    )
    db.commit()
    refused = submit(busy_client)
    assert refused.status_code == 503
    assert refused.headers["retry-after"] == "30"
    assert "busy" in refused.text
    assert submission_count(db) == 2
    assert redis.keys("ratelimit:submit:*") == [], "a refused submission must not use up the limit"


def test_rejudged_submissions_waiting_do_not_count_toward_the_queue_limit(
    busy_client: TestClient, db: Session, problem: Problem
) -> None:
    bob = db.scalars(select(User.id).where(User.username == "bob")).one()
    db.add_all(
        Submission(
            user_id=bob,
            problem_id=problem.id,
            language=Language.PYTHON,
            source="1",
            priority=QueuePriority.REJUDGE,
        )
        for _ in range(5)
    )
    db.commit()
    assert submit(busy_client).status_code == 303
    assert submission_count(db) == 6


def test_only_the_author_and_admins_can_open_a_submission(
    contestant: TestClient, db: Session, app_settings: Settings
) -> None:
    location = submit(contestant).headers["location"]
    assert contestant.get("/submissions").text.count("/submissions/") >= 1
    with TestClient(create_app(app_settings)) as other:
        register(other, "mallory", PASSWORD)
        assert other.get(location).status_code == 404
        assert location not in other.get("/submissions").text
    with TestClient(create_app(app_settings)) as admin:
        log_in_as_admin(admin, db)
        assert admin.get(location).status_code == 200


def test_my_submissions_needs_a_login(client: TestClient) -> None:
    response = client.get("/submissions", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_logging_in_again_keeps_earlier_submissions(contestant: TestClient) -> None:
    location = submit(contestant).headers["location"]
    contestant.cookies.clear()
    log_in(contestant, "alice", PASSWORD)
    assert location in contestant.get("/submissions").text
