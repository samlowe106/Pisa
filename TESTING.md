# Test plan

A prioritized, living checklist. CI ([.github/workflows/tests.yml](.github/workflows/tests.yml))
runs `manage.py test` under coverage in the Docker image (Lean toolchain baked in), so new tests
run automatically. **224 tests landed** across [apps/homework/tests/](apps/homework/tests/)
(stats, sandbox, permissions, grading, consumer, models, grades-logic, forms, views,
problem-views, lean-lsp, management, ops). All three phases below are essentially complete;
remaining unticked items are noted inline as lower-value follow-ups.

**Line coverage ≈ 91%** (`coverage report`, branch mode). At 100%: `lean_policy`, `stats`,
`forms`, `exports`, `context_processors`, `lean_lsp`, `views/grades`, `views/mixins`,
`views/lean_source_files`, `create_test_data`, `admin`, `health`. The notable remainders are
`consumers.py` (59% — the LSP message-rewrite flow; the security-critical cap/access paths *are*
covered) and `sandbox.py` (57% — the rlimit `preexec_fn` body runs post-`fork()` so coverage
can't trace it, though `test_sandbox.py` verifies it via the child's own `getrlimit`).

### CI environment notes (learned the hard way)
- **`SECURE_SSL_REDIRECT`**: CI runs with `DEBUG` off, which turns on the HTTP→HTTPS 301 redirect;
  the test client always speaks plain HTTP, so every client request 301'd. [settings.py](pisa/settings.py)
  now disables it under the test runner (alongside the static-manifest fallback).
- **bubblewrap**: `@requires_bwrap` ([tests/utils.py](apps/homework/tests/utils.py)) probes that
  bwrap can actually *create* a sandbox (not just that the binary exists) — inside a container
  without the mount caps it fails at `mount`, so the Layer 2 tests now skip there instead of
  failing. Real-Lean *logic* tests run Layer-1-only (`LEAN_SANDBOX_WRAPPER=[]`) so they don't
  depend on bwrap; the full-sandbox smoke test is gated on the probe.

Priority order is **security & permissions first** (Phase 1), then core logic (Phase 2), then
integration/ops (Phase 3). Tick items as they land.

## Test infrastructure to add first

- [x] **Role-matrix fixtures** — `make_role_matrix()` in [tests/utils.py](apps/homework/tests/utils.py)
      builds admin/instructor/TA/student/outsider + a published course/assignment/problem.
- [x] **`@requires_lean` skip** — `skipUnless` a Lean executable is resolvable
      ([tests/utils.py](apps/homework/tests/utils.py)); `@requires_bwrap` likewise for Layer 2.
- [x] **Sandbox test knobs** — `override_settings(LEAN_SANDBOX_*=...)` with tiny limits + stand-in
      executables (`test_sandbox.py`).
- [x] **Async consumer harness** — `WebsocketCommunicator` + `TransactionTestCase` Lean-cap tests
      (`test_consumer.py`).
- [ ] Tag slow/real-Lean tests so they can be selected/deselected.

## Phase 1 — Security & permissions (do first)

### 1a. Sandbox mechanics — `apps/homework/sandbox.py` (no Lean; `SimpleTestCase` + stand-ins) ✅ `test_sandbox.py`
- [x] `sandbox_env()` drops `SECRET_KEY`/secret-pattern vars, keeps `PATH`.
- [x] `popen_kwargs()` sets `env`, `start_new_session`, `preexec_fn`.
- [x] rlimits actually bite: CPU-bound stand-in under `RLIMIT_CPU` is killed.
- [x] `kill_process_group()` SIGKILLs the whole tree (stand-in that spawns children → all dead).
- [x] `wrap_argv()` substitutes `{workdir}` / prepends wrapper; returns argv unchanged when disabled.
- [ ] `RLIMIT_AS` / `RLIMIT_FSIZE` / `RLIMIT_NPROC` reject a stand-in that allocates / writes big /
      forks (CPU cap covered; the other three limits not yet exercised directly).

