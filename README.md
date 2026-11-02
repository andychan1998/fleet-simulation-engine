# fleet-simulation-engine

[![tests](https://github.com/andychan1998/fleet-simulation-engine/actions/workflows/test.yml/badge.svg)](https://github.com/andychan1998/fleet-simulation-engine/actions/workflows/test.yml)

Discrete-time fleet simulation engine in Python: ships, docks, and cargo driven by JSON commands over stdin/stdout, with tick-by-tick history.

## Setup

Requires Python 3.9+.

These steps are optional: the project uses only the standard library, so there is nothing to install. They are the usual way to keep a project's Python separate from the rest of your system. If you skip them, use `python3` wherever this README says `python`.

```
python3 -m venv .venv            # once: creates the virtual environment in .venv/
source .venv/bin/activate        # in each new terminal (Windows: .venv\Scripts\activate)
pip install -r requirements.txt  # nothing to install; kept for the standard workflow
```

## Run

The engine reads commands, one JSON object per line, and writes one JSON response line per command.

```
python -m fleetsim examples/demo.jsonl      # run a scenario file
python -m fleetsim < examples/demo.jsonl    # same thing, through stdin
```

Or start it with no file and type commands yourself. Each response prints right away:

```
python -m fleetsim
{"command":"init","parameters":{"travel_times":[[0,2],[2,0]],"dock_throughput":[1,1],"vessels":[{"id":"v1","location":0}]}}
{"command":"set_destination","parameters":{"vessel_id":"v1","destination":1}}
{"command":"tick","parameters":{}}
```

To exit, press Ctrl-D on an empty line (Windows: Ctrl-Z, then Enter).

Blank lines and lines starting with `#` are skipped, so scenario files can have comments.

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

The tests come in four layers:

| Layer | Where | What it shows |
|---|---|---|
| Spec examples | `tests/transcripts/spec/` | Every example in the spec, run exactly |
| Decisions and rules | `tests/transcripts/decisions/`, `tests/transcripts/rules/` | One test per design decision, tricky rule and edge case |
| Command matrix | `tests/test_command_matrix.py` | Every movement and cargo command in every ship situation (56 cases), as one table |
| Random stress test | `tests/test_invariants.py` | Thousands of random commands; checks rules that must always hold (cargo conserved, throughput never exceeded, rejected commands change nothing, history matches) |

Transcripts are plain text: a command (`>`) followed by its expected response (`<`, or `<~` to check only part of it). To add a test, drop a new `.txt` file in `tests/transcripts/`.

The stress test prints a seed on failure; replay it with `FLEETSIM_SEED=<seed> python -m unittest tests.test_invariants`.

### Large world (on request)

```
FLEETSIM_LARGE=1 python -m unittest tests.test_large_world -v    # about a minute
```

Skipped in the normal run. It drives a random world of 100 ports and 1,000 ships for 20 ticks (about 4,000 commands), checks the same kinds of rules as the stress test, and prints the engine's speed per command type and peak memory. Change the size with `FLEETSIM_LARGE_PORTS`, `FLEETSIM_LARGE_SHIPS`, `FLEETSIM_LARGE_TICKS` and `FLEETSIM_LARGE_COMMANDS_PER_TICK`.

### Speed and memory

```
python tools/benchmark.py                                   # standard sizes, one to two minutes
python tools/benchmark.py speed --ports 50 --ships 1000     # one world size
python tools/benchmark.py memory --ports 50 --ships 1000 --ticks 1000
```

Times the engine alone (no file reading or writing) at several world sizes: commands that change state, reads, and ticks. Memory is how much the process grows over many ticks, which is almost all history.

### Checking the tests themselves

```
python tools/mutation_test.py                            # every engine file, about 10 minutes
python tools/mutation_test.py --file fleetsim/model.py   # one file, faster
```

This mutation test makes one small change at a time to the engine (flips a comparison, changes a number, deletes a line, forces an `if`), runs the whole test suite against each changed copy, and lists any change that no test caught. Three changes can't alter what the program does, so no test can catch them; they were checked by hand and are reported separately. When this was written, every other change was caught. Your files are never modified: each change is made in a temporary copy.

## Layout

```
fleetsim/
  __main__.py     command-line entry point
  protocol.py     JSON line in, JSON line out; errors become error responses
  simulation.py   one handler per command, plus the tick logic
  model.py        ships, docks, cargo counts, and the world state
  params.py       parameter checking and error messages
tests/                   transcripts, command matrix, stress and large-world tests, command-line tests
tools/view.py            demo viewer
tools/mutation_test.py   checks that the tests catch injected bugs
tools/benchmark.py       measures speed and memory
examples/                demo scenario
```

## Stages

Each stage of the problem is tagged in git, and each tag passes that stage's spec examples:

- `v1-base`: base vessel simulation
- `v2-cargo`: cargo and dock operations
- `v3-history`: historical state queries
