from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from verdict.core.db import database_now
from verdict.core.verdicts import Verdict
from verdict.web.deps import DbDep, SettingsDep, ViewerDep, require_admin
from verdict.web.templating import render

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

# The filter matches the partial index on pending submissions, so this doesn't scan the table.
QUEUE_DEPTH = text(
    """
    SELECT count(*) FILTER (WHERE status = 'queued') AS queued,
           count(*) FILTER (WHERE status = 'judging') AS judging
    FROM submissions
    WHERE status IN ('queued', 'judging')
    """
)
RECENT_VERDICTS = text(
    """
    SELECT verdict, count(*) AS submissions
    FROM submissions
    WHERE verdict IS NOT NULL AND judged_at >= now() - interval '1 hour'
    GROUP BY verdict
    """
)
# Judge time runs from a worker's claim to the verdict: compiling plus every test that ran.
# Failed submissions are left out, since they are marked failed the moment they are claimed.
RECENT_JUDGE_TIMES = text(
    """
    SELECT count(*) AS judged,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY judge_ms) AS median_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY judge_ms) AS p95_ms
    FROM (
        SELECT (extract(epoch FROM judged_at - started_at) * 1000)::double precision AS judge_ms
        FROM submissions
        WHERE status = 'done' AND judged_at >= now() - interval '1 hour'
    ) AS recent
    """
)
# Each claim and heartbeat sets lease_expires_at to now() + the lease length, so subtracting
# the length gives the moment the lease was last updated.
ACTIVE_WORKERS = text(
    """
    SELECT DISTINCT lease_owner
    FROM submissions
    WHERE lease_owner IS NOT NULL
      AND lease_expires_at - make_interval(secs => :lease_seconds) >= now() - interval '1 minute'
    ORDER BY lease_owner
    """
)


@dataclass(frozen=True)
class JudgeStats:
    measured_at: datetime
    queued: int
    judging: int
    # Every verdict in a fixed order, with 0 for those not seen in the last hour.
    recent_verdicts: list[tuple[Verdict, int]]
    judged: int
    median_judge_ms: float | None
    p95_judge_ms: float | None
    active_workers: list[str]


@router.get("/stats")
def stats_page(
    request: Request, viewer: ViewerDep, db: DbDep, settings: SettingsDep
) -> HTMLResponse:
    stats = judge_stats(db, settings.lease_seconds)
    return render(request, viewer, "admin/stats.html", {"stats": stats})


def judge_stats(db: Session, lease_seconds: int) -> JudgeStats:
    queue = db.execute(QUEUE_DEPTH).one()
    verdict_counts = {Verdict(row.verdict): row.submissions for row in db.execute(RECENT_VERDICTS)}
    times = db.execute(RECENT_JUDGE_TIMES).one()
    workers = db.scalars(ACTIVE_WORKERS, {"lease_seconds": lease_seconds}).all()
    return JudgeStats(
        measured_at=database_now(db),
        queued=queue.queued,
        judging=queue.judging,
        recent_verdicts=[(verdict, verdict_counts.get(verdict, 0)) for verdict in Verdict],
        judged=times.judged,
        median_judge_ms=times.median_ms,
        p95_judge_ms=times.p95_ms,
        active_workers=list(workers),
    )
