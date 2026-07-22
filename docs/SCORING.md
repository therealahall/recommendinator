# How Scoring Works

The recommendation engine scores candidates through multiple weighted factors.
Each scorer has a configurable weight (set in `config.yaml` or per-user
preferences); set a weight to `0` to disable a scorer entirely.

| Scorer | What it does |
|--------|--------------|
| **Genre Match** | Boosts content matching genres you've rated highly |
| **Creator Match** | Prefers authors/directors/developers you've enjoyed |
| **Tag Overlap** | Threshold-based tag matching with semantic cluster bridging |
| **Series Order** | Prioritizes next items in series you're reading/watching/playing |
| **Continuation** | Boosts items you're actively consuming (e.g., in-progress TV show). Automatically removed from the pipeline when you have no in-progress items, so it never produces noise. |
| **Series Affinity** | Boosts items in franchises you've rated well |
| **Rating Pattern** | Learns from your rating history within genres |
| **Content Length** | Soft penalty for items that don't match your preferred length |
| **Custom Rules** | Applies your explicit preferences ("avoid X", "prefer Y") |
| **Semantic Similarity** | *(AI only)* Finds conceptually similar content |

Configure weights via `config.yaml`:

```yaml
recommendations:
  scorer_weights:
    genre_match: 2.0
    creator_match: 1.5
    tag_overlap: 1.0
    series_order: 1.5
    rating_pattern: 1.0
    semantic_similarity: 1.5  # Only active when ai_enabled is true
    content_length: 1.0
    continuation: 2.0
    series_affinity: 1.0
    custom_preference: 1.0
```

Or per-invocation via the CLI — see [CLI.md](CLI.md#preferences).

## Series filtering

When the **"Recommend series in order"** preference is enabled (the default), the
engine enforces series ordering. If Book 3 in a series would otherwise be
recommended but you haven't consumed Books 1 and 2, the engine automatically
substitutes the earliest available entry. This works with numbered titles, Roman
numerals, season indicators, and metadata-based series info from enrichment.
Half-numbered entries — novellas like `(The Expanse, #2.5)` — are ordered as
fractions, so a `#2.5` novella waits until you've read book `#2` rather than
being offered ahead of it.

## Content length preferences

Set length preferences per content type (`short`, `medium`, `long`, or `any`)
via the CLI or web UI. Items that don't match your preference still appear but
rank lower — it's a soft penalty, not a hard filter.

| Content Type | Short | Medium | Long |
|---|---|---|---|
| Book | < 250 pages | 250–500 pages | 500+ pages |
| Movie | < 90 minutes | 90–150 minutes | 150+ minutes |
| TV Show | < 3 seasons | 3–6 seasons | 6+ seasons |
| Video Game | < 10 hours | 10–40 hours | 40+ hours |

Items without length metadata (common before enrichment) receive a small
benefit-of-the-doubt score rather than being penalized.

## Variety after completion

Set the **"Variety After Completion"** preference above `0.0` (web UI slider, or
`preferences set-variety <0.0-5.0>` in the CLI) to stop the recommender from
marching through the next entry in a genre you just finished — for example,
finishing a fantasy book no longer makes the next fantasy book your automatic #1.

`variety_penalty` is a number in **0.0–5.0**, the same scale as the scorer
weights. `0.0` turns the feature **off**; higher values demote recently finished
genres more aggressively. Internally the preference is divided by its `5.0`
maximum to derive the top penalty *fraction* applied to a just-finished genre, so
`4.0` reproduces the legacy full-strength behavior (a `0.8` fraction) and `5.0`
is full strength — a just-finished genre's same-type candidates are zeroed
entirely. There is **no score floor**.

The genres you most recently *completed* are penalized on a stepped ladder by
recency. The most recently finished genre cluster takes the full penalty you set,
and the penalty decays over your last **5 distinct** finished genres. At the
maximum `5.0` that ladder is 100% → 80% → 60% → 40% → 20%, then nothing; a smaller
value scales the whole ladder down (e.g. `2.0` gives 40% → 32% → 24% → 16% → 8%).
A candidate is penalized by its freshest matching genre, and the penalty
multiplies its final score.

The penalty is **per content type** — finishing a fantasy *book* varies your book
recommendations but leaves fantasy *movies* and *games* untouched. Each
recommendation surfaces its applied penalty (CLI table/JSON and the web "Score
Details" panel) so you can see why a recently finished genre was demoted.

The next entry in a series you're **actively reading** gets a softened penalty
(halved): finishing book #1 of a series doesn't mean you're done with the genre,
so the legit next book is nudged down but not buried. Starting a brand-new series
in a just-finished genre still takes the full penalty — that's exactly the
genre-hop this preference is for.

Finishing a **TV season** counts as a completion too, even while the show as a
whole is still "currently consuming." A show with at least one fully-watched
season claims a rung on the ladder for its genres, dated by that season's
watched date rather than the show's (absent) completion date. That date comes
from Trakt's per-season last-watched time, or from the timestamp of a manual
season check-off in the library editor. The same season-date fallback also
covers a **completed** TV show that has no recorded completion date (for
example one tracked only per-season, so its `date_completed` is absent): it is
dated by its most recent watched-season timestamp instead of being treated as
undated, so a show you just finished lands on the freshest rungs rather than
sinking to the weakest one. The show's next season is still a
series continuation, so it gets the same halved penalty described above —
finishing season 1 nudges season 2 down without burying it. A finished season
with no recorded date (e.g. imported via CSV/JSON, watched before this
project started tracking per-season dates, or Trakt has no per-episode watch
timestamp for that season) still claims a rung — it just sorts to the
weakest/undated one, so it is never silently excluded from the ladder.

## Diversity bonus (advanced)

Independently of the variety toggle, an explicit per-user `diversity_weight`
(0.0–1.0, default 0.0) adds a mild genre-hopping bonus in the ranker, boosting
candidates whose genres differ from your recently completed content (Jaccard
distance on genre sets). Leave it at 0.0 unless you want an extra nudge on top of
the variety penalty.

## Ignored items

Items can be marked as `ignored` to permanently exclude them from
recommendations. Set `ignored: true` when importing via CSV or JSON templates, or
use the **Ignore** button in the web UI's Library page (CLI:
`library ignore --id <id>`). Ignored items remain in your library but are
excluded from **all** recommendation processing — they never feed preference
analysis, scoring, similarity search, or the "since you enjoyed X" explanation
references, and they are never surfaced as candidates. The same exclusion
applies to completed-but-unrated items in the *signal* set: an item you finished
but never rated carries no taste signal, so it does not shape recommendations
(though unrated items still appear as candidates — the backlog you might consume
next is unrated by nature). This filtering is centralized in the storage
layer's signal-set accessor, so every surface that shapes recommendations —
the ranking engine, the conversational assistant, and the web's streaming
blurbs — respects it uniformly.

Series *ordering* is the one deliberate exception: whether you have already
consumed an earlier entry in a series is a consumption fact independent of
rating or ignore state, so an ignored or unrated earlier entry still counts for
"recommend book #1 before book #3" purposes.
