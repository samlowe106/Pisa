"""Defensive execution of untrusted Lean.

Student-submitted Lean is arbitrary code (elaboration can run IO via `#eval`/metaprograms),
so every Lean process — the one-shot grader (``views.problems.run_lean_process``) and the
long-lived LSP server (``consumers.LeanLSPConsumer``) — is launched through here.

Two layers, both gated by ``LEAN_SANDBOX_ENABLED``:

* Layer 1 (always available, no system deps): a stripped environment so the child can't read
  app secrets, POSIX resource limits (CPU / memory / file-size / process count, and no core
  dumps), and its own process group so a runaway tree can be killed as a unit.
* Layer 2 (on by default): ``LEAN_SANDBOX_WRAPPER`` prepends an external sandbox runner —
  bubblewrap by default — for network / filesystem / syscall isolation (no network, read-only
  filesystem, only the per-execution temp dir writable). Set it empty to disable.
"""

import os
import re
import signal

from django.conf import settings

try:
    import resource  # POSIX only
except ImportError:  # pragma: no cover - non-POSIX
    resource = None


def _conf(name, default):
    return getattr(settings, name, default)


def sandbox_env() -> dict:
    """Environment for the Lean child. By default every variable is passed through except
    those whose name matches a sensitive pattern (secrets, passwords, tokens, DB URLs), which
    keeps HOME / PATH / ELAN_HOME so the toolchain still resolves. Set LEAN_SANDBOX_ALLOW_ENV
    to switch to a strict allowlist instead."""
    allow = _conf("LEAN_SANDBOX_ALLOW_ENV", None)
    if allow:
        return {key: os.environ[key] for key in allow if key in os.environ}
    patterns = _conf("LEAN_SANDBOX_DENY_ENV", None) or []
    deny = re.compile("|".join(patterns), re.IGNORECASE) if patterns else None
    return {
        key: value
        for key, value in os.environ.items()
        if not (deny and deny.search(key))
    }


def _build_preexec(cpu_seconds):
    memory_mb = _conf("LEAN_SANDBOX_MEMORY_MB", 0)
    fsize_mb = _conf("LEAN_SANDBOX_FSIZE_MB", 0)
    max_processes = _conf("LEAN_SANDBOX_MAX_PROCESSES", 0)

    def _apply():  # runs in the child after fork(), before exec()
        if resource is None:
            return
        limits = [(resource.RLIMIT_CORE, 0)]  # no core dumps
        if cpu_seconds:
            limits.append((resource.RLIMIT_CPU, cpu_seconds))
        if memory_mb:
            limits.append((resource.RLIMIT_AS, memory_mb * 1024 * 1024))
        if fsize_mb:
            limits.append((resource.RLIMIT_FSIZE, fsize_mb * 1024 * 1024))
        if max_processes:
            limits.append((resource.RLIMIT_NPROC, max_processes))
        for which, value in limits:
            try:
                resource.setrlimit(which, (value, value))
            except (ValueError, OSError):
                pass  # best-effort: never block the exec on a limit we couldn't set

    return _apply


def popen_kwargs(cpu_seconds=None) -> dict:
    """kwargs for ``subprocess.Popen`` / ``asyncio.create_subprocess_exec``. Always starts a
    new session so the process group can be killed; adds the stripped env and rlimits when the
    sandbox is enabled. Pass ``cpu_seconds=None`` for long-lived processes (the LSP server) so
    they aren't killed by a CPU-time cap."""
    kwargs: dict = {"start_new_session": True}
    if _conf("LEAN_SANDBOX_ENABLED", True):
        kwargs["env"] = sandbox_env()
        if resource is not None:
            kwargs["preexec_fn"] = _build_preexec(cpu_seconds)
    return kwargs


def wrap_argv(argv, *, workdir=None) -> list:
    """Prepend the external sandbox runner (LEAN_SANDBOX_WRAPPER), if configured. Any
    ``{workdir}`` token in the wrapper is replaced with ``workdir`` — the per-execution temp
    directory the Lean file lives in — so a read-only-filesystem sandbox (bubblewrap) can still
    bind that one directory in (e.g. ``--bind {workdir} {workdir} --chdir {workdir}``).
    """
    if not _conf("LEAN_SANDBOX_ENABLED", True):
        return list(argv)
    wrapper = _conf("LEAN_SANDBOX_WRAPPER", None) or []
    if workdir is not None:
        wrapper = [token.replace("{workdir}", str(workdir)) for token in wrapper]
    return list(wrapper) + list(argv)


def kill_process_group(proc) -> None:
    """SIGKILL the whole process group led by ``proc`` (it starts its own session), falling
    back to killing just the process if the group is already gone."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
