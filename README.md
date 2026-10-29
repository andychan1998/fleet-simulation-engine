# fleet-simulation-engine

Discrete-time fleet simulation engine in Python: ships, docks, and cargo driven by JSON commands over stdin/stdout, with tick-by-tick history.

## Setup

Requires Python 3.10+. No third-party packages.

```
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # nothing to install; kept for the standard workflow
```

## Run

```
python -m fleetsim < commands.jsonl     # commands on stdin, one JSON object per line
python -m fleetsim commands.jsonl       # or from a file
```

Each command line gets exactly one response line. Blank lines and lines starting with `#` are skipped, so scenario files can have comments.

## Demo

```
python tools/view.py examples/demo.jsonl          # readable, tick-by-tick view
python tools/view.py --step examples/demo.jsonl   # press Enter between commands
```

`tools/view.py` runs a scenario through the engine and formats its output; the engine's output itself is unchanged.

## Test

```
python -m unittest                      # everything (a few seconds)
python -m unittest -k spec              # just the examples from the spec
python -m unittest -k decisions         # just the design-decision tests
```

The tests come in three layers:

| Layer | Where | What it shows |
|---|---|---|
| Spec examples | `tests/transcripts/spec/` | Every example in the spec, run exactly |
| Decisions and rules | `tests/transcripts/decisions/`, `tests/transcripts/rules/` | One test per design decision and per tricky rule |
| Random stress test | `tests/test_invariants.py` | Thousands of random commands; checks rules that must always hold (cargo conserved, throughput never exceeded, rejected commands change nothing, history matches) |

Transcripts are plain text: a command (`>`) followed by its expected response (`<`, or `<~` to check only part of it). To add a test, drop a new `.txt` file in `tests/transcripts/`.

The stress test prints a seed on failure; replay it with `FLEETSIM_SEED=<seed> python -m unittest tests.test_invariants`.

## Layout

```
fleetsim/
  __main__.py     command-line entry point
  protocol.py     JSON line in, JSON line out; errors become error responses
  simulation.py   one handler per command, plus the tick logic
  model.py        ships, docks, cargo counts, and the world state
  params.py       parameter checking and error messages
tests/            transcripts, stress test, command-line tests
tools/view.py     demo viewer
examples/         demo scenario
```

## Stages

Each stage of the problem is tagged in git, and each tag passes that stage's spec examples:

- `v1-base`: base vessel simulation
- `v2-cargo`: cargo and dock operations
- `v3-history`: historical state queries
