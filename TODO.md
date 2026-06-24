# TODO / deferred work

Work intentionally postponed, with enough context to pick it up cleanly. Not a bug list —
these are deliberate "later" items.

## Scale & correctness — when load grows

Pisa runs single-process today (SQLite, in-memory Channels layer), which is fine for a class
or department. To run multiple workers:

- **Postgres** — make `DATABASES` configurable from `DATABASE_URL` (default to the current
  SQLite). Add `psycopg[binary]`.
- **Redis Channels layer** — make `CHANNEL_LAYERS` configurable from `REDIS_URL` (default to
  `InMemoryChannelLayer`). Add `channels-redis`. Group eviction in the Lean-cap consumer
  already uses the channel layer, so it works once this is Redis-backed.
- **Shared store for the per-user Lean cap** — `_LEAN_HOLDERS` in
  [apps/homework/consumers.py](apps/homework/consumers.py) is an in-process dict, so the
  "one live Lean process per user" cap becomes **per-worker** under multiple processes.
  Move it to the Django cache (Redis):
  - claim with `cache.add(key, channel, timeout)` (atomic set-if-absent → the "busy" check);
  - takeover overwrites + group-sends evict (as today);
  - release deletes only if we still hold it;
  - give holders a **TTL and refresh it (heartbeat) while the LSP session is open**, so a
    crashed worker's holder expires instead of leaking.
  - Re-run the `ApplicationCommunicator` cap test single-process (LocMemCache) and validate
    multi-worker against **real Redis** before trusting it.

Related: [scale-posture memory], README "Self-hosting → Notes".

## Lean performance — when mathlib lands

Cold `lean file.lean` startup is slow per run/submit. Not worth optimizing yet: problems
compile a bare file (no `lake` project, no mathlib), so there's no elaboration cache to reuse.
Once mathlib / a lake project is in play, consider lake cache reuse and/or a warm Lean
process pool.

## Smaller follow-ups (flagged during development)

- **Layer 2 sandbox (bubblewrap)** — ✅ now **on by default** (no network, read-only filesystem)
  and verified on the dev host; the compose files + CI grant the `seccomp:unconfined` profile it
  needs in Docker. Remaining hardening: replace `seccomp:unconfined` with a tight custom profile
  that only allows the unshare/clone syscalls bubblewrap needs, or move Lean execution into a
  separate locked-down runner container so the web container keeps full seccomp. Also smoke-test
  the bwrap path inside a real Docker deploy (host verification done; CI exercises it next).
- **Blacklist enforcement is on by default** — the submission pre-scan blocks the full
  construct blacklist (`sorry`, `axiom`, `IO`, `#eval`, …) on every problem. If a problem's
  intended solution uses one, the instructor must re-permit it via the problem's *Allowed
  constructs*. Worth auditing existing problems when there are more of them.
- **Base image CVEs** — `python:3.14-slim` was flagged with vulnerabilities; rebuild/bump the
  base periodically.
- **Test suite** — CI ([.github/workflows/tests.yml](.github/workflows/tests.yml)) already
  runs coverage in Docker; the suite itself is still to be written. Start with the
  role/permission matrix, `grade_lean_submission` + scoring policies, and the Lean-cap
  consumer (reuse the `ApplicationCommunicator` harness).

## Stretch goals

- **Course lineage tree** — a git-style visualization of course offerings (branches across
  terms/sections) built on `Course.renewed_from`. The course page already shows the immediate
  parent ("Renewed from") and direct children ("Renewed as"); this would render the full
  family tree so you can see every offering's timeline at a glance.
