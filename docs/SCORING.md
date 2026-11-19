# How Scoring Works

The engine scores every candidate through weighted factors. Setting a weight to
`0` disables that scorer. Weights resolve as **built-in default < global
settings < per-user preferences**.

| Weight key | What the scorer does | Default |
|------------|----------------------|---------|
| `genre_match` | How you rate the item's genres against your own average rating. A genre rated a star above your average climbs toward 100% the more often you have rated it, 75% at two ratings, and one a star below falls toward 0% the same way. Its genres are averaged, so one you dislike pulls down one you love, and a genre on most of your library, or one you have not rated, counts for little or nothing | 2.0 |
| `creator_match` | Prefers authors, directors and developers you have enjoyed, and demotes ones you have not | 1.5 |
| `tag_overlap` | How many of a consumed item's tags the item shares, scaled by how you rated it. Each tag counts once: in full when the item has the same tag, half when it has a related tag from the same genre cluster. A consumed item with more tags can lift a match higher. A match on a 1-star item still lifts, at half a 5-star match's strength | 1.0 |
| `content_length` | Soft penalty for a length mismatch. Counts only for a content type with a length preference other than `any` | 1.0 |
| `continuation` | Scores the later entries and seasons of a series you have begun by how you rated its entries of the same content type: 5 stars is 100%, 3 is 50%, 1 is 0%, and none rated is 50%. A show's next season is scored by the latest season you rated, or by the show's rating when no season is rated. Counts only where an earlier entry is finished or begun, and not while any entry of that series is in progress | 1.0 |
| `adaptation` | Boosts a film, show or game adapting something you rated well, and the source behind an adaptation you loved. An item adapts one of another type when either's title or series name matches the other's, a trailing "Collection" aside, or when they share an author and a similar title. Counts only for an item that adapts something | 1.5 |

Tags and genre clusters are different things. A tag is one of an item's own terms, such as `Cyberpunk` or `War`. A genre cluster is one of the coarse themes those terms group into, such as science fiction, so two items can share a genre cluster without sharing a tag. Of the scorers only `tag_overlap` reads genre clusters: `genre_match` reads tags, and `adaptation` matches titles and authors. The variety penalty reads genre clusters and nothing else.

## One score, fully explained

The score a recommendation shows **is** the weighted mean of the rows the web
**Score Details** panel lists for it, so those rows and their weights reproduce
the number beside them. Nothing is added outside that budget, and there is no
second combination stage.

A scorer that cannot fire for an item, or has nothing configured to measure, is
left out of both the panel and the weight the mean divides by: `continuation` on
an entry nothing begun precedes or whose series is in progress, `adaptation` on
something that adapts nothing, `content_length` on a type with no length
preference. A neutral `0.5` for want of evidence on a scorer that applies is a
real contribution and keeps its weight.

An entry of a series you have not started gets no series row, so it scores
exactly as a standalone with the same other rows. Which entry is offered is the
job of series filtering below.

A candidate the engine knows nothing about scores about `0.39` at the default
weights: `genre_match` and `creator_match` return a neutral `0.5` with no
evidence either way, but `tag_overlap` returns `0` with no shared tags and keeps
its weight, so the mean is `(0.5 × 2 + 0.5 × 1.5 + 0 × 1) / 4.5`.

Genre and creator dislike are ordinary contributions rather than a veto: a book
by an author you rated one star sinks to the bottom on those two rows and keeps
whatever the rest of the rows earn it. It is never removed from the ranking.

The one thing applied after the weighted mean is the variety penalty below,
which has its own row in the same panel.

## Scoring expectations, checked

Each row is one expectation from an interview with the operator in November 2026, held away from the code and then checked against it. Where the two differed, the operator either kept the code, and the row states that agreed behaviour beside the first answer, or the code changed to meet the expectation. Each row's test fails if scoring stops behaving as the row says.

