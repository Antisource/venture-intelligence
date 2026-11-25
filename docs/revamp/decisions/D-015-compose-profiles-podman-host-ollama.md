# D-015: Compose profiles with Caddy, compatible with Podman; Ollama on the host on Windows

- **Date:** 2026-11-23
- **Area:** Deployment
- **Differs from owner instruction:** No (adds the runtime and Windows choices)

1. **Chosen:** one `compose.yaml` with these profiles:
   - default: `db`, `migrate`, `api`, `worker`;
   - `demo`: seed data plus Mailpit;
   - `hosted`: Caddy with TLS;
   - `ollama`, `browser`, `observability` and `dev`.

   The one command is `docker compose --profile demo up --build`. The compose files must
   also run under Podman and Rancher Desktop. On Windows, Ollama runs natively on the host
   (`host.docker.internal:11434`) by default, and Postgres data lives only in named
   volumes. See ADR-0005.
2. **Owner instruction:** containerized deployment; one documented command brings up the
   full stack with demo data and no paid or hosted dependency; the dev machine is
   Windows 11 with Docker Desktop.
3. **Why:** Docker Desktop requires a paid subscription in larger organisations (verify
   current terms), so it cannot be the only supported runtime under the
   no-paid-dependency rule. Podman (Apache-2.0) and the Docker Engine (Apache-2.0) are
   free. Native Ollama on Windows gets the GPU without WSL passthrough. NTFS bind mounts
   are slow and break Postgres permissions. Mailpit shows the email-first loop without
   SMTP credentials.
   **Trade-off:** CI must lint the compose files for Podman compatibility, and the
   Windows defaults differ from the Linux `ollama` profile.
4. **Parity:** the native commands (P-104, P-105) keep working, and so do `API_HOST` and
   `API_PORT` (P-349, P-350). Images serve only `frontend/dist`; the P-003 full-directory
   mount is decided with the S6 hardening item from FINDINGS §10.3.
