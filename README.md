# Verdict

Verdict is a self-hosted online judge for college coding contests. Users submit C++ or Python
solutions, isolated judge workers compile and run them against hidden tests inside locked-down
containers, and results and a live ICPC-style scoreboard update in the browser in real time.

- Problems with Markdown statements, per-problem time and memory limits, and versioned test
  archives.
- Contests with a countdown, problems hidden until the start, and a live ICPC scoreboard.
- Verdicts AC, WA, TLE, MLE, RE, CE and SE, shown on the page the moment they are decided.
- Admin tools: problem and contest editing, test uploads, rejudging a problem, and a stats page.
  Administrators can submit to a contest to try it, but aren't ranked on its scoreboard.
- Five sample problems with tests and reference solutions, and a sample contest, added by one
  command.

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

Postgres is both the source of truth and the job queue: workers claim submissions with
`FOR UPDATE SKIP LOCKED` and a lease, and every write after a claim is fenced on the lease, so a
stalled worker can never overwrite a newer result. New submissions are judged before rejudged
ones, oldest first within each, so rejudging a problem during a contest never makes contestants
wait. Redis carries "something changed" events from
the workers to every web process, which relays them to browsers over Server-Sent Events.
[docs/design.md](docs/design.md) describes the data model and the request and judge flows.

## Running it locally