| Agreed expectation | Worked example | Settled | Test |
|---|---|---|---|
| A 1 drags matching candidates down, as one row among several, never a veto | Book 1 rated 1: book 2's `continuation` row is 0, so it scores 4.5/5.5 of an identical standalone | Met | `test_scoring_pipeline.py::test_a_sequel_sits_a_continuation_row_from_an_identical_standalone` |
| A creator rated only 1 drags their next work | Scores below a creator never read | Met | `test_scorers.py::test_a_creator_rated_only_one_star_scores_below_a_stranger` |
| A creator rated 1, 1, 5, 5, 5 is middling, whatever else the library holds | Beside a favourite rated 5 ten times: 0.55, level with a creator rated 5 once, above one rated 1 once, below one rated 5, 5, 5 | Code changed: each creator's ratings now net out before scaling, where a favourite's many 5s used to sink this record below a lone 1 | `test_scorers.py::test_a_mixed_record_scores_between_a_hated_and_a_loved_creator` |
| A tag rated the same way more often counts more | Rated 5 five times outscores rated 5 once | Met | `test_scorers.py::test_a_genre_rated_once_claims_less_than_one_rated_five_times` |
| A tag rated 3 is measured against the library's own average, so it is neutral only where that average is 3. First answer: a 3 never promotes or demotes | In a library averaging 4 stars, a tag rated 3 every time scores at most 0.25 in `genre_match` | Code kept | `test_scorers.py::test_loved_average_and_disliked_genres_separate_even_under_a_shared_term` |
| Tag matching: a 1-star item still connects, a 5-star one lifts most | A `War` candidate after a 1-star item tagged only `War`: `tag_overlap` 1/6, half the 5-star 1/3 | Met | `test_scorers.py::test_a_one_star_match_scores_half_a_five_star_one` |
| `continuation` reads series ratings: enjoying earlier entries boosts the next | Entries rated 5, 3 and unrated give the next entry 0.75 | Met | `test_scorers.py::test_the_rating_averages_only_the_rated_entries_of_that_series` |
| Every completion enters the variety ladder, a 1-star one too | A 1-star fantasy completion takes the top rung | Met | `test_variety.py::test_a_one_star_completion_claims_its_rung_like_a_five_star_one` |
| Variety keys on genre clusters alone. First answer: on tags as well | After a cyberpunk completion, a cyberpunk candidate and a space opera one take the same full penalty: only the science fiction genre cluster counts | Code kept | `test_variety.py::test_a_candidate_sharing_no_tag_takes_the_rung_of_its_genre_cluster` |
| A completion bringing no new genre cluster spends no rung. First answer: each completion pushes the ladder down a rung | Fantasy, fantasy, science fiction, newest first: science fiction takes the second rung, as the repeat spends none | Code kept | `test_variety.py::test_duplicate_clusters_collapse_to_one_rung` |
| One direct tag match never dominates. No size was given for the `+2`, so it stays | 5-star item tagged only `Magic`: 1/3. Tagged `Magic, Wizards, Sorcery`, all shared: 3/5 | Met | `test_scorers.py::test_a_one_tag_item_never_gives_full_marks_and_a_richer_one_reaches_higher` |
| A concept present as a tag and through a genre cluster counts once | 5-star item tagged `Space Warfare, Science Fiction`, candidate tagged `Space Warfare`: 1.5/4 | Met | `test_scorers.py::test_a_term_counts_once_however_many_clusters_it_shares` |
| Three genre cluster matches outrank one shared tag: which of the two a match comes through matters less than how much of the item it reaches | 5-star item tagged `War, Science Fiction, Space Opera`: a `Space Warfare` candidate scores 1.5/5, a `War` one 1/5 | Met | `test_scorers.py::test_three_genre_cluster_matches_outrank_one_shared_tag` |
| `genre_match` reads tags, never genre clusters, which reach scoring through `tag_overlap`. First answer: tags and genre clusters merged | After cyberpunk rated 5, a space opera candidate gets a neutral `genre_match`. Only `tag_overlap` sees the shared genre cluster | Code kept | `test_scorers.py::test_a_shared_genre_cluster_without_a_shared_tag_scores_neutral` |
| A candidate matches its best consumed item, so a larger library never dilutes its match | 5-star item tagged `Battle, War, Drama, Survival`: candidates tagged `Battle` 1.5/6, `Battle, War` 2/6, `Battle, War, Drama` 3/6, alone or beside 20 unrelated 5-star items | Met | `test_scorers.py::test_a_larger_library_leaves_each_candidates_score_where_it_was` |
| An item with no metadata keeps its score rather than ranking last | No genres, tags or creator: 0.39 | Met | `test_scoring_pipeline.py::test_an_item_with_no_metadata_scores_the_documented_bare_score` |

## Setting the global weights

From the **Settings** page (Recommendations section) or the CLI. Both take
effect immediately:

```bash
uv run python -m src.cli settings set recommendations.scorer_weights.genre_match 3.0
uv run python -m src.cli settings list --section recommendations   # every weight
```

## Per-user overrides

