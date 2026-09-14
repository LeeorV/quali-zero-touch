#!/usr/bin/env python3
"""Validate this plugin against the invariants documented in AGENTS.md.

These are the rules that Claude Cowork's uploader enforces silently: a violation
is rejected with a generic "validation error" that names neither the skill nor
the rule. `claude plugin validate` does not catch them either. So we check them
here, in CI and pre-commit, where the failure can actually say what broke.

Usage:
    python3 scripts/validate_plugin.py            # human-readable report
    python3 scripts/validate_plugin.py --quiet    # errors/warnings only

Exit status is 1 if any ERROR was found, 0 otherwise (warnings do not fail).
Requires PyYAML: descriptions must be measured after YAML folding/quoting, so a
regex over the raw text would give the wrong length.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    print("error: PyYAML is required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Cowork rejects the whole plugin at 1025+. Warn early so that a rename which
# lengthens a description cannot silently consume the last few characters —
# this is exactly how blueprint-review broke (1021 -> 1025).
DESC_MAX = 1024
DESC_WARN = 950

# The API helper is the one place allowed to speak HTTP; every other skill must
# route through it (AGENTS.md, "Avoid").
HTTP_EXEMPT_SKILLS = {"zero-touch-api"}
RAW_HTTP = re.compile(
    r"(?:^|[^\w-])(curl\s+-|import\s+requests|import\s+urllib|requests\.(?:get|post|put|delete)|urllib\.request)"
)

# Obvious committed-credential shapes. Kept in sync with pack.sh's pre-zip scan.
SECRETS = re.compile(r"(eyJ[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,})")

SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
PLUGIN_ROOT_REF = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)")

ERROR, WARN = "ERROR", "WARN"


class Report:
    def __init__(self) -> None:
        self.items: List[Tuple[str, str, str]] = []

    def error(self, where: str, msg: str) -> None:
        self.items.append((ERROR, where, msg))

    def warn(self, where: str, msg: str) -> None:
        self.items.append((WARN, where, msg))

    @property
    def errors(self) -> List[Tuple[str, str, str]]:
        return [i for i in self.items if i[0] == ERROR]

    @property
    def warnings(self) -> List[Tuple[str, str, str]]:
        return [i for i in self.items if i[0] == WARN]


def rel(path: str) -> str:
    return os.path.relpath(path, ROOT)


def parse_frontmatter(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return (frontmatter_dict, error_message)."""
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    if not m:
        return None, "no YAML frontmatter block (must start with '---' on line 1)"
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        detail = str(e).replace("\n", " ")
        return None, (
            f"frontmatter is not valid YAML: {detail}. "
            "A common cause is an unquoted multi-bracket argument-hint — "
            'write argument-hint: "[env] [workflow]".'
        )
    if not isinstance(data, dict):
        return None, "frontmatter did not parse to a mapping"
    return data, None


def check_skills(rep: Report) -> List[str]:
    skills_dir = os.path.join(ROOT, "skills")
    names = sorted(
        d for d in os.listdir(skills_dir) if os.path.isdir(os.path.join(skills_dir, d))
    )
    for name in names:
        path = os.path.join(skills_dir, name, "SKILL.md")
        where = rel(path)
        if not os.path.isfile(path):
            rep.error(f"skills/{name}", "missing SKILL.md")
            continue
        text = open(path, encoding="utf-8").read()

        fm, err = parse_frontmatter(text)
        if err:
            rep.error(where, err)
            continue

        # name must equal the folder name
        if fm.get("name") != name:
            rep.error(
                where, f"frontmatter name is {fm.get('name')!r} but the folder is {name!r}"
            )

        # description: present, and within the hard cap
        desc = fm.get("description")
        if not desc or not str(desc).strip():
            rep.error(where, "description is missing or empty (it drives skill invocation)")
        else:
            n = len(str(desc))
            if n > DESC_MAX:
                rep.error(
                    where,
                    f"description is {n} chars, over the {DESC_MAX} cap by {n - DESC_MAX}. "
                    "Cowork will reject the entire plugin. Trim filler words, not trigger phrases.",
                )
            elif n > DESC_WARN:
                rep.warn(
                    where,
                    f"description is {n} chars, only {DESC_MAX - n} under the cap. "
                    "A rename that lengthens a referenced name could push it over.",
                )

        # argument-hint: a list is a bare [token]; a string should still look like one
        hint = fm.get("argument-hint")
        if hint is not None and isinstance(hint, str) and "[" not in hint:
            rep.warn(
                where,
                f"argument-hint {hint!r} is prose; convention is a bracket token like [path].",
            )

        # raw HTTP outside the API helper
        if name not in HTTP_EXEMPT_SKILLS:
            for i, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith((">", "#")):
                    continue
                if RAW_HTTP.search(line):
                    rep.error(
                        f"{where}:{i}",
                        "calls HTTP directly; route Torque calls through "
                        "skills/zero-touch-api/scripts/ instead.",
                    )
                    break
    return names


