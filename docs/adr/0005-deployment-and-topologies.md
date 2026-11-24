# ADR-0005: Deployment and topologies

- **Status:** Proposed
- **Date:** 2026-11-23
- **Related:** ADR-0001, ADR-0002, ADR-0003, ADR-0004, ADR-0006, D-015, D-021

## Context

- **How it runs today:** `uv sync`, then `uv run python -m venture_intelligence.monitor`
  (P-104), or Uvicorn alone (P-105). pnpm builds `frontend/dist`, which FastAPI serves at
  `/` (P-001).
- **No containers.** CI runs the git guards and the offline suite only (P-115).
- **The whole `frontend/` directory is served at `/frontend`,** sources included
  (FINDINGS §10.3, P-003).
- **Development machine:** Windows 11 with Docker Desktop (Docker 29.9.2, Compose v5.6.1;
  STATUS preflight). CI is GitHub Actions on Ubuntu.
- **The owner requires:**
  - one documented command that brings up the full stack with a demo dataset, with no
    paid or hosted dependency;
  - containers;
  - a hosted mode with TLS, authentication and isolation;
  - the same tests for both topologies;
  - backups, restore and documented sizing.

## Decision

1. **One multi-stage `Dockerfile`.**
   - **`web` stage:** Node 22 and pnpm build `frontend/dist`.
   - **`app` stage:** `python:3.12-slim` on Debian 13 (verify the tag). Dependencies come
     from `uv.lock`, the process runs as a non-root user, and only `dist/` is copied from
     the frontend.
   - **`app-browser` stage:** adds Playwright Chromium and Node for Crawlee (P-111). It is
     used only when rendering is enabled, because it adds roughly 1 GB (verify).
   - Images may be published to GHCR for convenience, but a local build must always work.
2. **`compose.yaml` and its profiles.** Core services:
   - `db`: `postgres:18` with a named volume and a healthcheck; not published to the host;
     the erasure ledger (ADR-0002) is on a separate named volume, `erasure-ledger`,
     mounted only into the one-shot services that run owner-role `vi admin` tasks
     (erase-subject, erasure-retry, backup and restore), never into `api` or `worker`;
   - `migrate`: one-shot `vi db upgrade`;
   - `api`: serves the API and the UI on `127.0.0.1:8000`;
   - `worker`: runs `vi worker` (ADR-0003).

   Profiles add services:

   | Profile | Adds |
   |---|---|
   | (default) | the core services |
   | `demo` | `seed-demo` (idempotent `vi demo load`), Mailpit (MIT) at `localhost:8025`, deterministic model provider |
   | `ollama` | `ollama/ollama` (MIT) container, for Linux and NVIDIA hosts |
   | `browser` | worker built from `app-browser` |
   | `hosted` | Caddy (Apache-2.0; 2.10+, verify) on 443, `VI_MODE=hosted`, secrets from files; `api` is no longer published |
   | `observability` | Prometheus, Grafana, Jaeger (ADR-0004) |
   | `dev` | publishes `db` on `127.0.0.1:55432` for native development |

3. **The one command** is `docker compose --profile demo up --build`; then open
   `http://localhost:8000`. Demo digests arrive in Mailpit, so the email-first loop is
   visible without SMTP credentials, a model, or an API key. The README quick start
   documents it, with the expected first-run time.
