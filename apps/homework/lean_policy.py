"""Policy catalogue: Lean constructs we may disallow in *student* submissions.

This module is the policy catalogue (``RULES``) plus the checks that enforce it, used by
``grade_lean_submission`` in two layers:

1. ``scan()`` rejects disallowed constructs in the student's editable code *before* Lean runs.
   It's a literal text scan (matches inside comments/strings, evadable), so it's the first,
   cheap line of defence.
2. ``forbidden_axioms()`` audits a ``#print axioms`` report *after* a successful compile — the
   un-evadable backstop for the soundness rules (catches ``sorry``/``axiom``/``native_decide``
   however they got in).

Per-problem ``Problem.allowed_constructs`` / ``axiom_target`` / ``allowed_axioms`` tune both.
The scan runs against the student's *editable* code only, never the instructor's imported
prefix / fixed blocks (which legitimately use ``axiom``, ``opaque``, ``@[extern]`` …).

Add a rule by appending a ``Rule`` to ``RULES``.
"""

import re
from dataclasses import dataclass

# Rule categories.
UNSOUND = "unsound"  # lets a false/unproven statement typecheck — grade integrity
SYSTEM = "system"  # touches the OS: files, processes, FFI
NETWORK = "network"  # opens network connections
ESCAPE = "escape"  # runs arbitrary code at elaboration time (metaprogramming)
EXPENSIVE = "expensive"  # can blow up compile time / memory


@dataclass(frozen=True)
class Rule:
    id: str  # stable identifier (used for per-problem allow/deny overrides)
    pattern: str  # regex, matched with re.search against the submission text
    category: str
    reason: str  # shown to the student / instructor when it trips


RULES: list[Rule] = [
    # --- Soundness / anti-cheat: pass without actually proving the goal ------------------
    Rule("sorry", r"\bsorry\b", UNSOUND, "Admits any goal without proof."),
    Rule("admit", r"\badmit\b", UNSOUND, "Closes a goal without proving it."),
    Rule("axiom", r"\baxiom\b", UNSOUND, "Asserts a statement true without proof."),
    Rule(
        "native_decide",
        r"\bnative_decide\b",
        UNSOUND,
        "Trusts compiled code; a documented soundness escape hatch.",
    ),
    Rule(
        "implemented_by",
        r"@\[\s*implemented_by",
        UNSOUND,
        "Replaces a definition with code the kernel doesn't check.",
    ),
    Rule("unsafe", r"\bunsafe\b", UNSOUND, "Opts out of Lean's safety checks."),
    Rule(
        "of_reduce_bool",
        r"\bLean\.ofReduceBool\b",
        UNSOUND,
        "Reflects compiled Bool evaluation into a proof.",
    ),
    # --- System / IO (defense-in-depth above the OS sandbox) ----------------------------
    Rule(
        "io_process",
        r"\bIO\.Process\b",
        SYSTEM,
        "Spawns external processes (also a network vector via curl/wget).",
    ),
    Rule("io_fs", r"\bIO\.FS\b", SYSTEM, "Reads or writes the filesystem."),
    Rule("system_ns", r"\bSystem\.", SYSTEM, "OS interaction (paths, env, …)."),
    Rule("extern", r"@\[\s*extern", SYSTEM, "Binds to native / FFI code."),
    # --- Network ------------------------------------------------------------------------
    Rule("socket", r"\bSocket\b", NETWORK, "Opens network sockets."),
    Rule("http", r"\bHttp\b", NETWORK, "Makes HTTP requests."),
    # --- Elaboration-time code execution ------------------------------------------------
    Rule(
        "eval", r"#eval\b", ESCAPE, "Runs arbitrary code (incl. IO) while elaborating."
    ),
    Rule("run_cmd", r"\brun_cmd\b", ESCAPE, "Runs arbitrary metaprogram commands."),
    Rule("init_attr", r"@\[\s*init\b", ESCAPE, "Runs an initialiser at load time."),
    # --- Expense ------------------------------------------------------------------------
    Rule(
        "max_heartbeats",
        r"set_option\s+maxHeartbeats",
        EXPENSIVE,
        "Raises or removes the elaboration time budget.",
    ),
]


def scan(code: str, *, allowed: frozenset = frozenset()) -> list:
    """Return the rules that match ``code``. ``allowed`` re-permits specific rule ids (e.g. a
    problem that legitimately wants ``#eval``).

    NOTE: a literal text scan — it also matches inside comments and strings, and can be evaded
    (whitespace, Unicode lookalikes). The ``#print axioms`` post-check below is the
    un-evadable backstop for the soundness rules.
    """
    return [
        rule
        for rule in RULES
        if rule.id not in allowed and re.search(rule.pattern, code)
    ]


# Lean's three standard (sound) axioms; a proof using only these is fully checked.
STANDARD_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})

_AXIOMS_RE = re.compile(r"depends on axioms:\s*\[([^\]]*)\]")
_NO_AXIOMS_RE = re.compile(r"does not depend on any axioms")


def parse_axioms(lean_output: str):
    """The set of axioms a ``#print axioms <decl>`` reported in ``lean_output``, or ``None`` if
    no such report is present (e.g. the declaration didn't exist)."""
    match = _AXIOMS_RE.search(lean_output)
    if match:
        return frozenset(
            name.strip() for name in match.group(1).split(",") if name.strip()
        )
    if _NO_AXIOMS_RE.search(lean_output):
        return frozenset()
    return None


def forbidden_axioms(lean_output: str, *, allowed=frozenset()):
    """Axioms the proof depends on that aren't allowed — i.e. outside Lean's standard sound
    axioms plus the problem's ``allowed`` set. ``sorryAx`` (left by ``sorry``/``admit``) is
    never standard, so it always shows up here. Returns ``None`` if there was no axiom report
    to check (caller decides how to treat that)."""
    used = parse_axioms(lean_output)
    if used is None:
        return None
    permitted = STANDARD_AXIOMS | frozenset(allowed)
    return frozenset(name for name in used if name not in permitted)
