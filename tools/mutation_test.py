#!/usr/bin/env python3
"""Mutation testing: checks that the tests would notice bugs in the engine.

    python tools/mutation_test.py                            # every engine file (about 10 minutes)
    python tools/mutation_test.py --file fleetsim/model.py   # just one file, faster
    python tools/mutation_test.py --only 12,47               # re-check specific changes by id

It makes one small change at a time to the engine's code (flips a comparison,
changes a number, deletes a line, forces an `if` to always or never run),
runs the whole test suite against that changed copy, and records whether any
test failed. A change that no test notices is listed with its file, line and
id: either a gap in the tests, or a change that can't alter what the program
does. Changes of the second kind were checked by hand and are listed in
KNOWN_HARMLESS, so they're reported separately.

Your files are never modified: each change is made in a temporary copy of the
project. Uses only the standard library.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ENGINE_FILES = [
    "fleetsim/__init__.py",
    "fleetsim/__main__.py",
    "fleetsim/model.py",
    "fleetsim/params.py",
    "fleetsim/protocol.py",
    "fleetsim/simulation.py",
]

# What gets copied for each run: everything the test suite needs.
PROJECT_PARTS = ["fleetsim", "tests", "tools", "examples", "pyproject.toml"]

# Changes no test can catch, because they can't change what the program does.
# Each was checked by hand. They're matched by file, function, change and code
# (not line number), so edits elsewhere in a file don't break the match.
KNOWN_HARMLESS = [
    {
        "file": "fleetsim/__init__.py", "function": "<module>", "change": "< -> <=",
        "code": "if sys.version_info < (3, 9):",
        "why": "real version numbers have five parts, so none is exactly (3, 9)",
    },
    {
        "file": "fleetsim/simulation.py", "function": "_commit", "change": "condition always True",
        "code": "if vessel.state == DOCKED:",
        "why": "only docked ships can have cargo changes pending, so listing idle ships too adds 0 operations",
    },
    {
        "file": "fleetsim/model.py", "function": "transfer_ops", "change": "> -> >=",
        "code": "if change > 0:",
        "why": "the two branches differ only when change is 0, and then both add 0",
    },
    {
        "file": "fleetsim/params.py", "function": "check_object", "change": "line deleted",
        "code": "return value",
        "why": "callers use check_object only for its check and ignore what it returns",
    },
]

# ---- the kinds of change ----

SWAPS = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.In: ast.NotIn, ast.NotIn: ast.In,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
    ast.And: ast.Or, ast.Or: ast.And, ast.Add: ast.Sub, ast.Sub: ast.Add,
}
SYMBOLS = {
    ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!=",
    ast.In: "in", ast.NotIn: "not in", ast.Is: "is", ast.IsNot: "is not",
    ast.And: "and", ast.Or: "or", ast.Add: "+", ast.Sub: "-",
}


def swap_label(op_type) -> str:
    return f"{SYMBOLS[op_type]} -> {SYMBOLS[SWAPS[op_type]]}"


def possible_changes(node: ast.AST, skip: set[int]):
    """Yield (label, apply) for every small change that can be made at this node.

    `apply(node, parent_of)` changes the node in place. The code is handled as
    Python itself reads it (a tree of pieces), so every change is still valid Python.
    """
    if id(node) in skip:
        return
    if isinstance(node, ast.Compare):
        for i, op in enumerate(node.ops):
            if type(op) in SWAPS:
                def apply(n, parent_of, i=i):
                    n.ops[i] = SWAPS[type(n.ops[i])]()
                yield swap_label(type(op)), apply
    if isinstance(node, (ast.BoolOp, ast.BinOp, ast.AugAssign)) and type(node.op) in SWAPS:
        def apply(n, parent_of):
            n.op = SWAPS[type(n.op)]()
        yield swap_label(type(node.op)), apply
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        def apply(n, parent_of):
            replace(parent_of, n, n.operand)
        yield "`not` removed", apply
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            def apply(n, parent_of):
                n.value = not n.value
            yield f"{node.value} -> {not node.value}", apply
        elif isinstance(node.value, int):
            def apply(n, parent_of):
                n.value += 1
            yield f"{node.value} -> {node.value + 1}", apply
    if isinstance(node, (ast.If, ast.While, ast.IfExp)):
        for forced in (True, False):
            def apply(n, parent_of, forced=forced):
                n.test = ast.Constant(forced)
            yield f"condition always {forced}", apply
    if isinstance(node, ast.comprehension):
        for i in range(len(node.ifs)):
            def apply(n, parent_of, i=i):
                n.ifs[i] = ast.Constant(True)
            yield "filter removed", apply
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("max", "min"):
        other = "min" if node.func.id == "max" else "max"
        def apply(n, parent_of, other=other):
            n.func.id = other
        yield f"{node.func.id} -> {other}", apply
    if isinstance(node, (ast.Expr, ast.Assign, ast.AugAssign, ast.Raise, ast.Return, ast.Delete)):
        def apply(n, parent_of):
            replace(parent_of, n, ast.Pass())
        yield "line deleted", apply


# ---- reading the code ----

def skipped_nodes(tree: ast.AST) -> set[int]:
    """Parts not worth changing: docstrings, imports, type hints, __all__."""
    skip: set[int] = set()

    def skip_all(node):
        skip.update(id(n) for n in ast.walk(node))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                skip_all(first)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            skip_all(node)
        if isinstance(node, ast.AnnAssign):
            skip_all(node.annotation)
        if isinstance(node, ast.arg) and node.annotation is not None:
            skip_all(node.annotation)
        if isinstance(node, ast.FunctionDef) and node.returns is not None:
            skip_all(node.returns)
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "__all__" for t in node.targets):
            skip_all(node)
    return skip


def function_names(tree: ast.AST) -> dict[int, str]:
    """For each node, the function it's in ('<module>' outside any function)."""
    names: dict[int, str] = {}

    def visit(node, current):
        names[id(node)] = current
        inner = node.name if isinstance(node, ast.FunctionDef) else current
        for child in ast.iter_child_nodes(node):
            visit(child, inner)

    visit(tree, "<module>")
    return names