4. **Simulated hosted mode** is `docker compose --profile hosted --profile demo up`. Caddy
   uses `tls internal` for `vi.localhost`; trust its local CA with `caddy trust`, or
   pass `--cacert` in CI. A real deployment replaces the site address with a domain and
   gets ACME certificates automatically (Let's Encrypt and ZeroSSL are free). A
   self-provided certificate also works.
5. **Two topologies, one test suite.** In GitHub Actions:
   - the `offline` job runs pytest against a `services: postgres` container;
   - the `stack` job, with matrix `topology: [local, hosted]`, runs `docker compose ...
     up --wait` and then the black-box suite `tests/system/`.

   The black-box suite calls the API, the remote CLI and MCP against
   `http://127.0.0.1:8000` or `https://vi.localhost`. Only the auth fixture differs
   between topologies.
6. **Windows and Docker Desktop (D-015).**
   - Postgres data lives only in named volumes: NTFS bind mounts are slow and break
     Postgres permissions.
   - `.gitattributes` forces LF line endings for entrypoint scripts.
   - The database is not published, so it cannot conflict with a native Postgres on
     5432.
   - Document a `.wslconfig` memory limit of at least 6 GB when Ollama runs.
   - Hot-reload development runs natively (`uv`, `pnpm dev`) against the compose
     database.
   - On Windows, Ollama runs natively on the host by default
     (`OLLAMA_BASE_URL=http://host.docker.internal:11434`), which gives it GPU access
     without WSL passthrough.
   - Compose files must also work with Podman (Apache-2.0) and Rancher Desktop, because
     Docker Desktop requires a paid subscription in larger organisations (verify current
     terms).
7. **Backups and restore.**
   - `vi admin backup` runs `pg_dump -Fc` through a one-shot service into
     `./backups/vi-<timestamp>.dump`, copies the erasure ledger beside it as
     `vi-<timestamp>.erasure.jsonl`, and keeps the last N pairs. The ledger is never
     inside the dump.
   - `vi admin restore FILE` stops the `api` and `worker` services, restores into a
     fresh database (`pg_restore --clean --if-exists --no-owner`), runs `vi db upgrade`,
     then replays the erasure ledger: the live ledger, or the newest backed-up copy if
     the live one is lost. Replay runs under the owner role, takes the per-request-id
     advisory lock that the original erasure takes before its intent append, skips an
     intent that already has an `erasure_log` row, and calls `vi_replay_erasure` for every intent in the ledger, marked
     or not, re-erasing its row ids and re-adding its subject key hash to
     `subject_suppressions`, the same hash the identity gate (P-227) checks. Subjects
     erased after the backup therefore stay erased (ADR-0002). The `api` and `worker`
     restart only after replay finishes, and readiness fails until then. If any record
     fails to replay, restore exits non-zero, the services stay stopped and readiness
     keeps failing; it never finishes with erased data restored.
   - Hosted guidance: nightly encrypted dumps (restic, BSD-2, or age, BSD-3) with an
     off-site copy; pgBackRest (MIT) for point-in-time recovery if needed.
8. **Sizing.** These are starting points; the S6 load test verifies them.

| Topology | CPU | RAM | Disk | Notes |
|---|---|---|---|---|
| Local, deterministic model | 2 vCPU | 4 GB | 10 GB | the operator's install holds about 7k rankings |
| Local + Ollama default model (`granite3.3:2b`, D-021) | 4 vCPU | 8–12 GB | 15 GB | model about 2 GB; GPU optional |
| Hosted, up to 25 users and 100 mandates | 4 vCPU | 8 GB | 50 GB SSD | 2 API workers; 1 worker at concurrency 4; `shared_buffers` 2 GB |

## Alternatives considered

- **Kubernetes and Helm first:** standard at scale, but far too much for a solo
  self-hoster. A chart can come later from the same images.
- **Traefik (MIT) or nginx (BSD-2) instead of Caddy:** Traefik's label-driven routing
  suits dynamic fleets. nginx is the most familiar, but needs certbot for automatic TLS.
  Caddy gives automatic HTTPS and a local CA from a ten-line Caddyfile.
- **Native install as the primary path** (`uv tool install` plus a system Postgres): lighter
  and needs no container runtime, but installing Postgres on Windows is a manual step.
  The owner chose Docker or Podman compose as the only supported install (resolved,
  D-021). Users without Docker get documented manual Postgres steps, not a supported
  native path.
- **Dev containers or Nix:** reproducible, but niche for this audience. Not adopted.

## Consequences

- **A container runtime is needed** for the one-command path. The `app` image stays small;
  the browser image is opt-in.
- **Migration steps:**
  1. Write the Dockerfile and compose profiles.
  2. Build the demo dataset and `vi demo load` (an S0 item in STATUS).
  3. Add the CI `stack` job.
  4. Add backup and restore commands.
  5. Rewrite the README quick start.
- **Parity risks:**
  - `/frontend/*` (P-003) serves only `dist/` in images. The removal of the full-directory
    mount is decided together with the S6 hardening item from FINDINGS §10.3.
  - `API_HOST` and `API_PORT` (P-349, P-350) still apply: the container binds `0.0.0.0`,
    and the host publishes only `127.0.0.1`.
  - The native commands P-104 and P-105 keep working.

## How parity is verified

- The CI `stack` job passes in both topologies. Its smoke checks cover P-001, P-002,
  P-004, P-007, P-063 and P-097.
- `vi demo load` is idempotent: run twice, it gives the same row counts and digests.
- Backup, restore and digest equality are checked in CI.
- A CI test erases a subject after a backup, restores that backup, and checks the
  subject stays erased and suppressed; with a corrupt ledger line, restore exits
  non-zero.
- `docker compose config` validates for every profile.
- The owner runs the one-command path on Windows; the result is recorded in STATUS.
