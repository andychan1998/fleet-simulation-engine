"""Command-line entry point.

    python -m fleetsim < commands.jsonl     read commands from stdin
    python -m fleetsim commands.jsonl       read commands from a file
"""

import sys

from fleetsim.protocol import run


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) > 1:
        print("usage: python -m fleetsim [COMMANDS_FILE]", file=sys.stderr)
        return 2
    if args:
        with open(args[0], encoding="utf-8") as f:
            run(f, sys.stdout)
    else:
        run(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
