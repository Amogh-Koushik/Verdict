import re

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.integration.forms import log_in_as_admin
from tests.integration.submissions import add_submissions
from verdict.core.config import Settings
from verdict.web.admin_stats import judge_stats

# Times are set from the database's clock, which can drift from this machine's.
SET_JUDGED = text(
    """
    UPDATE submissions
    SET status = :status, verdict = :verdict,
        judged_at = now() - make_interval(secs => :seconds_ago),
        started_at = now() - make_interval(secs => :seconds_ago + :judge_ms / 1000.0),
        lease_owner = :worker,
        lease_expires_at = now() - make_interval(secs => :seconds_ago) + make_interval(secs => 60)
    WHERE id = :submission_id
    """
)
SET_JUDGING = text(
    """
    UPDATE submissions
    SET status = 'judging', attempt = 1, lease_owner = :worker, started_at = now(),
        lease_expires_at = now() + make_interval(secs => 60)
    WHERE id = :submission_id
    """
)
# The check written out by hand, the way an admin would run it in psql.
RAW_ROWS = text(
    """
    SELECT status, verdict,
           judged_at >= now() - interval '1 hour' AS in_last_hour,
           extract(epoch FROM judged_at - started_at) * 1000 AS judge_ms,
           lease_expires_at - interval '60 seconds' >= now() - interval '1 minute' AS lease_fresh,
           lease_owner
    FROM submissions
    """
)


def seed_activity(db: Session) -> None:
    ids = iter(add_submissions(db, 8))
    judged = [
        # (status, verdict, seconds ago, judge ms, worker)
        ("done", "AC", 600, 100, "worker-old"),
        ("done", "AC", 1200, 200, "worker-old"),
        ("done", "AC", 1800, 300, "worker-old"),
        ("done", "WA", 2400, 400, "worker-old"),
        ("done", "TLE", 40, 1000, "worker-b"),
        ("done", "AC", 7200, 5000, "worker-old"),
        ("failed", "SE", 300, 0, "worker-old"),
    ]
    for status, verdict, seconds_ago, judge_ms, worker in judged:
        db.execute(
            SET_JUDGED,
            {
                "submission_id": next(ids),
                "status": status,
                "verdict": verdict,
                "seconds_ago": seconds_ago,
                "judge_ms": judge_ms,
                "worker": worker,
            },
        )
    db.execute(SET_JUDGING, {"submission_id": next(ids), "worker": "worker-a"})
    db.commit()
    # Two more submissions are still waiting.
    add_submissions(db, 2)


def linear_percentile(values: list[float], fraction: float) -> float:
    """Linear interpolation between closest ranks, which is what percentile_cont does."""
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def shown(page: str, stat: str) -> str:
    match = re.search(rf'data-stat="{stat}">([^<]+)<', page)
    assert match, f"the page shows no {stat}"
    return match.group(1)


def test_the_numbers_match_the_ones_worked_out_by_hand(db: Session, settings: Settings) -> None:
    seed_activity(db)
    stats = judge_stats(db, settings.lease_seconds)
    assert (stats.queued, stats.judging) == (2, 1)
    assert {verdict.value: count for verdict, count in stats.recent_verdicts if count} == {
        "AC": 3,
        "WA": 1,
        "TLE": 1,
        "SE": 1,
    }
    assert stats.judged == 5
    # Judge times in the last hour: 100, 200, 300, 400 and 1000 ms.
    assert stats.median_judge_ms is not None
    assert round(stats.median_judge_ms) == 300
    assert stats.p95_judge_ms is not None
    assert round(stats.p95_judge_ms) == 880
    assert stats.active_workers == ["worker-a", "worker-b"]


def test_the_numbers_match_a_manual_sql_check(db: Session, settings: Settings) -> None:
    seed_activity(db)
    rows = db.execute(RAW_ROWS).all()
    recent_done = [row for row in rows if row.status == "done" and row.in_last_hour]
    expected_verdicts: dict[str, int] = {}
    for row in rows:
        if row.verdict is not None and row.in_last_hour:
            expected_verdicts[row.verdict] = expected_verdicts.get(row.verdict, 0) + 1
    judge_times = [float(row.judge_ms) for row in recent_done]

    stats = judge_stats(db, settings.lease_seconds)

    assert stats.queued == sum(row.status == "queued" for row in rows)
    assert stats.judging == sum(row.status == "judging" for row in rows)
    assert {verdict.value: count for verdict, count in stats.recent_verdicts if count} == (
        expected_verdicts
    )
    assert stats.judged == len(recent_done)
    assert stats.median_judge_ms is not None
    assert stats.p95_judge_ms is not None
    assert abs(stats.median_judge_ms - linear_percentile(judge_times, 0.5)) < 0.01
    assert abs(stats.p95_judge_ms - linear_percentile(judge_times, 0.95)) < 0.01
    assert stats.active_workers == sorted({row.lease_owner for row in rows if row.lease_fresh})


def test_the_stats_page_shows_them(client: TestClient, db: Session) -> None:
    seed_activity(db)
    log_in_as_admin(client, db)
    page = client.get("/admin/stats").text
    assert (shown(page, "queued"), shown(page, "judging")) == ("2", "1")
    assert [shown(page, f"verdict-{verdict}") for verdict in ("AC", "WA", "TLE", "MLE", "SE")] == [
        "3",
        "1",
        "1",
        "0",
        "1",
    ]
    assert shown(page, "judged") == "5"
    assert (shown(page, "median-ms"), shown(page, "p95-ms")) == ("300 ms", "880 ms")
    assert "<code>worker-a</code>" in page
    assert "<code>worker-b</code>" in page
    assert "worker-old" not in page


def test_an_idle_judge_has_empty_stats(client: TestClient, db: Session) -> None:
    log_in_as_admin(client, db)
    page = client.get("/admin/stats").text
    assert (shown(page, "queued"), shown(page, "judging")) == ("0", "0")
    assert "Nothing was judged in the last hour." in page
    assert "No worker has held a lease in the last minute." in page
