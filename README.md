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

## Test

```
python -m unittest                      # everything
python -m unittest -k spec              # just the spec examples
```

Most tests are transcripts in `tests/transcripts/`: a command (`>`) followed by its expected response (`<`). To add one, drop a new `.txt` file in that folder.
