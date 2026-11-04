# Verdict design

Verdict is an online judge for college contests. Contestants submit C++ or Python; workers judge each submission in a fresh, locked-down container; pages and the ICPC scoreboard update live.

## Architecture

```
 Browser ── HTTPS ──> Caddy ──> web (FastAPI, 2 Uvicorn workers, stateless)
                                  │  reads/writes          │ subscribe / publish
                                  v                        v
                             PostgreSQL <──────────── Redis (sessions, rate limits,
                    (source of truth + job queue)      scoreboard cache, pub/sub)
                                  ^                        ^
                       claim / heartbeat / finish          │ publish verdict events
                                  │                        │
                       worker x N (judge processes, scaled with --scale)
                                  │ Docker SDK (host Docker socket)
                                  v
                  one sandbox container per submission
                  (no network, non-root, CPU / memory / pids limits)
```

- **web** serves the pages, the forms and the live-update streams. It never runs submitted code.
- **worker** claims one submission at a time from Postgres, judges it in a new sandbox container, records the verdict and publishes an event to Redis. Throughput grows with the number of workers.
- **Postgres** holds everything that matters and is also the queue. **Redis** holds what can be lost: sessions, rate-limit counters, the scoreboard cache, and "something changed" events.
- **Caddy** terminates HTTPS, limits request sizes, and passes live-update streams through unbuffered.

## Data model

| Table | Holds |
|---|---|
| `users` | username (unique ignoring case), Argon2id password hash, admin flag |
| `problems` | slug, title, Markdown statement, time and memory limits, current `tests_version`, public flag |
| `problem_tests` | SHA-256 of each test's input and output, per problem and version; files live at `/data/problems/{id}/v{version}/{i}.in` and `.out` |
| `contests` | slug, title, `starts_at` < `ends_at` |
| `contest_problems` | a problem in a contest under a label A to Z |
| `submissions` | user, problem, contest (null for practice), language, source; `status` (queued, judging, done, failed), verdict, failed test, time, memory, compiler output; the tests version judged against; `priority` (new or rejudged); `attempt`, `lease_owner`, `lease_expires_at` |

Timestamps are `timestamptz` in UTC and shown in IST. A problem is visible to non-admins when it is public or belongs to a contest that has started. Only the author and admins see a submission's source.

## Submitting

1. The form posts to a problem page or to a contest page; both run the same checks: language, at most 64 KB of UTF-8 source, the problem has tests, fewer than `QUEUE_LIMIT` new submissions are waiting (otherwise 503 with `Retry-After`; rejudged ones don't count, so a rejudge never locks contestants out), and the user's limits of 1 per 10 seconds and 60 per hour (otherwise 429).
2. The row is inserted and `NOTIFY submission_queued` is sent in the same transaction, so workers wake as soon as it commits.
3. A submission counts for a contest only when it is made from the contest's pages inside the window `[starts_at, ends_at)`. The database's clock decides, in the transaction that sets `created_at`, so the decision and the stored time always agree.

## Judging

1. **Claim.** One `UPDATE ... WHERE id = (SELECT ... ORDER BY priority DESC, id FOR UPDATE SKIP LOCKED LIMIT 1)` takes the next queued submission, or one whose lease expired: new submissions before rejudged ones, the oldest first within each. A partial index on `(priority DESC, id)` over pending rows holds them in exactly that order, so the claim reads one index entry however long the queue is. It sets `status = judging`, adds 1 to `attempt`, records the worker as `lease_owner`, sets a 60-second lease, and records the problem's current tests version. A row claimed for the fourth time is marked `failed` with `SE` instead, so one bad submission can't crash workers forever.
2. **Heartbeat.** While judging, a thread extends the lease every 15 seconds. If the update matches no row, the lease was lost: the worker kills its sandbox and stops.
3. **Sandbox.** One container per submission, from `verdict-runner-cpp:1` (gcc 14) or `verdict-runner-python:1` (Python 3.12):
   - no network, user `runner` (uid 1000), all capabilities dropped, `no-new-privileges`, read-only root filesystem, no IPC namespace sharing, no message queues;
   - 1 CPU, 64 processes, memory = the limit + 64 MB with swap off;
   - writable only in `/box` and a 16 MB `/tmp`;
   - files go in and out with `put_archive`, never bind mounts, because the worker itself runs in a container.
4. **Run.** C++ is compiled with `g++ -std=c++17 -O2` under a 10-second timeout; a failure is `CE`. Each test runs in order under GNU `time` and `timeout -s KILL` (wall limit = 2 x time limit + 1 s) with output capped at 32 MB. A pure function classifies each run: CPU time at or over the limit, or killed at the wall limit, is `TLE`; peak memory at or over the limit, or killed by the memory cap, is `MLE`; any other non-zero exit is `RE`. Otherwise the output is compared token by token. Judging stops at the first failed test. A watchdog kills the container if the whole submission overruns its deadline, so a worker never hangs.
5. **Finish.** The verdict is written with `WHERE id AND attempt AND lease_owner AND status = 'judging'`. If no row matches, another worker took over and this result is discarded. The worker then publishes to `submission:{id}` and, for contest submissions, deletes the cached scoreboard and publishes to `contest:{id}`.
6. **Shutdown.** On SIGTERM a worker finishes its current submission, or after 10 seconds hands it back to the queue, then exits.
7. **Rejudge.** An admin can requeue every submission of a problem after changing its tests. The rows go back to `queued` with `attempt` reset to 0, the results cleared, and the lower priority, so they fill the judges' idle time and never delay a new submission. A worker still judging the old run is fenced out by `status` and `lease_owner`.

## Live updates

- `GET /live/submissions/{id}`, `/live/submissions?ids=...` and `/live/contests/{id}` are Server-Sent Event streams. Each subscribes to its Redis channels and waits for Redis to confirm before responding, sends a comment every 15 seconds, and ends cleanly when the server shuts down.
- Events only say that something changed. The page then re-fetches an HTML fragment with htmx, and it re-fetches everything still pending whenever the stream reconnects, so a missed event costs nothing. Postgres stays the source of truth.

## Scoreboard

- A pure function ranks contestants by problems solved, then by penalty: minutes from the start to each first accepted solution, plus 20 for every earlier rejected attempt. `CE` and `SE` cost nothing, attempts after the first `AC` are ignored, and an attempt still being judged shows `?`. Ties share a rank. Administrators can submit to try a contest's problems, but aren't ranked.
- The result is cached in Redis per contest. Every change deletes the entry and bumps a generation counter in one transaction. A reader stores its result tagged with the generation it read before querying Postgres, so a scoreboard computed just before a change is never served after it.

## Security

- Passwords are hashed with Argon2id. Sessions are random 32-byte tokens in Redis, sent in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` in production) and replaced at login. Every form carries a CSRF token tied to the session. Logins are limited to 5 a minute per address and username; behind Caddy, the web app believes a forwarded client address only from Caddy's fixed address on a network the two share alone.
- Statements are rendered from Markdown with raw HTML disabled, and every template escapes its output.
- **Known risk:** the worker has the host's Docker socket, which is equivalent to root on the host. Anyone who takes over a worker process controls the machine. The sandbox settings above stand between submitted code and the worker, but a production judge should run its workers on a dedicated machine and use a stronger container runtime such as gVisor.
