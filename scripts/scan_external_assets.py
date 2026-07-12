#!/usr/bin/env python3
"""
Scan templates/static for unsafe external assets (supply-chain guardrail).

Motivated by the polyfill.io CDN attack: a third-party script loaded without
Subresource Integrity (SRI) can be silently swapped for malware. This scanner
flags, and exits non-zero on:

1. References to known-malicious / high-risk CDN hosts (documented supply-chain
   incidents) anywhere in a file -- including inline JS.
2. External <script>/<link rel=stylesheet> hosts that are not on the allowlist,
   so a new CDN can't be added without review.
3. External <script>/<link rel=stylesheet> tags loaded without an `integrity`
   (SRI) attribute.

Usage:
    python scripts/scan_external_assets.py [PATH ...] [--sarif OUT.sarif]

With no PATH args it walks the default roots (templates/, static/). pre-commit
passes the changed files as PATH args. Optionally writes a SARIF 2.1.0 report so
CI can surface findings as GitHub code-scanning alerts.

Exit codes: 0 = clean, 1 = findings, 2 = usage error.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOTS = ["templates", "static"]
SCAN_SUFFIXES = {".html", ".htm", ".js"}

# Hosts known to have served malware / been compromised in supply-chain attacks.
# Seeded from public incidents; extend as new ones are reported.
KNOWN_MALICIOUS_HOSTS = {
    "polyfill.io",
    "cdn.polyfill.io",
    "polyfill-fastly.io",
    "polyfill-fastly.net",
    "bootcss.com",
    "bootcdn.net",
    "bootcdn.com",
    "staticfile.org",
    "staticfile.net",
    "unionadjs.com",
    "newcrtb.com",
}

# External hosts we trust to serve assets. Same-origin / {% static %} is implicitly allowed.
ALLOWED_HOSTS = {
    "cdnjs.cloudflare.com",
}

# Tags that load executable/style resources we want SRI on.
TAG_RE = re.compile(
    r"<(?P<tag>script|link)\b(?P<attrs>[^>]*)>", re.IGNORECASE | re.DOTALL
)
ATTR_RE = re.compile(
    r"""(?P<name>[a-zA-Z][\w-]*)\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""",
    re.DOTALL,
)
# Any absolute or protocol-relative URL, for the denylist sweep.
URL_RE = re.compile(r"""(?P<url>(?:https?:)?//[^\s"'<>)]+)""", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    rule: str
    level: str
    path: str
    line: int
    message: str


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _host_matches(host: str, host_set: set[str]) -> bool:
    host = host.lower().split(":")[0]
    return any(host == h or host.endswith("." + h) for h in host_set)


def _is_external(url: str) -> bool:
    return (
        url.startswith("http://") or url.startswith("https://") or url.startswith("//")
    )


def _host_of(url: str) -> str:
    if url.startswith("//"):
        url = "https:" + url
    return (urlparse(url).hostname or "").lower()


def _parse_attrs(attrs: str) -> dict[str, str]:
    return {m.group("name").lower(): m.group("value") for m in ATTR_RE.finditer(attrs)}


def scan_text(rel_path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []

    # 1. Denylist sweep across the whole file (catches inline JS, fetch(), etc.).
    for m in URL_RE.finditer(text):
        host = _host_of(m.group("url"))
        if host and _host_matches(host, KNOWN_MALICIOUS_HOSTS):
            findings.append(
                Finding(
                    rule="malicious-cdn-host",
                    level="error",
                    path=rel_path,
                    line=_line_of(text, m.start()),
                    message=f"Reference to known-malicious CDN host '{host}'. Remove it immediately.",
                )
            )

    # 2 & 3. Asset tags: allowlist + SRI enforcement.
    for m in TAG_RE.finditer(text):
        tag = m.group("tag").lower()
        attrs = _parse_attrs(m.group("attrs"))
        url = attrs.get("src") if tag == "script" else attrs.get("href")
        if not url or not _is_external(url):
            continue
        if tag == "link" and "stylesheet" not in attrs.get("rel", "").lower():
            continue  # only stylesheets among <link> need SRI

        line = _line_of(text, m.start())
        host = _host_of(url)
        if host and _host_matches(host, KNOWN_MALICIOUS_HOSTS):
            continue  # already reported by the denylist sweep
        if not _host_matches(host, ALLOWED_HOSTS):
            findings.append(
                Finding(
                    rule="external-host-not-allowlisted",
                    level="error",
                    path=rel_path,
                    line=line,
                    message=(
                        f"External {tag} loads from '{host}', which is not in ALLOWED_HOSTS. "
                        "Add it to the allowlist after review, or self-host the asset."
                    ),
                )
            )
        if "integrity" not in attrs:
            findings.append(
                Finding(
                    rule="missing-sri",
                    level="error",
                    path=rel_path,
                    line=line,
                    message=(
                        f"External {tag} from '{host}' has no Subresource Integrity (integrity=) hash. "
                        "Add integrity + crossorigin so the browser rejects tampered files."
                    ),
                )
            )

    return findings


def iter_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    targets = (
        [Path(p) for p in paths] if paths else [REPO_ROOT / r for r in DEFAULT_ROOTS]
    )
    for target in targets:
        if target.is_dir():
            files.extend(
                p for p in target.rglob("*") if p.suffix.lower() in SCAN_SUFFIXES
            )
        elif target.is_file() and target.suffix.lower() in SCAN_SUFFIXES:
            files.append(target)
    return files


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def to_sarif(findings: list[Finding]) -> dict:
    rules = {
        "malicious-cdn-host": "Reference to a known-malicious CDN host",
        "external-host-not-allowlisted": "External asset host is not on the allowlist",
        "missing-sri": "External asset loaded without Subresource Integrity",
    }
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "scan-external-assets",
                        "informationUri": "https://github.com/samlowe106/Pisa",
                        "rules": [
                            {"id": rid, "shortDescription": {"text": desc}}
                            for rid, desc in rules.items()
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": f.rule,
                        "level": f.level,
                        "message": {"text": f.message},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": f.path},
                                    "region": {"startLine": f.line},
                                }
                            }
                        ],
                    }
                    for f in findings
                ],
            }
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan templates/static for unsafe external assets."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files or directories to scan (default: templates/, static/).",
    )
    parser.add_argument(
        "--sarif", metavar="OUT", help="Write a SARIF 2.1.0 report to this path."
    )
    args = parser.parse_args(argv)

    findings: list[Finding] = []
    for path in iter_files(args.paths):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"warning: could not read {path}: {exc}", file=sys.stderr)
            continue
        findings.extend(scan_text(_rel(path), text))

    if args.sarif:
        Path(args.sarif).write_text(
            json.dumps(to_sarif(findings), indent=2), encoding="utf-8"
        )

    if findings:
        print(f"Unsafe external assets found ({len(findings)}):\n")
        for f in sorted(findings, key=lambda x: (x.path, x.line, x.rule)):
            print(f"  {f.path}:{f.line} [{f.rule}] {f.message}")
        print(
            "\nFix the issues above (remove the host, add it to the allowlist, or add an SRI hash)."
        )
        return 1

    print("OK: no unsafe external assets found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
