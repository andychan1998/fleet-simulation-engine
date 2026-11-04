# Fleet Simulation Engine: Design

**Author:** Andy Chan \
**Date:** November 3, 2026

## Introduction

This project is a discrete-time simulation engine for a fleet of vessels.

- Vessels can travel between ports.
- A tick is the basic time unit.
- Ports and vessels can carry cargo. Each unit of cargo is identified by its destination port.
- The committed state is the world as of the last tick. The planned state is the last committed state plus new commands accepted since then. A tick makes the plan the new committed state.

Please consult the project document for the detailed operations. This document focuses on discussing design decisions, implementation, test coverage, performance, future enhancements, and AI usage.

## Design Decisions

### Questions Left to the Implementer

| ID | Question | Decision | Rationale |
|---|---|---|---|
| D1 | `set_destination` to the current port | Accepted; the vessel stays idle. | This provides a way to cancel a pending `set_destination` command. |
| D2 | `dock` after `dock` | Accepted; nothing changes. | The project document specifies that `dock` should be accepted if the committed state is idle. |
| D3 | `undock` after `dock` | Accepted; cancels the pending dock. | This provides a way to cancel a pending `dock`. |
| D4 | `undock` after `undock` | Accepted; nothing changes. | The project document specifies that `undock` should be accepted if the committed state is docked. |
| D5 | `dock` after `undock` | Accepted; cancels the pending undock. | This provides a way to cancel a pending `undock`. |
| D6 | `undock` after `load`/`unload`/`swap` | Accepted; the cargo moves and the vessel undocks in the same tick. | Commands are executed in order, so cargo operations are allowed before undocking. |
| D7 | `load`/`unload`/`swap` after `undock` | Rejected. | Commands are executed in order, so cargo operations are not allowed after undocking until a `dock` cancels the `undock`. This is stricter than what's specified in the project document. |
| D8 | `load` and `unload` the same cargo | Accepted; the operations cancel out and throughput only counts each vessel's net change. | Net change counts only the necessary work. |

### Other Questions That Came Up During Implementation

| ID | Question | Decision | Rationale |
|---|---|---|---|
| D9 | Loading cargo that another vessel unloaded in the same tick | Accepted. | Commands are executed in order, so a cargo unit just unloaded by a vessel can be loaded by another vessel in the same tick. |
| D10 | `swap` if only one half can succeed | Rejected; nothing changes. | A half-done swap isn't what the user asked for. |
| D11 | `swap` of cargo to the same destination | Accepted; always counts as a no-op. | Swapping a destination for itself can't change anything, so a no-op is the simplest rule for users. |

## Implementation

### Modules

- `__main__.py`: Entry point that reads from stdin or a file.
- `protocol.py`: Goes from JSON to Python dictionaries and vice versa. Any failure becomes an error response.
- `params.py`: Command parameter checker.
- `simulation.py`: Core engine that holds the state and runs each command against it.
- `model.py`: World, Vessel, and cargo counts.

One command's path through the code:

![How one command flows through the code](docs/command_flow.png)

### Implementation Choices

- Implemented in Python, standard library only, and tested with Python 3.9-3.14.
- Instead of keeping a running counter, dock throughput is calculated by comparing each docked vessel's cargo with its last committed state. This makes cancel-outs automatic.
- Vessel and World states are read-only. A new command builds a new state, copies only the vessels and ports it changes, and shares everything else. If the command fails, the new state is simply discarded. This partial-copy design makes per-command execution faster and also consumes less memory to keep history.

## Test Coverage

The tests below achieve 100% line and branch coverage, measured with coverage.py (see [README](README.md)). Mutation testing plants 454 bugs and catches 450 of them. The four uncaught "bugs" were checked manually to verify that they don't actually alter the program.

Every pull request and push triggers GitHub Actions, which runs the unit tests on both Linux and macOS for Python 3.9-3.14.

### Run by `python -m unittest`

| ID | Category | Location | Coverage |
|---|---|---|---|
| T1 | Project examples | [`tests/transcripts/spec/`](tests/transcripts/spec/) | Examples in the project document |
| T2 | Design decisions | [`tests/transcripts/decisions/`](tests/transcripts/decisions/) | Tests for the design decisions |
| T3 | Rules and edge cases | [`tests/transcripts/rules/`](tests/transcripts/rules/) | Rules and edge cases such as shared dock throughput, simultaneous arrivals, empty fleet, single port, large values, unusual vessel IDs |
| T4 | Command matrix | [`tests/test_command_matrix.py`](tests/test_command_matrix.py) | All 56 combinations of 8 vessel situations × 7 movement and cargo commands |
| T5 | Read-only history | [`tests/test_model.py`](tests/test_model.py) | Past states can't be changed, and unchanged vessels and ports are shared |
| T6 | Random stress test | [`tests/test_invariants.py`](tests/test_invariants.py) | About 20,000 random commands on small worlds, with rules checked after every command, such as every cargo unit is accounted for and no dock exceeds its throughput |
| T7 | Command line | [`tests/test_cli.py`](tests/test_cli.py) | Messy inputs, various output behaviors, and startup errors |
| T8 | Output order | [`tests/test_output_order.py`](tests/test_output_order.py) | Vessels are listed in `init` order |
| T9 | Unexpected error response | [`tests/test_protocol.py`](tests/test_protocol.py) | Correct handling of an unexpected internal bug |
| T10 | Tools | [`tests/test_tools.py`](tests/test_tools.py) | Verification of the demo, viewer tool, mutation tool, and benchmark tool |

### Run on Request

| ID | Category | Location | Coverage |
|---|---|---|---|
| T11 | Large world | [`tests/test_large_world.py`](tests/test_large_world.py) | 100 ports, 1,000 vessels, and about 4,000 commands, with rules checked after every command |
| T12 | Mutation | [`tools/mutation_test.py`](tools/mutation_test.py) | Planting bugs and checking that the tests successfully catch every one that can change program behavior |
| T13 | Benchmark | [`tools/benchmark.py`](tools/benchmark.py) | Speed per command and memory growth |

## Performance

The time complexity is O(n) per command, where n is the size of the world state (i.e., vessels + cargo entries).

The space complexity is O(t x n), where t is the number of ticks and n is the size of the world state.

In a test with 100 ports and 1,000 vessels (`python tools/benchmark.py --ports 100 --ships 1000`), it takes around 2 ms per command. Most of the time is spent on building the full-state response. Memory grows by about 62 KB per tick. This test was run on my MacBook Pro (Apple M1 Pro, Linux VM, Python 3.10).

## Future Enhancements

Some possible future enhancements:

- We can build the response for each vessel and port's cargo once and store it. Since most vessels and ports don't change between responses, this allows faster response time.
- Currently, each tick stores a table pointing to every vessel and port. We can reduce memory consumption by storing only the changes, but this requires each `get_state_at` to rebuild from past states.
- Features that make the simulation more realistic such as per-vessel cargo capacity limit, per-vessel travel speed, per-dock vessel limit, and cargo with deadlines.

## AI Usage

Claude was used to write the code, tests, tools, README, and presentation. I made all the design decisions and reviewed everything Claude produced. This design document was written manually, with AI used only for grammar, image generation, research, and feedback.
