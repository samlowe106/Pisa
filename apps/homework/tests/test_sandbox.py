"""Tests for the untrusted-Lean sandbox (apps/homework/sandbox.py).

- Mechanics (Layer 1) use stand-in commands — no Lean needed.
- Isolation (Layer 2) uses the default bubblewrap wrapper with stand-ins — `@requires_bwrap`.
- One real-Lean smoke test confirms a proof still compiles inside the full sandbox.
"""

import os
import subprocess
import tempfile

from django.test import SimpleTestCase, override_settings

from apps.homework import sandbox
from apps.homework.lean_runner import run_lean_process

from .utils import requires_bwrap, requires_lean

# The production default wrapper (bubblewrap) — used directly so the tests pin real behaviour.
BWRAP = (
    "bwrap --unshare-all --die-with-parent --new-session "
    "--ro-bind / / --dev /dev --proc /proc --tmpfs /tmp "
    "--bind {workdir} {workdir} --chdir {workdir}"
).split()


class SandboxMechanicsTests(SimpleTestCase):
    """Layer 1: env strip, rlimits, process-group kill, wrapper plumbing. No Lean."""

    @override_settings(SECRET_KEY="super-secret-value", LEAN_SANDBOX_ALLOW_ENV=None)
    def test_env_strip_removes_secrets_keeps_path(self):
        os.environ["MY_API_TOKEN"] = "leak-me"
        try:
            env = sandbox.sandbox_env()
        finally:
            del os.environ["MY_API_TOKEN"]
        self.assertNotIn("SECRET_KEY", env)
        self.assertNotIn("MY_API_TOKEN", env)
        self.assertIn("PATH", env)

    @override_settings(LEAN_SANDBOX_ALLOW_ENV=["PATH"])
    def test_allowlist_mode_keeps_only_listed_vars(self):
        os.environ["PISA_EXTRA_VAR"] = "x"
        try:
            env = sandbox.sandbox_env()
        finally:
            del os.environ["PISA_EXTRA_VAR"]
        self.assertNotIn("PISA_EXTRA_VAR", env)  # not on the allowlist
        self.assertEqual(set(env) - {"PATH"}, set())  # nothing beyond the allowlist

    def test_popen_kwargs_shape(self):
        kwargs = sandbox.popen_kwargs(cpu_seconds=5)
        self.assertTrue(kwargs["start_new_session"])
        self.assertIn("env", kwargs)
        self.assertIn("preexec_fn", kwargs)
        self.assertNotIn("SECRET_KEY", kwargs["env"])

    def test_wrap_argv_substitutes_workdir(self):
        with override_settings(LEAN_SANDBOX_WRAPPER=["run", "--dir", "{workdir}"]):
            self.assertEqual(
                sandbox.wrap_argv(["lean", "f.lean"], workdir="/tmp/x"),
                ["run", "--dir", "/tmp/x", "lean", "f.lean"],
            )

    def test_wrap_argv_disabled_returns_argv(self):
        with override_settings(LEAN_SANDBOX_ENABLED=False, LEAN_SANDBOX_WRAPPER=BWRAP):
            self.assertEqual(sandbox.wrap_argv(["lean"], workdir="/tmp"), ["lean"])

    def test_cpu_rlimit_kills_runaway(self):
        # A busy loop under a 1-second CPU cap is killed (negative return code).
        result = subprocess.run(
            ["python3", "-c", "x=0\nwhile True: x+=1"],
            capture_output=True,
            timeout=20,
            **sandbox.popen_kwargs(cpu_seconds=1),
        )
        self.assertLess(result.returncode, 0)

    def test_kill_process_group_reaps_children(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "child.pid")
            proc = subprocess.Popen(
                ["sh", "-c", f"sleep 60 & echo $! > {pidfile}; wait"],
                **sandbox.popen_kwargs(cpu_seconds=None),
            )
            child_pid = None
            for _ in range(200):  # wait up to ~2s for the child to spawn
                if os.path.exists(pidfile) and os.path.getsize(pidfile):
                    child_pid = int(open(pidfile).read())
                    break
                subprocess.run(["sleep", "0.01"])
            self.assertIsNotNone(child_pid)
            sandbox.kill_process_group(proc)
            proc.wait(timeout=5)
            for _ in range(200):
                try:
                    os.kill(child_pid, 0)
                    subprocess.run(["sleep", "0.01"])
                except ProcessLookupError:
                    break
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)  # child must be dead too


@requires_bwrap
@override_settings(LEAN_SANDBOX_WRAPPER=BWRAP)
class SandboxIsolationTests(SimpleTestCase):
    """Layer 2 (default bubblewrap): the real network / filesystem isolation, via stand-ins."""

    def _run(self, inner_argv):
        with tempfile.TemporaryDirectory() as workdir:
            argv = sandbox.wrap_argv(inner_argv, workdir=workdir)
            return subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=40,
                **sandbox.popen_kwargs(cpu_seconds=15),
            )

    def test_network_is_blocked(self):
        result = self._run(
            [
                "python3",
                "-c",
                "import socket; s=socket.socket(); s.settimeout(3); "
                "s.connect(('1.1.1.1', 53)); print('CONNECTED')",
            ]
        )
        self.assertNotIn("CONNECTED", result.stdout)
        self.assertNotEqual(result.returncode, 0)

    def test_filesystem_is_read_only_outside_workdir(self):
        marker = "/pisa_escape_test_marker"
        try:
            result = self._run(
                ["sh", "-c", f"echo x > {marker} && echo WROTE || echo BLOCKED"]
            )
            self.assertIn("BLOCKED", result.stdout)
            self.assertFalse(os.path.exists(marker))  # nothing escaped to the host
        finally:
            if os.path.exists(marker):
                os.remove(marker)


