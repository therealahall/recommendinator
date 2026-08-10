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

Both surfaces keep at most 50 rules of 500 characters each
(`UserPreferenceConfig.MAX_CUSTOM_RULES` and
`UserPreferenceConfig.MAX_CUSTOM_RULE_LENGTH`), because every rule goes into the
interpreter's prompt on each request.

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

Rules are sanitised before either interpreter sees them, and narrowly: only
what would break the prompt, the request carrying it, or a reader of whatever
the interpreter cuts from the rule. Runs of whitespace collapse to one space
and the ends are trimmed — a tab, a doubled space, U+00A0, and every character
`str.splitlines` treats as a break, U+2028 and U+0085 included. So a rule
occupies exactly one prompt line and cannot forge another to smuggle
instructions past the interpreter.

Double quotes and braces are dropped, because the rule sits in a quoted slot
beside a JSON template. Lone surrogates go because they fail the UTF-8 encode
of the request body and cost you the whole interpretation. NUL and the other
control characters encode fine. They go because a rule is prose, and one that
survived into a genre name would rewrite the terminal printing it back, or
truncate the log entry it landed in.

Everything else you type reaches the model as you typed it: `prefer 4+ star
ratings`, `no more than 20% horror`, `rating >= 4`, accents, CJK, emoji and
all.

## If a rule is not working

`custom-rules interpret "your rule"` shows how it parses, `custom-rules list`
confirms it saved, and in the web UI check that you clicked **Save Preferences**.
Use standard genre names, and prefer several simple rules over one complex one.