def check_readme(rep: Report, skill_names: List[str]) -> None:
    readme = os.path.join(ROOT, "README.md")
    text = open(readme, encoding="utf-8").read()
    for name in skill_names:
        if name not in text:
            rep.error(
                "README.md",
                f"skill {name!r} is not documented. The README is the only user-facing "
                "inventory — add it to the Skills or Commands table.",
            )


def check_plugin_root_refs(rep: Report) -> None:
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "skills")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            path = os.path.join(dirpath, fn)
            for i, line in enumerate(
                open(path, encoding="utf-8").read().splitlines(), 1
            ):
                for ref in PLUGIN_ROOT_REF.findall(line):
                    if "..." in ref:  # prose placeholder, not a real path
                        continue
                    if not os.path.exists(os.path.join(ROOT, ref)):
                        rep.error(
                            f"{rel(path)}:{i}",
                            f"${{CLAUDE_PLUGIN_ROOT}}/{ref} does not exist "
                            "(stale path after a rename?)",
                        )


def check_manifests(rep: Report) -> None:
    pj_path = os.path.join(ROOT, ".claude-plugin", "plugin.json")
    mp_path = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
    try:
        pj = json.load(open(pj_path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        rep.error(rel(pj_path), f"cannot parse: {e}")
        return

    for key in ("name", "version", "description"):
        if not pj.get(key):
            rep.error(rel(pj_path), f"missing required key {key!r}")

    version = str(pj.get("version", ""))
    if version and not SEMVER.match(version):
        rep.error(
            rel(pj_path),
            f"version {version!r} is not semver; release.yml tags releases as v<version>.",
        )

    if not os.path.isfile(mp_path):
        return
    try:
        mp = json.load(open(mp_path, encoding="utf-8"))
    except json.JSONDecodeError as e:
        rep.error(rel(mp_path), f"cannot parse: {e}")
        return

    entries = mp.get("plugins", [])
    match = next((e for e in entries if e.get("name") == pj.get("name")), None)
    if match is None:
        rep.error(
            rel(mp_path),
            f"no plugins[] entry named {pj.get('name')!r} to match plugin.json.",
        )
    elif match.get("license") and pj.get("license") and match["license"] != pj["license"]:
        rep.error(
            rel(mp_path),
            f"license {match['license']!r} disagrees with plugin.json "
            f"({pj['license']!r}). Keep both in sync with the LICENSE file.",
        )


def check_secrets(rep: Report) -> None:
    for sub in ("skills", ".claude-plugin"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, sub)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                path = os.path.join(dirpath, fn)
                try:
                    text = open(path, encoding="utf-8").read()
                except (OSError, UnicodeDecodeError):
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    if SECRETS.search(line):
                        rep.error(
                            f"{rel(path)}:{i}",
                            "looks like a committed credential. Never commit a "
                            "TORQUE_API_TOKEN value.",
                        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true", help="suppress the OK summary line")
    args = ap.parse_args()

    rep = Report()
    skill_names = check_skills(rep)
    check_readme(rep, skill_names)
    check_plugin_root_refs(rep)
    check_manifests(rep)
    check_secrets(rep)

    in_ci = bool(os.environ.get("GITHUB_ACTIONS"))
    for level, where, msg in rep.items:
        if in_ci:
            kind = "error" if level == ERROR else "warning"
            file_part = where.split(":")[0]
            line_part = where.split(":")[1] if ":" in where else ""
            loc = f"file={file_part}" + (f",line={line_part}" if line_part.isdigit() else "")
            print(f"::{kind} {loc}::{where}: {msg}")
        else:
            print(f"{level:5} {where}: {msg}")

    if rep.errors:
        print(
            f"\n{len(rep.errors)} error(s), {len(rep.warnings)} warning(s) "
            f"across {len(skill_names)} skills.",
            file=sys.stderr,
        )
        return 1
    if not args.quiet:
        print(
            f"OK — {len(skill_names)} skills validated, "
            f"{len(rep.warnings)} warning(s)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
