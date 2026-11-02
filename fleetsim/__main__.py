"""Command-line entry point.

    python -m fleetsim < commands.jsonl     read commands from stdin
    python -m fleetsim commands.jsonl       read commands from a file
"""

from __future__ import annotations

import os
import sys

from fleetsim.protocol import run


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) > 1:
        print("usage: python -m fleetsim [COMMANDS_FILE]", file=sys.stderr)
        return 2
    # utf-8-sig skips the invisible byte-order mark some editors put at the
    # start of a file. errors="replace" turns an invalid byte into a character
    # that fails JSON parsing, so it gets an error response instead of a crash.
    try:
        if args:
            try:
                f = open(args[0], encoding="utf-8-sig", errors="replace")
            except OSError as exc:
                print(f"fleetsim: cannot open {args[0]}: {exc.strerror}", file=sys.stderr)
                return 2
            with f:
                run(f, sys.stdout)
        else:
            if hasattr(sys.stdin, "reconfigure"):
                sys.stdin.reconfigure(encoding="utf-8-sig", errors="replace")
            run(sys.stdin, sys.stdout)
    except BrokenPipeError:
        # Whoever was reading our output stopped early (e.g. `| head`).
        # Exit quietly, like other command-line tools. Pointing stdout at
        # /dev/null stops Python from failing again when it flushes at exit.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
