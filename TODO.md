# TODO / deferred work

Work intentionally postponed, with enough context to pick it up cleanly.

## Lean performance: when mathlib lands

Cold `lean file.lean` startup is slow per run/submit. Not worth optimizing yet: problems compile a bare file (no `lake` project, no mathlib), so there's no elaboration cache to reuse. Once mathlib / a lake project is in play, consider lake cache reuse and/or a warm Lean process pool.

## Smaller follow-ups (flagged during development)

- **AppArmor profile for the sandbox**: [docker/seccomp/pisa.json](docker/seccomp/pisa.json) tightened the seccomp side of container security_opt; AppArmor is still `unconfined`. A tighter profile needs `apparmor_parser` as root on the host to load, which no dev/CI environment here can do, so it stays as future work rather than shipping unverified.
- **Blacklist audit**: the submission pre-scan blocks the full construct blacklist (`sorry`, `axiom`, `IO`, `#eval`, …) on every problem; an instructor must re-permit a construct via the problem's *Allowed constructs* if the intended solution uses one. Worth auditing existing problems when there are more of them.
