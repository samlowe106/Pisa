# TODO / deferred work

Work intentionally postponed, with enough context to pick it up cleanly.

## Scale & correctness: when load grows

Pisa runs single-process today (SQLite, in-memory Channels layer), which is fine for a class or department. To run multiple workers:

- **Postgres**: make `DATABASES` configurable from `DATABASE_URL` (default to the current SQLite). Add `psycopg[binary]`.
- **Redis Channels layer**: make `CHANNEL_LAYERS` configurable from `REDIS_URL` (default to `InMemoryChannelLayer`). Add `channels-redis`. Group eviction in the Lean-cap consumer already uses the channel layer, so it works once this is Redis-backed.
- **Shared store for the per-user Lean cap**: `_LEAN_HOLDERS` in [apps/homework/consumers.py](apps/homework/consumers.py) is an in-process dict, so the "one live Lean process per user" cap becomes **per-worker** under multiple processes. Move it to the Django cache (Redis):
  - claim with `cache.add(key, channel, timeout)` (atomic set-if-absent, the "busy" check);
  - takeover overwrites + group-sends evict (as today);
  - release deletes only if we still hold it;
  - give holders a **TTL and refresh it (heartbeat) while the LSP session is open**, so a crashed worker's holder expires instead of leaking.
  - Re-run the `ApplicationCommunicator` cap test single-process (LocMemCache) and validate multi-worker against **real Redis** before trusting it.

Related: [scale-posture memory], the Notes under README "Self-hosting".

## Lean performance: when mathlib lands

Cold `lean file.lean` startup is slow per run/submit. Not worth optimizing yet: problems compile a bare file (no `lake` project, no mathlib), so there's no elaboration cache to reuse. Once mathlib / a lake project is in play, consider lake cache reuse and/or a warm Lean process pool.

## Smaller follow-ups (flagged during development)

- **Sandbox hardening**: the bubblewrap sandbox runs in Docker via `seccomp:unconfined`, `apparmor:unconfined`, and `systempaths=unconfined`. Replace those with tight custom seccomp/AppArmor profiles that only allow the unshare/clone/mount operations bubblewrap needs, or move Lean execution into a separate locked-down runner container so the web container keeps full confinement.
- **Non-root container user**: the runtime image runs as root (mitigated by the bubblewrap sandbox around all untrusted code). Adopting a `USER app` pattern needs the Lean toolchain moved out of `/root/.elan`, the entrypoint's `data/` + `staticfiles/` writes made uid-aware, and the dev compose volume mount to keep working. Do it as one deliberate change, not piecemeal.
- **Blacklist audit**: the submission pre-scan blocks the full construct blacklist (`sorry`, `axiom`, `IO`, `#eval`, …) on every problem; an instructor must re-permit a construct via the problem's *Allowed constructs* if the intended solution uses one. Worth auditing existing problems when there are more of them.
- **Test coverage gaps**: the LSP message-rewrite flow in [consumers.py](apps/homework/consumers.py) is the main uncovered area, and a coverage `fail_under` gate is still deferred (see the [pyproject.toml](pyproject.toml) comment).

## Stretch goals

- **Course lineage tree**: a git-style visualization of course offerings (branches across terms/sections) built on `Course.renewed_from`. The course page already shows the immediate parent ("Renewed from") and direct children ("Renewed as"); this would render the full family tree so you can see every offering's timeline at a glance.
