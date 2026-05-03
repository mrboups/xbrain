# spike-mem0 — 1-day decision spike

**Scope** : 1 day max. **Delete after Plan 02-03 starts.**

Goal : decide GO mem0 / NO-GO mem0 based on 4 empirical tests.

## Setup local

```bash
# Backends (terminal 1)
docker run -d --name spike-pg \
  -p 5432:5432 \
  -e POSTGRES_PASSWORD=spike \
  -e POSTGRES_USER=spike \
  -e POSTGRES_DB=spike \
  postgres:17

docker run -d --name spike-qdrant \
  -p 6333:6333 \
  qdrant/qdrant:v1.17.1
```

## Run spike (terminal 2)

```bash
cd apps/spike-mem0
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate
pip install -e .

export OPENAI_API_KEY=sk-...   # for mem0 default embedder
python spike.py
cat spike-results.json
```

## Output

`spike-results.json` contains the 4 test results + a `recommendation` field (GO mem0 / NO-GO mem0 / hybrid).

Then write `.planning/phases/02-memoire-intelligente-agents/02-SPIKE-RESULT.md` with the decision matrix and implications for Plan 02-03.

## Cleanup

```bash
docker rm -f spike-pg spike-qdrant
deactivate
# Then once Plan 02-03 starts, delete this whole apps/spike-mem0/ folder
```

## What the spike measures

1. **Team isolation** : 100 facts across 3 teams encoded as `user_id="team:{slug}"`. Search with team A returns 0 facts from team B (leak count must = 0).
2. **Versioning** : update one fact 3×, retrieve full history.
3. **Truth-level glue effort** : count lines of glue code needed to overlay xbrain's state machine (`EPHEMERAL → CANONICAL`) on top of mem0. Pass if ≤ 100 lines.
4. **Latency** : 1000 search queries, P95 must be < 500 ms.

If any test fails or is borderline, decide NO-GO mem0 → switch to NativeProvider in Plan 02-03.
