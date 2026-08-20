# How Scoring Works

The engine scores every candidate through weighted factors. Setting a weight to
`0` disables that scorer. Weights resolve as **built-in default <
`config.yaml` < global settings < per-user preferences**.

| Weight key | What the scorer does | Default |
|------------|----------------------|---------|
| `genre_match` | Boosts genres you rate highly, and sinks ones you rate badly | 2.0 |
| `creator_match` | Prefers authors, directors and developers you have enjoyed, and demotes ones you have not | 1.5 |
| `tag_overlap` | Threshold tag matching, bridged by thematic genre clusters | 1.0 |
| `series_order` | Prioritises the next entry in a series you are partway through | 1.5 |
| `rating_pattern` | Learns from your rating history within a genre | 1.0 |
| `content_length` | Soft penalty for a length mismatch | 1.0 |
| `continuation` | Boosts items you are actively consuming. Dropped from the pipeline when you have none | 2.0 |
| `series_affinity` | Boosts franchises you have rated well | 1.0 |
| `adaptation` | Boosts a film, show or game adapting something you rated well, and the source behind an adaptation you loved. Dropped from the pipeline when nothing adapts anything | 1.5 |
| `custom_preference` | Applies your natural language rules, "avoid X" and "prefer Y". Only in the pipeline when you have rules | 1.0 |

## One score, fully explained

The score a recommendation shows **is** the weighted mean of the table above.
Every contribution is a scorer with a weight you can set and a row in the web
**Score Details** panel, so the rows and their weights reproduce the number
beside them. Nothing is added outside that budget, and there is no second
combination stage.

A candidate the engine knows nothing about scores near `0.5`, because most
scorers return a neutral `0.5` with no evidence either way. Genre and creator
dislike are ordinary contributions rather than a veto: a book by an author you
rated one star sinks to the bottom on those two rows and keeps whatever the
rest of the rows earn it. It is never removed from the ranking.

The one thing applied after the weighted mean is the variety penalty below,
which has its own row in the same panel.

## Setting the global weights

From the **Settings** page (Recommendations section) or the CLI. Both take
effect immediately:

```bash
python3.11 -m src.cli settings set recommendations.scorer_weights.genre_match 3.0
python3.11 -m src.cli settings list --section recommendations   # every weight
```

A `recommendations.scorer_weights` block in `config.yaml` moves the baseline
before anything is saved, but the database wins once it holds a value.

## Per-user overrides

The Preferences page and the `preferences` CLI (see [CLI.md](CLI.md#preferences))
set weights for one user. That map is **sparse**: it overrides only the keys you
touch, and every other weight falls back to the global value.

Both surfaces take only the eleven keys in the table above, and only finite
numbers. Anything else is refused where it is written, rather than stored to
weight nothing.

## Series filtering

With **"Recommend series in order"** on, the default, a book 3 you cannot yet
read is replaced by the earliest available entry in its series. Ordering reads
numbered titles, Roman numerals, season indicators and series metadata from
enrichment. Half-numbered entries such as `(The Expanse, #2.5)` order as
fractions, so the novella waits for book `#2`.

## Content length preferences

Set a preference per content type (`short`, `medium`, `long` or `any`) from the
CLI or web UI. A mismatch ranks lower but still appears. Soft penalty, not a
filter.

| Content type | Short | Medium | Long |
|---|---|---|---|
| Book | up to 250 pages | 251 to 500 | over 500 |
| Movie | up to 90 minutes | 91 to 150 | over 150 |
| TV show | up to 3 seasons | 4 to 6 | over 6 |
| Video game | up to 10 hours | 11 to 40 | over 40 |

An item with no length metadata, common before enrichment, gets a
benefit-of-the-doubt score rather than a penalty.

Video game hours are the average playtime RAWG reports for the game, filled in
by enrichment as `average_playtime_hours`. Your own hours on record, from Steam
or an imported `hours_played` column, describe you rather than the game, so they
count for nothing here. A game RAWG has not enriched has no length and takes the
benefit-of-the-doubt score above. So does one enriched before this release, whose
average sits under the old key until enrichment runs again.

## Variety after completion

Set **"Variety After Completion"** above `0.0` (web UI slider, or
`preferences set-variety <0.0-5.0>`) to stop the recommender marching through
the genre you just finished. `0.0` turns it off. The value is divided by its
`5.0` maximum to give the top penalty fraction, so `5.0` zeroes a just-finished
genre's same-type candidates outright. There is **no score floor**.

Your five most recently finished genre clusters sit on a ladder that decays by
recency. Every completion counts, rated or not — finishing six fantasy novels
tires you of fantasy whether or not you scored them. Ignored items do not, since
ignoring something asks for less of it. At `5.0` the rungs are 100%, 80%, 60%,
40%, 20%, then nothing, and a lower setting scales the whole ladder down: `2.0`
gives 40%, 32%, 24%, 16%, 8%. A candidate takes the penalty of its freshest
matching cluster, multiplied into its final score.

The penalty is **per content type**. Finishing a fantasy *book* varies your book
recommendations and leaves fantasy *movies* and *games* alone. Every
recommendation reports the penalty it took, in the CLI table and JSON and in the
web **Score Details** panel.

The next entry in a series you are actively reading takes **60%** of the
penalty, because finishing book #1 does not mean you are done with the genre.
Starting a brand-new series in that genre takes the full penalty.

A finished **TV season** counts as a completion even while the show is still in
progress, dated by that season's watched date. That date comes from Trakt's
per-season last-watched time or a manual season check-off, and it also dates a
completed show carrying no completion date of its own. An undated finished
season still claims a rung, the weakest one, so it is never silently dropped.
The show's next season is a series continuation, so it takes the softened
penalty.

Completions are ordered by completion date, so something you finish today
outranks an import dated years ago. Which surface stamps which is in
[ARCHITECTURE.md](../ARCHITECTURE.md#user-owned-fields).

The date is the calendar day in the host's timezone, not UTC. See
[DOCKER.md](DOCKER.md#environment-variables) for setting `TZ` on a container.
Setting it does not correct stored dates, because the sync rule keeps the later
of two dates and a corrected local date is the earlier one.

## Ignored items

An ignored item stays in your library and shapes nothing you are recommended. It
never feeds preference analysis, scoring or the "since you enjoyed X"
references, is never offered as a candidate, and claims no variety
rung. It still counts as consumed for series ordering, one of the two
consumption facts spelled out at the end of this page. Ignore it from the web
Library or Recommendations page, from `library ignore --id <id>`, or with
`ignored: true` in a CSV or JSON import.

A re-import leaves the flag alone unless the file states it. A stated
`ignored: false` un-ignores, which is what makes the export-edit-re-import round
trip work, and also why re-importing an export replaces your whole ignore list.
See [DATA_SOURCES.md](DATA_SOURCES.md#library-export). A merge moves no ignore:
the absorbed row's stops applying while that row is hidden.

Completed-but-unrated items are excluded from the taste *signal* the same way,
since something you finished but never rated says nothing about your taste. They
still appear as candidates, a backlog being unrated by nature. The filtering
lives in the storage layer's signal-set accessor, so every caller respects it.

The two deliberate exceptions are the consumption facts. Series *ordering* asks
whether you have consumed an earlier entry, which is independent of rating and
ignore state, so an ignored or unrated earlier entry still counts for "book #1
before book #3". The variety ladder above asks what you recently finished, which
is independent of rating but not of ignoring.