You need Docker with Compose 2.24 or later, [uv](https://docs.astral.sh/uv/), and `make`.

```bash
make up
docker compose exec web python -m verdict.cli create-admin admin
make seed
```

`make up` builds the two runner images (`verdict-runner-cpp:1`, `verdict-runner-python:1`) and
starts Postgres, Redis, the migrations, the web app and one worker. Open http://localhost:8000 and
log in as the administrator you created. `make seed` adds the sample problems and a contest that
starts ten minutes later.

On Linux, the worker needs the group that owns the Docker socket: copy `.env.example` to `.env` and
set `DOCKER_GID` to the output of `getent group docker | cut -d: -f3`. Every other setting has a
working default, listed in `.env.example`.

More workers judge more submissions at once:

```bash
docker compose up -d --scale worker=4
```

To judge a file against a problem's tests without the queue, for example a reference solution:

```bash
docker compose exec worker python -m verdict.cli try-run /app/src/verdict/samples/solutions/longest_increasing_subsequence.cpp longest-increasing-subsequence
```

## Running the tests

```bash
uv sync
make lint
make test
make test-sandbox
```

The tests use the Postgres and Redis started by `make up`, in a separate database (`verdict_test`)
and Redis database (15). `make test` runs the unit and integration tests. `make test-sandbox` judges
programs in real containers and needs the runner images: every verdict in both languages, hostile
programs (a fork bomb, network access, a huge file, reading `/etc/shadow`, writing outside `/box`),
the reference solutions, and the worker's shutdown, lease and rejudge behaviour.

## Load test

`make load-test` measures the judge on the running stack. It queues a burst of submissions to the
sample problem "Sum of two numbers" (10 tests) straight into the database, then waits until every
one has a verdict. Half the submissions are accepted solutions and the rest give WA, RE, CE, TLE
and MLE, in C++ and Python. It runs once each with 1, 2 and 4 workers, and reports throughput and
the time from submitting to the verdict.

Measured with 100 submissions per round on an Intel Core Ultra 9 185H (16 cores, 22 threads),
Windows 11 with Docker Desktop 29.4.3 (a WSL2 virtual machine with 22 CPUs and 15.4 GiB of memory):

| Workers | Submissions | Wall time | Throughput | Median submit to verdict | p95 submit to verdict | Wrong verdicts |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 100 | 276.7 s | 21.7 per minute | 145.8 s | 262.8 s | 0 |
| 2 | 100 | 136.8 s | 43.9 per minute | 72.9 s | 129.8 s | 0 |
| 4 | 100 | 76.2 s | 78.8 per minute | 39.7 s | 70.1 s | 0 |

Throughput grows almost in step with the number of workers: 2.0 times with two, 3.6 times with
four. All 100 submissions arrive at the same moment, so the median waits for about half the queue
and the p95 for nearly all of it; the times above are queueing as much as judging.

A submission takes about three quarters of a second to judge, plus about a quarter of a second for
each test that runs. That is mostly Docker's own work, starting each test's process in the container
and copying its output out, since the programs themselves finish in about 10 ms. A Python solution
passing all 10 tests took 3.4 seconds on average, and one failing the first test 1.0 second. A time
limit exceeded costs the full wall-clock limit (twice the time limit plus a second), so a Python one
took 6.0 seconds.

On a small cloud server running the production stack, an Azure Standard_B2als_v2 (2 vCPUs, 4 GiB of
memory, Ubuntu 24.04):

| Workers | Submissions | Wall time | Throughput | Median submit to verdict | p95 submit to verdict | Wrong verdicts |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 100 | 205.1 s | 29.3 per minute | 104.4 s | 191.4 s | 0 |
| 2 | 100 | 139.5 s | 43.0 per minute | 72.4 s | 131.7 s | 0 |
| 4 | 100 | 117.9 s | 50.9 per minute | 62.9 s | 111.7 s | 0 |

One worker is faster here than on the machine above, most likely because Docker runs directly on
Linux, with no virtual machine in between. With only two vCPUs to share, more workers help less: 1.5
times with two and 1.7 times with four, still with no wrong verdicts. Two workers suit a server this
size. Treat these as best-case figures: a problem with more than this one's 10 tests takes longer per
submission, and this server type is burstable, so it can slow down under long sustained load.

To measure a production stack, whose database has no published port, run the script on the server
itself: Linux can reach a container's address directly. It needs `uv` and the seeded sample problems,
replaces the workers while it runs, and leaves one worker and its submissions behind, so don't run it
during a contest.

```bash
set -a; . ./.env; set +a
export COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml
export DATABASE_URL="postgresql+psycopg://verdict:${POSTGRES_PASSWORD}@$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(docker compose ps -q db)"):5432/verdict"
uv run python scripts/load_test.py
```

## Deploying

`docker-compose.prod.yml` adds Caddy, which gets and renews an HTTPS certificate on its own, and
keeps Postgres, Redis and the web app off the host's ports.

1. Point a DNS name at the server and open ports 80 and 443.
2. Copy `.env.example` to `.env` and set `VERDICT_DOMAIN` to that name, `POSTGRES_PASSWORD` to a
   long random value (for example `openssl rand -hex 24`), and `DOCKER_GID` as above. Compose
   refuses to start the production stack until `POSTGRES_PASSWORD` and `VERDICT_DOMAIN` are set.
3. Build the runner images and start everything:

   ```bash
   make runners
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
   docker compose -f docker-compose.yml -f docker-compose.prod.yml exec web python -m verdict.cli create-admin admin
   ```

In production the session cookie is `Secure`, and every service restarts with Docker except the
one-off migration job. Caddy passes live-update streams through without buffering, limits request
bodies (60 MB for test archives, 1 MB for everything else), and forwards the client's address,
which the login rate limit uses. Caddy and the web app share a network of their own,
`10.89.250.0/28`, where Caddy has the fixed address `10.89.250.2`, and the web app believes
forwarded addresses from that address only. If the server already uses that range, change the
subnet, the range and both addresses together in `docker-compose.prod.yml`.

Every image the stack uses is published for x86-64 and ARM64, and the app and runner images build
and run on ARM64. The time limits and the load-test numbers above were measured on x86-64, so on
a new server, judge the reference solutions with `try-run` (each should take well under half its
time limit) and measure it with the load test, as described below.

## Security notes

- **The worker has the host's Docker socket, which is equivalent to root on the host.** Anyone who
  takes over a worker process controls the machine. Submitted code never runs in the worker: it
  runs in a sandbox container with no network, a non-root user, all capabilities dropped,
  `no-new-privileges`, a read-only root filesystem, 1 CPU, a memory cap with swap off, and a limit
  of 64 processes. Still, a production judge should run its workers on a dedicated machine and use
  a stronger container runtime such as gVisor.
- Passwords are hashed with Argon2id. Sessions are random tokens stored in Redis, sent in an
  `HttpOnly`, `SameSite=Lax` cookie and replaced at login. Every form carries a CSRF token, and
  logins are limited to 5 attempts a minute per address and username.
- Statements are Markdown with raw HTML disabled, and every template escapes its output.
- Contestants see only their own source code; hidden problems and contest problems before the start
  answer 404 rather than revealing that they exist.
- Redis has no password. It is reachable only over the Compose network, never from outside.
- Live-update streams are open to anyone for a started contest's scoreboard, and each holds a
  Redis connection while open. Verdict does not limit how many one address can open.