The Preferences page and the `preferences` CLI (see [CLI.md](CLI.md#preferences))
set weights for one user. That map is **sparse**: it overrides only the keys you
touch, and every other weight falls back to the global value.

Both surfaces take only the six keys in the table above, and only numbers
from `0` to `5`, the global settings included. Anything else is refused where it
is written, rather than stored to weight nothing. A per-user weight stored above
`5` before that bound existed reads as `5`.

## Series filtering

With **"Recommend series in order"** on, the default, a book 3 you cannot yet
read is replaced by the earliest available entry in its series. That entry takes
book 3's place in the list and shows its own score, marked "Up next in" the series
with the score it is ranked at.

In a series you have started, the entry offered is the earliest one in your
library you have neither finished nor ignored. A position missing from the
library, or ignored, is skipped rather than waited on, and an earlier entry
comes back first: with Fallout 3 played, Fallout 1 is offered ahead of New Vegas.

Both the series and the position come from what a source states: series
metadata, or a marker still naming the series in the title, such as
`(The Expanse, #2)`. An item nothing names belongs to no series. A bare number
says nothing on its own, as "Left 4 Dead 2" shows.

Half-numbered entries such as `(The Expanse, #2.5)` order as fractions, so the
novella waits for book `#2`.

Where any entry of a series states no position, the whole series orders by
release year instead: one entry's date is never ranked against another's number.
An entry with no year recorded ranks after the dated ones, by title.

Where no entry records a year, the series is left unordered instead: ranking on
title alone would invent a reading order. A book's stored year is its edition's
rather than the work's, so a whole book series is the common case — series
filtering leaves those entries alone and offers them like any other candidate.

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
the genre you just finished. `0.0` turns it off, and `5.0` zeroes that genre's
same-type candidates outright. There is **no score floor**.

Your five latest completions each own a rung on a ladder decaying by completion
date, and every genre cluster that completion reaches sits on its rung. A
completion whose clusters are all on the ladder already spends no rung, leaving
it to an older one.

At `5.0` the rungs are 100%, 80%, 60%, 40%, 20%, then nothing, and a lower
setting scales the whole ladder down: `2.0` gives 40%, 32%, 24%, 16%, 8%. A
candidate takes each rung it touches scaled by how much of that rung's clusters
it shares, and keeps the largest: after a fantasy litrpg adventure, the next book
in that series takes the full rung, another litrpg most of it, an epic fantasy a
fraction, a thriller nothing.

The penalty is **per content type**: a fantasy *book* leaves fantasy *movies*
and *games* alone. A series gets no exemption.

### How games cluster

A **game's** clusters come from its genres and IGDB themes, never its tags, so a
fantasy RPG and a sci-fi RPG differ. A game enriched before IGDB stated themes
separately has none until `enrichment reset --provider igdb` re-enriches it.

### TV seasons and completion dates

A finished **TV season** counts as a completion on its watched date, even with
the show unfinished.

The date is the calendar day in the host's timezone, not UTC (see
[DOCKER.md](DOCKER.md#environment-variables) for `TZ`). A sync keeps the later
of two dates, so re-syncing repairs a date only after a move east. Setting `TZ`
alone rewrites nothing, so set it before the import that matters.

## Items you are part-way through

Something in progress is not offered back to you: the book you are reading, the game you are playing, the season you are mid-way through. The book or game after it still appears, without a `continuation` row. Once nothing in the series is in progress, what follows a finished entry gets its `continuation` row back, scored by how you rated that series.

No later season of a show appears until the one in flight is finished, whether or not series are in order.

A rating on something in progress counts like one on something finished: it shapes your taste, can be cited as a reason, and lifts what adapts it. Unrated, it does none of those.

Each watched season of a show takes its own rating. Once one is rated, the show's rating is their mean rounded half up, so seasons rated 5 and 2 make a 4, and a half-watched show counts on what you have rated. Its next season is scored by the latest season rated, so a great first season does not carry a weak second.

A show with a known season count is scored season by season, so the season in flight leaves on its own. Trakt and Tautulli count episodes per season, which names it. Without counts, as after an import, a show with a season finished is between seasons and its next season is offered, while one with none finished is in season 1. A show with no season count cannot be split, and leaves whole while in progress.

## Items you did not finish

Something marked did not finish is never offered back. Rated, it shapes your taste exactly as a finished item does; unrated, it does not, and it never appears under needs rating. It claims a variety rung like a completion.

Giving up on a series entry holds every later entry, whether or not series are in order. Finish or start a later entry and the series resumes after it, in order as usual. A show marked did not finish offers none of its seasons.

## Unreleased items

Nothing is recommended before it is out. A movie or game with a release date after today is held back, and without a date, one whose release year is after this year. A book is judged by its year published, a show by its first air date. An item stating neither counts as released. It stays in your library either way.

A show TMDB has enriched also carries each season's air date. A season airing after today is never offered, and nor is one announced without a date while its show's other seasons have one. No later season is offered past either. A season TMDB did not list, such as one a later sync counted, is judged by the show's first air date.

A TMDB-enriched show with an unwatched season TMDB has not dated, or dated after today, is enriched again by the first enrichment run a week after its last, which is how that season's date arrives.

## Ignored items

An ignored item stays in your library and shapes nothing you are recommended. It
never feeds preference analysis, scoring or the "since you enjoyed X"
references, is never offered as a candidate, and claims no variety rung. It
still counts as consumed for series ordering, one of the two exceptions
spelled out at the end of this page.

A re-import leaves the flag alone unless the file states it. A stated
`ignored: false` un-ignores, which is what makes the export-edit-re-import round
trip work, and also why re-importing an export replaces your whole ignore list.
See [DATA_SOURCES.md](DATA_SOURCES.md#library-export). A merge moves no ignore:
the absorbed row's stops applying while that row is hidden.

Completed-but-unrated items are excluded from the taste *signal* the same way,
since something you finished but never rated says nothing about your taste. Like
anything finished, they are never offered back. Unrated items you have not
started still are, a backlog being unrated by nature. The filtering lives in
the storage layer's signal-set accessor, so every caller respects it.

There are two deliberate exceptions. Series *ordering* asks whether you have
consumed an earlier entry, which is independent of rating and ignore state, so
an ignored or unrated earlier entry still counts for "book #1 before book #3".
The variety ladder above asks what you recently finished, which is independent
of rating but not of ignoring.
