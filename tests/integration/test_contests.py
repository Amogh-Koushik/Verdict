import json
import re
from datetime import timedelta

import httpx2
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.integration.contests import add_contest, add_contest_problem, add_contest_submission
from tests.integration.events import next_event
from tests.integration.forms import csrf_token_from, log_in_as_admin, register
from verdict.core.accounts import create_user
from verdict.core.events import contest_channel, scoreboard_key
from verdict.core.models import Submission
from verdict.core.verdicts import Verdict

PASSWORD = "a long enough password"
SOLUTION = "print(sum(map(int, input().split())))\n"
STARTED = -timedelta(minutes=10)


def submit(client: TestClient, path: str, source: str = SOLUTION, **fields: str) -> httpx2.Response:
    csrf_token = csrf_token_from(client.get("/contests"))
    data = {"csrf_token": csrf_token, "language": "python", "source": source, **fields}
    return client.post(path, data=data, follow_redirects=False)


def submit_to_contest(
    client: TestClient, label: str = "A", source: str = SOLUTION
) -> httpx2.Response:
    return submit(client, "/contests/weekly/submit", source, label=label)


def contest_of_only_submission(db: Session) -> int | None:
    return db.scalars(select(Submission.contest_id)).one()


def test_a_contests_problems_are_hidden_before_it_starts(client: TestClient, db: Session) -> None:
    contest = add_contest(db, starts_in=timedelta(minutes=30))
    add_contest_problem(db, contest, "A", "secret-sum")
    register(client, "alice", PASSWORD)

    page = client.get("/contests/weekly")
    assert page.status_code == 200
    assert "Secret Sum" not in page.text
    assert "The problems appear here when the contest starts." in page.text
    assert client.get("/contests/weekly/problems/A").status_code == 404
    assert client.get("/problems/secret-sum").status_code == 404
    assert "secret-sum" not in client.get("/problems").text
    assert submit_to_contest(client).status_code == 404
    assert client.get("/contests/weekly/scoreboard/table").status_code == 404
    assert client.get(f"/live/contests/{contest.id}").status_code == 404
    assert db.scalars(select(Submission)).first() is None


def test_a_contests_problems_appear_once_it_has_started(client: TestClient, db: Session) -> None:
    contest = add_contest(db, starts_in=STARTED)
    add_contest_problem(db, contest, "A", "secret-sum")
    page = client.get("/contests/weekly").text
    assert 'href="/contests/weekly/problems/A">Secret Sum</a>' in page
    problem_page = client.get("/contests/weekly/problems/A")
    assert problem_page.status_code == 200
    assert "<h1>A. Secret Sum</h1>" in problem_page.text
    assert client.get("/problems/secret-sum").status_code == 200


def test_an_admin_sees_a_contests_problems_before_it_starts(
    client: TestClient, db: Session
) -> None:
    contest = add_contest(db, starts_in=timedelta(minutes=30))
    add_contest_problem(db, contest, "A", "secret-sum")
    log_in_as_admin(client, db)
    page = client.get("/contests/weekly").text
    assert "Hidden from contestants until the contest starts." in page
    assert 'href="/contests/weekly/problems/A"' in page
    assert client.get("/contests/weekly/problems/A").status_code == 200


def test_a_submission_from_the_contest_page_during_the_contest_counts_for_it(
    client: TestClient, db: Session
) -> None:
    contest = add_contest(db, starts_in=STARTED)
    problem = add_contest_problem(db, contest, "A", "secret-sum")
    register(client, "alice", PASSWORD)

    response = submit_to_contest(client)

    assert response.status_code == 303
    submission = db.scalars(select(Submission)).one()
    assert response.headers["location"] == f"/submissions/{submission.id}"
    assert (submission.contest_id, submission.problem_id) == (contest.id, problem.id)
    assert contest.starts_at <= submission.created_at < contest.ends_at
    table = client.get("/contests/weekly/scoreboard/table").text
    assert "<td>alice</td>" in table
    assert 'title="Still being judged">?</td>' in table


def test_an_admin_can_submit_during_the_contest_without_being_ranked(
    client: TestClient, db: Session
) -> None:
    contest = add_contest(db, starts_in=STARTED)
    add_contest_problem(db, contest, "A", "secret-sum")
    log_in_as_admin(client, db)

    assert submit_to_contest(client).status_code == 303

    assert contest_of_only_submission(db) == contest.id
    table = client.get("/contests/weekly/scoreboard/table").text
    assert "<td>admin</td>" not in table
    assert "No contestant has submitted yet." in table


def test_a_new_contest_submission_refreshes_live_scoreboards(
    client: TestClient, db: Session, redis: Redis
) -> None:
    contest = add_contest(db, starts_in=STARTED)
    add_contest_problem(db, contest, "A", "secret-sum")
    register(client, "alice", PASSWORD)
    client.get("/contests/weekly/scoreboard")
    assert redis.exists(scoreboard_key(contest.id))
    events = redis.pubsub(ignore_subscribe_messages=True)
    events.subscribe(contest_channel(contest.id))

    submit_to_contest(client)

    message = next_event(events)
    assert message is not None
    assert json.loads(message["data"]) == {"contest_id": contest.id}
    assert not redis.exists(scoreboard_key(contest.id))
    events.close()


def test_a_submission_after_the_contest_has_ended_is_practice(
    client: TestClient, db: Session
) -> None:
    contest = add_contest(db, starts_in=-timedelta(hours=3))
    add_contest_problem(db, contest, "A", "secret-sum")
    register(client, "alice", PASSWORD)
    assert "submissions now are practice" in client.get("/contests/weekly").text

    assert submit_to_contest(client).status_code == 303

    assert contest_of_only_submission(db) is None


