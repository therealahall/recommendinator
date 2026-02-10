# Custom Rules Guide

Custom rules let you fine-tune recommendations using natural language preferences. Rules are interpreted automatically and influence the scoring of recommended items.

## Quick Start

### CLI

```bash
# Add a rule
python3.11 -m src.cli preferences custom-rules add "avoid horror"

# List your rules
python3.11 -m src.cli preferences custom-rules list

# Remove a rule by index
python3.11 -m src.cli preferences custom-rules remove 0

# Clear all rules
python3.11 -m src.cli preferences custom-rules clear --yes

# Test how a rule will be interpreted
python3.11 -m src.cli preferences custom-rules interpret "prefer sci-fi"
```

### Web UI

1. Go to the **Preferences** tab
2. Scroll to **Custom Rules** section
3. Type your rule and click **Add Rule**
4. Click **Save Preferences** to apply

## Supported Rule Patterns

### Genre Preferences

**Boost genres you like:**
- "prefer horror"
- "love sci-fi"
- "more fantasy"
- "give me mystery"
- "in the mood for comedy"

**Penalize genres you want to avoid:**
- "avoid horror"
- "no romance"
- "skip thriller"
- "hate drama"
- "tired of action"

### Content Type Filters

**Focus on specific types:**
- "only books"
- "just movies"
- "exclusively TV shows"

**Exclude types:**
- "no video games"
- "skip movies"

### Length Preferences

**Prefer short, medium, or long content:**
- "short books"
- "long movies"
- "quick games"
- "epic novels"

## Genre Aliases

The system recognizes common aliases:

| Canonical Name | Aliases |
|----------------|---------|
| science fiction | sci-fi, scifi, sf |
| horror | scary, terrifying |
| mystery | mysteries, detective |
| romance | romantic, love story |
| comedy | comedies, funny, humor |
| rpg | role-playing |
| fps | first-person shooter, shooter |

## How Rules Affect Scoring

Rules adjust the scoring pipeline via the `CustomPreferenceScorer`:

1. **Genre boosts** increase scores for matching items (up to +0.5)
2. **Genre penalties** decrease scores for matching items (up to -0.5)
3. **Content type preferences** influence scoring for matching types
4. **Length preferences** apply a soft scoring penalty (non-matching items rank lower but are not excluded)

Multiple rules are merged together. Later rules take precedence for conflicts.

## LLM-Enhanced Interpretation

When AI features are enabled, complex rules can use LLM interpretation:

```bash
# Use LLM for nuanced rule interpretation
python3.11 -m src.cli preferences custom-rules interpret "I'm burnt out on grimdark fantasy but still enjoy lighter fantasy with humor" --use-llm
```

The LLM interpreter handles:
- Complex compound rules
- Nuanced preferences
- Context-dependent meanings

Results are cached to avoid repeated LLM calls.

## Examples

```bash
# Avoid a genre you're tired of
python3.11 -m src.cli preferences custom-rules add "tired of superhero movies"

# Focus on a specific mood
python3.11 -m src.cli preferences custom-rules add "in the mood for cozy mysteries"

# Multiple rules work together
python3.11 -m src.cli preferences custom-rules add "prefer sci-fi"
python3.11 -m src.cli preferences custom-rules add "avoid romance"
python3.11 -m src.cli preferences custom-rules add "short books"
```

## Tips

1. **Be specific**: "avoid horror movies" is clearer than "no scary stuff"
2. **Use common terms**: The system recognizes standard genre names
3. **Test first**: Use `interpret` to see how a rule will be parsed
4. **Combine rules**: Multiple simple rules often work better than one complex rule
5. **Review periodically**: Your preferences change over time

## Troubleshooting

**Rule not working?**
1. Check interpretation: `python3.11 -m src.cli preferences custom-rules interpret "your rule"`
2. Verify it was saved: `python3.11 -m src.cli preferences custom-rules list`
3. Make sure you clicked "Save Preferences" in the web UI

**Unexpected recommendations?**
- Rules influence but don't completely override the scoring system
- High-scoring items may still appear despite penalties
- Check your other preference settings (scorer weights, etc.)