### 1b. Adversarial Lean — real execution (`@requires_lean` / `@requires_bwrap`, runs in CI)
- [x] **Network / filesystem isolation (Layer 2, default):** under the default bubblewrap sandbox,
      a stand-in that opens a socket or writes outside its temp dir **fails** (`test_sandbox.py`
      `SandboxIsolationTests`); a real proof still compiles inside the sandbox (`SandboxLeanSmokeTest`).
- [x] Runaway/∞-loop *real-Lean* `#eval` → reported as a timeout and the request returns, doesn't
      hang (`SandboxAdversarialLeanTests`); the group-reap mechanism is pinned by the stand-in
      CPU/kill tests in `SandboxMechanicsTests`.
- [x] Disk: writing past `RLIMIT_FSIZE` is killed end-to-end (`SandboxResourceLimitTests`).
- [x] `RLIMIT_AS` / `FSIZE` / `NPROC` / `CORE` are actually applied to the child (read back via the
      child's own `getrlimit`) — deterministic, no allocator dependence.
- [x] Env secrecy via *real Lean*: `IO.getEnv` of a `*SECRET*` var returns nothing while a
      non-secret var still passes through (`SandboxAdversarialLeanTests`).
- [ ] A real-Lean *memory* bomb tripping `RLIMIT_AS` end-to-end is left out: a cap low enough to
      bite is below Lean's own startup footprint, so it's flaky; the applied-limit + FSIZE-bite
      tests cover the mechanism instead.

### 1c. Submission policy / anti-cheat — `lean_policy.py`, `grade_lean_submission` ✅ `test_grading.py`
- [x] `scan()` flags each category; `allowed=` re-permits (pure unit).
- [x] `parse_axioms` / `forbidden_axioms` — report parsing, `sorryAx` always forbidden, `allowed`
      extends the permitted set, missing report → `None`.
- [x] Pre-scan **rejects** `sorry`/`axiom`/`IO`/`#eval`/`Socket` in *student* code (no Lean run);
      confirmed it scans student code, **not** the instructor prefix.
- [x] `#print axioms` backstop catches a `sorry` proof even when the text scan is told to allow it
      (`@requires_lean`).
- [x] A correct proof **passes** under the full sandbox (`@requires_lean`).
- [x] `required_code` enforced; `sanitize_lean_output` hides internals from students but keeps them
      for staff (`keep_internal`); `assemble_lean_submission_source` student/full split.
- [ ] timeout/error → correct status (`STATUS_ERROR`) — not yet exercised directly.

### 1d. Lean-instance cap — `consumers.py` (async) ✅ `test_consumer.py`
- [x] Passive 2nd connection → busy reject (4409); `?takeover=1` evicts the holder (4410) and
      reassigns; disconnect releases the slot; different users are independent.
      (Per-process today; multi-worker correctness is a TODO item.)

### 1e. Permission / infosec matrix (`TestCase`) ✅ `test_permissions.py`
- [x] **Grade export is instructor/admin-only at the *endpoint*** — `export_grades_csv` /
      `export_grades_excel` 403 for TA/student/outsider, 302 unauthenticated; the export control
      isn't rendered for TAs; TAs **can** still view the grades table.
- [x] Edit / `course_renew` instructor+admin-only (404 for others); course create admin-only;
      only admins manage instructors; TAs cannot add students; draft visibility; `editable_courses`.
- [x] LSP **WebSocket access gate** (enrolled student + course staff only; outsider and a draft
      problem are refused before any Lean spawns) — `test_consumer.py` `LeanSocketAccessTests`.
- [x] member **remove** (instructor), Lean source-file library is instructor/admin-only and
      owner-scoped — `test_views.py`; context-processor role flags — `test_ops.py`.
- [x] `accessible_problems` (students: published-in-enrolled only; staff/admin: all) and
      `editable_assignments`/`editable_problems`; cross-course isolation — `test_models.py`.
- [x] Unauthenticated → login redirect; dashboard redirects to `/courses/` — `test_views.py`.
- [ ] Still open (lower value): stats-tab visibility, hidden-source-file tagging, and the
      `run`/`submit` endpoints' own access checks (the policy/grading path they call is covered).

## Phase 2 — Core domain logic ✅ `test_models.py`, `test_grades_logic.py`, `test_forms.py`

- [x] Scoring policies — `_passed_pairs` for best / recent / superscore, including where recent
      **revokes** an earlier pass (`test_grades_logic.py`, pure-unit).
- [x] Grade bands / `letter_for` (boundary-inclusive); `student_course_summary` (grade +
      open-assignment count) / `staff_course_summary` (roster + draft count); cards
      (active/previous split, staff-vs-student card, roster marker, student grade).
- [x] Stats plumbing — `section_score_data` (overall = every student; per-assignment =
      submitters only), `compare_two_sections` (matched by slug), `grade_distribution_chart`.
- [x] `renew_course` clone — copies content + staff, **not** students/submissions; due dates
      cleared; unique slug; `renewed_from` set. `course_family` lineage across a 3-deep chain.
- [x] `Submission.is_late`; `Problem.position` (incl. query-free when prefetched) + reorder;
      `display_name`; reserved-slug validators (function + `full_clean`); `get_absolute_url`.
- [x] Forms — `CourseForm` strictly-descending bands + roster email parsing + role exclusivity +
      admin-only instructors field; `CourseRenewForm` term/section; `ProblemForm` `allowed_constructs`;
      `AssignmentForm` reserved-slug rejection.

## Phase 3 — Integration, ops, edges ✅ `test_views.py`, `test_ops.py`

- [x] View smoke/integration: course create (admin becomes instructor) + update + renew flow
      (offering created, redirects), self-enrol (+ staff rejected), member remove, dashboard
      redirect, **grades export content** (CSV substrings + parsed XLSX rows), assignment-create
      page, nested problem reorder, Lean source-file library (instructor-only, owner-scoped).
- [x] Health endpoint: 200 + `{database: ok}`; 503 + `{database: error}` when the DB is unreachable.
- [x] Self-host settings: `env_bool` truth table; `DEBUG=False` → secure cookies + proxy SSL header;
      `CSRF_TRUSTED_ORIGINS`/`ALLOWED_HOSTS` from `PISA_DOMAIN`; `DEBUG=True` relaxes hosts
      (via an isolated settings-module reload).
- [x] N+1 guard: `assertNumQueries(1)` on the admin `course_cards_for` path; `Problem.position`
      query-free when prefetched (`test_models.py`).
- [ ] Edge cases partially covered (empty/ungraded course in `test_grades_logic.py`,
      single-section family in `test_models.py`); a dedicated empty/no-submissions view pass is
      still open.

## Conventions

- `SimpleTestCase` for pure logic (stats, sandbox mechanics, policy scan, settings parsing);
  `TestCase` for DB; async for the consumer. Real-Lean tests use `@requires_lean`.
- **Treat the security tests as executable specs.** When one reveals a gap, fix the system —
  don't weaken the test.

## Sandbox layers (now both on by default)

- **Layer 1** (always): stripped environment (no secrets), POSIX resource limits
  (CPU/memory/file-size/process count), and its own process group (timeout reaps the whole tree).
- **Layer 2** (`LEAN_SANDBOX_WRAPPER`, **now default = bubblewrap**): a new network/pid/ipc
  namespace (no network), a read-only filesystem, and only the per-execution temp dir writable.
  Inside Docker it needs the relaxed seccomp profile the compose files + CI now set. Verified on
  the dev host (proof passes; network unreachable; FS read-only). A host without bubblewrap must
  set `LEAN_SANDBOX_WRAPPER=""` (and then loses network/FS isolation — Layer 1 only).
- The construct **blacklist** (default-on pre-scan) is defense-in-depth on top — it discourages
  `IO`/`Socket`/`#eval` in submissions but is evadable, so it's not the primary control.

Remaining hardening (TODO.md): a tighter custom seccomp profile instead of `unconfined`, or
running Lean in a separate locked-down runner container.