def test_a_submission_from_the_problems_own_page_during_the_contest_is_practice(
    client: TestClient, db: Session
) -> None:
    contest = add_contest(db, starts_in=STARTED)
    add_contest_problem(db, contest, "A", "secret-sum")
    register(client, "alice", PASSWORD)
    page = client.get("/problems/secret-sum").text
    assert 'part of <a href="/contests/weekly">Weekly</a>, which is running now' in page

    assert submit(client, "/problems/secret-sum/submit").status_code == 303

    assert contest_of_only_submission(db) is None


def test_an_admins_submission_before_the_start_is_practice(client: TestClient, db: Session) -> None:
    contest = add_contest(db, starts_in=timedelta(minutes=30))
    add_contest_problem(db, contest, "A", "secret-sum")
    log_in_as_admin(client, db)
    assert (
        "hasn't started, so submissions now are practice"
        in client.get("/contests/weekly/problems/A").text
    )

    assert submit_to_contest(client).status_code == 303

    assert contest_of_only_submission(db) is None


def test_a_refused_contest_submission_shows_the_contest_problem_again(
    client: TestClient, db: Session
) -> None:
    contest = add_contest(db, starts_in=STARTED)
    add_contest_problem(db, contest, "A", "secret-sum")
    register(client, "alice", PASSWORD)

    response = submit_to_contest(client, source="   ")

    assert response.status_code == 400
    assert "Paste your solution or choose a file." in response.text
    assert "<h1>A. Secret Sum</h1>" in response.text
    assert 'action="/contests/weekly/submit"' in response.text
    assert '<input type="hidden" name="label" value="A">' in response.text
    assert db.scalars(select(Submission)).first() is None


def test_an_unknown_contest_or_label_is_not_found(client: TestClient, db: Session) -> None:
    contest = add_contest(db, starts_in=STARTED)
    add_contest_problem(db, contest, "A", "secret-sum")
    register(client, "alice", PASSWORD)
    assert client.get("/contests/weekly/problems/B").status_code == 404
    assert submit_to_contest(client, label="B").status_code == 404
    assert client.get("/contests/monthly").status_code == 404
    assert client.get("/contests/monthly/scoreboard").status_code == 404


def test_an_anonymous_visitor_is_sent_to_log_in_before_submitting(
    client: TestClient, db: Session
) -> None:
    contest = add_contest(db, starts_in=STARTED)
    add_contest_problem(db, contest, "A", "secret-sum")
    assert "to submit a solution" in client.get("/contests/weekly").text
    csrf_token = csrf_token_from(client.get("/register"))
    response = client.post(
        "/contests/weekly/submit",
        data={"csrf_token": csrf_token, "label": "A", "language": "python", "source": SOLUTION},
        follow_redirects=False,
    )
    assert (response.status_code, response.headers["location"]) == (303, "/login")


def test_the_contest_list_groups_running_upcoming_and_past_contests(
    client: TestClient, db: Session
) -> None:
    add_contest(db, slug="today", starts_in=STARTED)
    add_contest(db, slug="tomorrow", starts_in=timedelta(days=1))
    add_contest(db, slug="yesterday", starts_in=-timedelta(days=1))
    page = client.get("/contests").text
    order = [
        "Running now",
        'href="/contests/today"',
        "Upcoming",
        'href="/contests/tomorrow"',
        "Past",
        'href="/contests/yesterday"',
    ]
    positions = [page.index(text) for text in order]
    assert positions == sorted(positions)


def test_the_contest_page_counts_down_to_the_start_and_then_to_the_end(
    client: TestClient, db: Session
) -> None:
    contest = add_contest(db, starts_in=timedelta(minutes=10))
    upcoming = re.search(
        r'data-countdown-seconds="(\d+)" data-reload-at-zero>Starts in',
        client.get("/contests/weekly").text,
    )
    assert upcoming is not None
    # The database's clock decides; allow for it to differ a little from this machine's.
    assert 570 <= int(upcoming.group(1)) <= 630

    # Moved to have started 10 minutes ago; it still ends 2 hours 10 minutes from now.
    contest.starts_at -= timedelta(minutes=20)
    db.commit()
    running = re.search(
        r'data-countdown-seconds="(\d+)" data-at-zero="The contest has ended.">Ends in',
        client.get("/contests/weekly").text,
    )
    assert running is not None
    assert 7770 <= int(running.group(1)) <= 7830


def test_the_scoreboard_ranks_contestants_from_their_verdicts(
    client: TestClient, db: Session
) -> None:
    contest = add_contest(db, starts_in=STARTED)
    first = add_contest_problem(db, contest, "A", "sum")
    second = add_contest_problem(db, contest, "B", "paths")
    alice, bob, carol = (create_user(db, name, PASSWORD) for name in ("alice", "bob", "carol"))
    add_contest_submission(db, contest, alice, first, minute=1, verdict=Verdict.WRONG_ANSWER)
    add_contest_submission(db, contest, alice, first, minute=5, verdict=Verdict.ACCEPTED)
    add_contest_submission(db, contest, bob, first, minute=3, verdict=Verdict.ACCEPTED)
    add_contest_submission(db, contest, bob, second, minute=4, verdict=None)
    # A practice submission, made outside the contest's pages, doesn't count.
    add_contest_submission(
        db, contest, carol, first, minute=2, verdict=Verdict.ACCEPTED, practice=True
    )

    page = client.get("/contests/weekly/scoreboard").text

    rows = re.findall(r"<td>(\d+)</td>\s*<td>(\w+)</td>\s*<td>(\d+)</td>\s*<td>(\d+)</td>", page)
    assert rows == [("1", "bob", "1", "3"), ("2", "alice", "1", "25")]
    assert '<td class="cell cell-accepted">+1<small>5</small></td>' in page
    assert 'data-live-url="/live/contests/' in page
