"""Run and grade Lean submissions: assemble the compilable document, execute Lean in the
sandbox, and apply the two-layer anti-cheat policy (construct scan + axiom audit).

Used by the run/submit views (``views/problems.py``) and shared with the live-LSP consumer —
``assemble_lean_submission_source`` is deliberately kept in sync with the document assembly in
``consumers.py``.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings

from . import lean_policy, sandbox
from .models import Problem, ProblemBlock, Submission


def get_lean_executable() -> str:
    explicit = getattr(settings, "LEAN_EXECUTABLE", None)
    if explicit:
        return explicit

    found = shutil.which("lean")
    if found:
        return found

    elan_path = Path.home() / ".elan" / "bin" / "lean"
    if elan_path.exists():
        return str(elan_path)

    raise FileNotFoundError("Lean executable not found")


def _sandbox_wrapper_failed(stderr: str | None) -> bool:
    """True when the sandbox wrapper itself failed to start, so Lean never ran. Bubblewrap
    prefixes its own errors with ``bwrap:`` (e.g. "bwrap: Failed to make / slave: Permission
    denied" when Docker's default AppArmor profile blocks it)."""
    return any(line.startswith("bwrap:") for line in (stderr or "").splitlines())


def sanitize_lean_output(output: str, keep_internal: bool = False) -> str:
    if keep_internal or not output:
        return output or ""

    lines = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^info:", stripped, re.IGNORECASE):
            continue
        lines.append(line)

    return "\n".join(lines)


def filter_lean_response(response: dict, keep_internal: bool) -> dict:
    filtered = response.copy()
    for key in ("stdout", "stderr"):
        if key in filtered and filtered[key] is not None:
            filtered[key] = sanitize_lean_output(filtered[key], keep_internal)
    return filtered


def parse_lean_feedback(
    stdout: str | None, stderr: str | None, returncode: int | None = None
) -> dict:
    goals: list[str] = []
    messages: list[str] = []
    errors: list[str] = []

    for stream in (stderr or "", stdout or ""):
        for line in stream.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            lower = stripped.lower()
            if stripped.startswith("bwrap:"):
                # Sandbox-wrapper failure, not Lean output — never a "message".
                errors.append(line)
            elif lower.startswith("goal:"):
                goals.append(stripped[len("goal:") :].strip())
            elif lower.startswith("msg:") or lower.startswith("message:"):
                messages.append(stripped.split(":", 1)[1].strip())
            elif re.search(r"\berror\b", lower):
                errors.append(line)
            elif re.search(r"\bwarning\b", lower):
                messages.append(line)
            elif "⊢" in line:
                goals.append(line)
            else:
                messages.append(line)

    if returncode == 0 and not goals and not messages and not errors:
        if stdout and stdout.strip():
            messages.append(stdout.strip())
        else:
            messages.append("Lean ran with no errors :)")

    return {
        "goals": goals,
        "messages": messages,
        "errors": errors,
    }


def build_lean_run_response(response: dict, keep_internal: bool) -> dict:
    filtered = filter_lean_response(response, keep_internal)
    parsed = parse_lean_feedback(
        filtered.get("stdout"), filtered.get("stderr"), filtered.get("returncode")
    )
    filtered.update(parsed)
    return filtered


def assemble_lean_submission_source(
    problem: Problem, post_data
) -> tuple[str, str, str | None]:
    """Build the full compilable document and, separately, just the *student's* editable text
    (so policy checks scan student code, not the instructor's prefix). Returns
    ``(full_code, student_code, error)``."""
    editable_blocks = list(
        problem.blocks.filter(
            block_type=ProblemBlock.BLOCK_TYPE_EDITABLE_CODE
        ).order_by("order")
    )
    submitted_editables = {}

    for block in editable_blocks:
        field_name = f"editable_code_{block.pk}"
        block_value = post_data.get(field_name)
        if block_value is None and len(editable_blocks) == 1:
            block_value = post_data.get("code")
        if block_value is None:
            return "", "", f"Missing submission for editable block {block.pk}."
        submitted_editables[block.pk] = block_value

    if editable_blocks:
        full_code = ""
        for source_file in problem.assignment.source_files.order_by("pk"):
            if source_file.content:
                full_code += source_file.content + "\n\n"

        student_code = ""
        for block in problem.blocks.order_by("order"):
            if block.block_type == ProblemBlock.BLOCK_TYPE_FIXED_CODE:
                if block.content:
                    full_code += block.content + "\n\n"
            elif block.block_type == ProblemBlock.BLOCK_TYPE_EDITABLE_CODE:
                editable = submitted_editables.get(block.pk, "")
                full_code += editable + "\n\n"
                student_code += editable + "\n\n"

        return full_code, student_code, None

    code = post_data.get("code", "")
    return code, code, None


def run_lean_process(code: str, extra: str = "") -> dict:
    """Write ``code`` (plus optional ``extra``, e.g. a grading stub) to a temp ``.lean`` file
    and run Lean on it, always cleaning the file up. Returns one of:

    - ``{"returncode": int, "stdout": str, "stderr": str}`` — Lean ran
    - ``{"timeout": True, "stdout": str, "stderr": str}`` — timed out (with partial output)
    - ``{"missing": True}`` — the Lean executable could not be found
    - ``{"sandbox_error": True, "stdout": str, "stderr": str}`` — the sandbox wrapper failed
      to start, so Lean never ran (a server problem, not a grading result)
    """
    workdir = None
    try:
        # Own temp dir per run, so the sandbox can bind *just* this directory writable.
        workdir = tempfile.mkdtemp(prefix="pisa_lean_")
        source_path = Path(workdir) / "submission.lean"
        source_path.write_text(code + (f"\n\n{extra}" if extra else ""))

        try:
            argv = sandbox.wrap_argv(
                [get_lean_executable(), str(source_path)], workdir=workdir
            )
        except FileNotFoundError:
            return {"missing": True}

        wall_timeout = getattr(settings, "LEAN_TIMEOUT", 60)
        # CPU-time backstop: a process can't outrun the wall clock, but a multi-threaded one
        # could burn more CPU, so cap it at a generous multiple of the wall timeout.
        cpu_seconds = (
            getattr(settings, "LEAN_SANDBOX_CPU_SECONDS", 0) or wall_timeout * 4
        )
        try:
            process = subprocess.Popen(
                argv,
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                **sandbox.popen_kwargs(cpu_seconds=cpu_seconds),
            )
        except FileNotFoundError:
            return {"missing": True}

        try:
            stdout, stderr = process.communicate(timeout=wall_timeout)
        except subprocess.TimeoutExpired:
            # Kill the whole process group so no Lean child outlives the request.
            sandbox.kill_process_group(process)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            return {"timeout": True, "stdout": stdout or "", "stderr": stderr or ""}
        if process.returncode != 0 and _sandbox_wrapper_failed(stderr):
            return {
                "sandbox_error": True,
                "stdout": stdout or "",
                "stderr": stderr or "",
            }
        return {
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)


def grade_lean_submission(
    problem: Problem,
    code: str,
    student_code: str | None = None,
    keep_internal: bool = False,
) -> tuple[str, str]:
    if problem.required_code and problem.required_code.strip() not in code:
        return (
            Submission.STATUS_FAILED,
            "Your submission does not include the required code snippet.",
        )

    # Layer 1: reject disallowed constructs in the *student's* code before running Lean.
    allowed = frozenset(problem.allowed_constructs or ())
    hits = lean_policy.scan(
        student_code if student_code is not None else code, allowed=allowed
    )
    if hits:
        listed = "\n".join(f"  • {rule.id}: {rule.reason}" for rule in hits)
        return (
            Submission.STATUS_FAILED,
            "Your submission uses constructs that aren't allowed here:\n" + listed,
        )

    # Append a `#print axioms` audit of the target declaration so the soundness check can't be
    # evaded (catches sorry/admit/axiom/native_decide however they got in).
    extra = problem.grading_stub or ""
    if problem.axiom_target:
        extra = (
            extra + "\n\n" if extra else ""
        ) + f"#print axioms {problem.axiom_target}"

    result = run_lean_process(code, extra)

    if result.get("missing"):
        return (
            Submission.STATUS_ERROR,
            "Lean executable not found. Install Lean on the server or configure a Lean runtime.",
        )
    if result.get("sandbox_error"):
        msg = (
            "The server's Lean sandbox failed to start, so your submission could not be "
            "graded. This is a server problem, not an issue with your proof — please tell "
            "your instructor."
        )
        if keep_internal:
            detail = (result["stderr"] or result["stdout"]).strip()
            if detail:
                msg += "\n" + detail
        return (Submission.STATUS_ERROR, msg)
    if result.get("timeout"):
        out = sanitize_lean_output(result["stdout"], keep_internal=keep_internal)
        err = sanitize_lean_output(result["stderr"], keep_internal=keep_internal)
        combined = (err + "\n" + out).strip() or ""
        msg = "Lean execution timed out."
        if combined:
            msg += "\nPartial output:\n" + combined
        return (Submission.STATUS_ERROR, msg)

    if result["returncode"] == 0:
        # Layer 2: the proof compiled — now verify it doesn't lean on forbidden axioms.
        if problem.axiom_target:
            allowed_axioms = frozenset(
                name.strip()
                for name in problem.allowed_axioms.split(",")
                if name.strip()
            )
            bad = lean_policy.forbidden_axioms(result["stdout"], allowed=allowed_axioms)
            if bad is None:
                return (
                    Submission.STATUS_FAILED,
                    f"Could not verify the axioms of '{problem.axiom_target}'. "
                    "Check that this declaration exists in the submission.",
                )
            if bad:
                if "sorryAx" in bad:
                    return (
                        Submission.STATUS_FAILED,
                        "Your proof is incomplete — it relies on `sorry`.",
                    )
                return (
                    Submission.STATUS_FAILED,
                    "Your proof depends on disallowed axioms: "
                    + ", ".join(sorted(bad)),
                )
        return (
            Submission.STATUS_PASSED,
            sanitize_lean_output(
                result["stdout"] or "Lean ran with no errors :)",
                keep_internal=keep_internal,
            ),
        )
    return (
        Submission.STATUS_FAILED,
        sanitize_lean_output(
            result["stderr"]
            or result["stdout"]
            or f"Lean exited with code {result['returncode']}",
            keep_internal=keep_internal,
        ),
    )