class SandboxResourceLimitTests(SimpleTestCase):
    """Layer 1 POSIX rlimits — the guard against a submission eating memory / disk / PIDs.

    The applied-limits test reads the child's *own* ``getrlimit`` so it's deterministic (no
    dependence on allocator behaviour); one behavioural test proves a file-size cap actually
    kills a runaway writer end-to-end.
    """

    def _child_rlimits(self):
        # Print the child's effective soft limits for the resources the sandbox sets.
        code = (
            "import resource as r;"
            "print(r.getrlimit(r.RLIMIT_AS)[0], r.getrlimit(r.RLIMIT_FSIZE)[0], "
            "r.getrlimit(r.RLIMIT_NPROC)[0], r.getrlimit(r.RLIMIT_CORE)[0])"
        )
        result = subprocess.run(
            ["python3", "-c", code],
            capture_output=True,
            text=True,
            timeout=20,
            **sandbox.popen_kwargs(cpu_seconds=None),
        )
        return [int(x) for x in result.stdout.split()]

    @override_settings(
        LEAN_SANDBOX_MEMORY_MB=256,
        LEAN_SANDBOX_FSIZE_MB=8,
        LEAN_SANDBOX_MAX_PROCESSES=32,
    )
    def test_configured_rlimits_are_applied_to_the_child(self):
        as_lim, fsize_lim, nproc_lim, core_lim = self._child_rlimits()
        self.assertEqual(as_lim, 256 * 1024 * 1024)
        self.assertEqual(fsize_lim, 8 * 1024 * 1024)
        self.assertEqual(nproc_lim, 32)
        self.assertEqual(core_lim, 0)  # no core dumps, always

    @override_settings(LEAN_SANDBOX_FSIZE_MB=1)
    def test_fsize_limit_kills_a_runaway_writer(self):
        # Writing well past the 1 MB cap trips RLIMIT_FSIZE (SIGXFSZ → negative return code).
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "big.bin")
            result = subprocess.run(
                ["python3", "-c", f"open({target!r},'wb').write(b'x'*(50*1024*1024))"],
                capture_output=True,
                timeout=20,
                **sandbox.popen_kwargs(cpu_seconds=5),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertLess(os.path.getsize(target), 8 * 1024 * 1024)  # cap held


@requires_lean
@requires_bwrap
class SandboxLeanSmokeTest(SimpleTestCase):
    def test_real_proof_compiles_inside_sandbox(self):
        # Guards that the full default sandbox (bubblewrap + env-strip + read-only FS) doesn't
        # break the elan toolchain. Needs a bwrap that can actually sandbox here.
        with override_settings(LEAN_TIMEOUT=120):
            result = run_lean_process("theorem t : True := trivial\n")
        self.assertEqual(result.get("returncode"), 0)


@requires_lean
@override_settings(LEAN_SANDBOX_WRAPPER=[])  # Layer 1 only: these don't depend on bwrap
class SandboxAdversarialLeanTests(SimpleTestCase):
    """Real untrusted Lean against the Layer 1 controls (no bubblewrap needed): a non-terminating
    program is killed by the wall-clock timeout, and secrets never reach the child's environment.
    Network / filesystem isolation (Layer 2) is covered by SandboxIsolationTests."""

    def test_runaway_evaluation_times_out_and_returns(self):
        runaway = "partial def spin : Nat → Nat\n  | n => spin (n + 1)\n#eval spin 0\n"
        with override_settings(LEAN_TIMEOUT=5):
            result = run_lean_process(runaway)
        # The request returns (doesn't hang) and is reported as a timeout, not a clean run.
        self.assertTrue(result.get("timeout"))

    def test_secret_env_is_stripped_but_public_env_passes_through(self):
        # "SECRET" matches the deny pattern; the other name is innocuous and should survive.
        os.environ["PISA_TEST_SECRET"] = "SENTINEL_LEAK"
        os.environ["PISA_TEST_PUBLIC"] = "VISIBLE_OK"
        try:
            program = (
                '#eval (IO.getEnv "PISA_TEST_SECRET")\n'
                '#eval (IO.getEnv "PISA_TEST_PUBLIC")\n'
            )
            with override_settings(LEAN_TIMEOUT=60):
                result = run_lean_process(program)
        finally:
            del os.environ["PISA_TEST_SECRET"]
            del os.environ["PISA_TEST_PUBLIC"]
        combined = (result.get("stdout") or "") + (result.get("stderr") or "")
        self.assertNotIn("SENTINEL_LEAK", combined)  # secret never reached Lean
        self.assertIn("VISIBLE_OK", combined)  # non-secret env still passed through