def parents(tree: ast.AST) -> dict[int, tuple]:
    """For each node: (parent, field name, index in that field or None)."""
    parent_of = {}
    for parent in ast.walk(tree):
        for field, value in ast.iter_fields(parent):
            if isinstance(value, list):
                for i, child in enumerate(value):
                    if isinstance(child, ast.AST):
                        parent_of[id(child)] = (parent, field, i)
            elif isinstance(value, ast.AST):
                parent_of[id(value)] = (parent, field, None)
    return parent_of


def replace(parent_of, old: ast.AST, new: ast.AST) -> None:
    parent, field, index = parent_of[id(old)]
    if index is None:
        setattr(parent, field, new)
    else:
        getattr(parent, field)[index] = new


def find_changes(repo: Path, file: str) -> list[dict]:
    """Every change the tool can make in one file, in a fixed order."""
    source = (repo / file).read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    skip = skipped_nodes(tree)
    functions = function_names(tree)
    changes = []
    for index, node in enumerate(ast.walk(tree)):
        for k, (label, _) in enumerate(possible_changes(node, skip)):
            line = getattr(node, "lineno", 0)
            changes.append({
                "file": file, "node": index, "k": k, "line": line,
                "function": functions[id(node)], "change": label,
                "code": lines[line - 1].strip() if line else "",
            })
    return changes


def apply_change(repo: Path, change: dict) -> str:
    """The file's source code with this one change made."""
    tree = ast.parse((repo / change["file"]).read_text(encoding="utf-8"))
    node = list(ast.walk(tree))[change["node"]]
    _, apply = list(possible_changes(node, skipped_nodes(tree)))[change["k"]]
    apply(node, parents(tree))
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


# ---- running the tests ----

