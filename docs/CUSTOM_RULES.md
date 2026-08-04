# Custom Rules

Custom rules are natural language preferences that adjust how candidates are
scored.

## Adding rules

```bash
python3.11 -m src.cli preferences custom-rules add "avoid horror"
python3.11 -m src.cli preferences custom-rules list
python3.11 -m src.cli preferences custom-rules interpret "prefer sci-fi"   # dry run
python3.11 -m src.cli preferences custom-rules remove 0
python3.11 -m src.cli preferences custom-rules clear --yes
```

In the web UI: **Preferences** tab, **Rules** section, **Custom rules**. Type
the rule, click **Add Rule**, then **Save Preferences**.

## Supported patterns

| Kind | Phrasings |
|------|-----------|
| Boost a genre | "prefer horror", "love sci-fi", "in the mood for comedy" |
| Penalise a genre | "avoid horror", "no romance", "tired of action" |
| Focus on a type | "only books", "just movies", "exclusively TV shows" |
| Exclude a type | "no video games", "skip movies" |
| Length | "short books", "long movies", "epic novels" |

Common aliases resolve to a canonical genre: sci-fi, scifi and sf all mean
science fiction, scary means horror, role-playing means rpg, shooter means fps.
Aliases exist for around fifty genres.

## How rules affect scoring

`CustomPreferenceScorer` starts a candidate at a neutral `0.5` and moves it:

- A matching genre boost pushes toward `1.0`, by up to `0.5`
- A matching genre penalty pushes toward `0.0`, by up to `0.5`
- Content type preferences shape the score for matching types
- Length preferences apply a soft penalty, so an item that does not match ranks
  lower rather than disappearing

Rules merge, and a later rule wins a conflict. They influence the score rather
than overriding it, so a strong candidate can still surface despite a penalty.
Check your scorer weights too, see [SCORING.md](SCORING.md).

## LLM interpretation

With AI enabled, `--use-llm` handles compound and nuanced rules:

```bash
python3.11 -m src.cli preferences custom-rules interpret \
  "I'm burnt out on grimdark fantasy but still enjoy lighter fantasy with humor" \
  --use-llm
```

Results are cached, so a repeated rule costs no further LLM calls.

## If a rule is not working

`custom-rules interpret "your rule"` shows how it parses, `custom-rules list`
confirms it saved, and in the web UI check that you clicked **Save Preferences**.
Use standard genre names, and prefer several simple rules over one complex one.