def run_suite(repo: Path, file: str | None = None, source: str | None = None, timeout: int = 600):
    """Copy the project, make the change (if any), run all tests. Returns (passed, seconds)."""
    work = Path(tempfile.mkdtemp(prefix="fleetsim-mutant-"))
    try:
        for part in PROJECT_PARTS:
            src = repo / part
            if src.is_dir():
                shutil.copytree(src, work / part, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            elif src.exists():
                shutil.copy2(src, work / part)
        if file is not None:
            (work / file).write_text(source, encoding="utf-8")
        # A shorter stress test keeps each run to a couple of seconds.
        env = {**os.environ, "FLEETSIM_SCENARIOS": "10", "PYTHONDONTWRITEBYTECODE": "1"}
        start = time.time()
        try:
            result = subprocess.run(
                [sys.executable, "-m", "unittest", "-q"], cwd=work, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout,
            )
            passed = result.returncode == 0
        except subprocess.TimeoutExpired:
            passed = False  # a change that makes the tests hang counts as caught
        return passed, time.time() - start
    finally:
        shutil.rmtree(work, ignore_errors=True)


def is_known_harmless(change: dict) -> bool:
    return any(all(change[key] == known[key] for key in ("file", "function", "change", "code"))
               for known in KNOWN_HARMLESS)


def why_harmless(change: dict) -> str:
    for known in KNOWN_HARMLESS:
        if all(change[key] == known[key] for key in ("file", "function", "change", "code")):
            return known["why"]
    return ""


def where(change: dict) -> str:
    return f"{change['file']}:{change['line']} ({change['function']})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", choices=ENGINE_FILES, help="only change this engine file")
    parser.add_argument("--only", help="comma-separated change ids to try (ids are shown in the report)")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 2,
                        help="changes to try at the same time (default: number of CPU cores)")
    parser.add_argument("--out", type=Path, help="also save every result as JSON")
    args = parser.parse_args(argv)

    passed, baseline_seconds = run_suite(REPO_ROOT)
    if not passed:
        print("The tests fail even without any change; fix that first.", file=sys.stderr)
        return 2

    changes = [change for file in ENGINE_FILES for change in find_changes(REPO_ROOT, file)]
    for change_id, change in enumerate(changes):
        change["id"] = change_id
    for known in KNOWN_HARMLESS:
        if not any(is_known_harmless(c) and c["code"] == known["code"] and c["file"] == known["file"]
                   for c in changes):
            print(f"Note: the hand-checked entry for {known['file']} ({known['code']}) no longer "
                  "matches the code; it can be removed from KNOWN_HARMLESS.", flush=True)
    selected = changes
    if args.file:
        selected = [c for c in selected if c["file"] == args.file]
    if args.only:
        wanted = {int(x) for x in args.only.split(",")}
        selected = [c for c in selected if c["id"] in wanted]
    print(f"Tests pass without changes ({baseline_seconds:.1f}s). "
          f"Trying {len(selected)} changes, {args.jobs} at a time...", flush=True)

    timeout = max(60, int(baseline_seconds * 10))

    def try_change(change):
        tests_passed, _ = run_suite(REPO_ROOT, change["file"], apply_change(REPO_ROOT, change), timeout)
        return change, tests_passed

    missed = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for done, (change, tests_passed) in enumerate(pool.map(try_change, selected), start=1):
            change["caught"] = not tests_passed
            if tests_passed:
                missed.append(change)
            if done % 50 == 0 or done == len(selected):
                print(f"  {done}/{len(selected)} tried, {len(missed)} not caught so far", flush=True)

    harmless = [c for c in missed if is_known_harmless(c)]
    gaps = [c for c in missed if not is_known_harmless(c)]
    print(f"\nCaught {len(selected) - len(missed)} of {len(selected)} changes.")
    if harmless:
        print(f"\n{len(harmless)} not caught, but checked by hand as unable to change behavior:")
        for c in harmless:
            print(f"  {where(c)}  {c['change']}: {c['code']}")
            print(f"      {why_harmless(c)}")
    if gaps:
        print(f"\n{len(gaps)} not caught: possible gaps in the tests.")
        for c in gaps:
            print(f"  #{c['id']:<4} {where(c)}  {c['change']}: {c['code']}")
        print("\nAfter adding tests, re-check just these with:  --only " + ",".join(str(c["id"]) for c in gaps))
    else:
        print("\nNo gaps: every change that alters behavior was caught by a test.")
    if args.out:
        args.out.write_text(json.dumps(selected, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
