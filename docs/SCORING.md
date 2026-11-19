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
| `continuation` | Scores the later entries and seasons of a series you have begun by how you rated its entries of the same content type: 5 stars is 100%, 3 is 50%, 1 is 0%, and none rated is 50%. A show's next season is scored by the latest season you rated, not the show's rating. Counts only where an earlier entry is finished or begun, and not while any entry of that series is in progress | 1.0 |
| `adaptation` | Boosts a film, show or game adapting something you rated well, and the source behind an adaptation you loved. An item adapts one of another type when either's title or series name matches the other's, a trailing "Collection" aside, or when they share an author and a similar title. Counts only for an item that adapts something | 1.5 |

Tags and genre clusters are different things. A tag is one of an item's own terms, such as `Cyberpunk` or `War`. A genre cluster is one of the coarse themes those terms group into, such as science fiction, so two items can share a genre cluster without sharing a tag. Of the scorers only `tag_overlap` reads genre clusters, and the variety penalty reads nothing else.

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

## Settled questions, as worked examples

Each row is a scoring question with its agreed answer. The example is the answer worked through, and the test fails if scoring stops giving it. Scores are row values unless marked as the mean.

| Question | Agreed answer | Example and expected outcome | Test |
|---|---|---|---|
| How far does a 1-star item pull related candidates down, and which scorer does it? | `genre_match`, `creator_match` and `continuation` pull down, each toward 0 on its own row, so the drag is one row among several and never a veto. `tag_overlap` never pulls down: a match on a 1-star item still connects, at half a 5-star match's lift | Book 1 rated 1, then book 2 against a standalone by the same author with the same genres: `continuation` 0, so book 2's mean is 4.5/5.5 of the standalone's. Book 1 rated 5 gives book 2 a full-marks row instead | `tests/test_scoring_pipeline.py::TestScoringPipeline::test_a_sequel_sits_a_continuation_row_from_an_identical_standalone` |
| | | An author rated only 1, against one never read: `creator_match` below the stranger's 0.5 | `tests/test_scorers.py::TestCreatorMatchScorer::test_a_creator_rated_only_one_star_scores_below_a_stranger` |
| | | Romance rated 2 five times, horror 5 five times: `genre_match` at most 0.25 for romance | `tests/test_scorers.py::TestGenreMatchScorer::test_loved_average_and_disliked_genres_separate_even_under_a_shared_term` |
| | | A `War` candidate after a 1-star item tagged only `War`: `tag_overlap` 1/6. After a 5-star one: 1/3 | `tests/test_scorers.py::TestTagOverlapScorer::test_a_one_star_match_scores_half_a_five_star_one` |
| Does `continuation` read series ratings? | Yes: the mean of the rated entries of that series and content type, 5 stars 1.0, 3 stars 0.5, 1 star 0. Unrated entries are left out, and a series with none rated scores 0.5 | Entries 1 to 3 rated 5, 3 and unrated, scoring entry 4: 0.75 | `tests/test_scorers.py::TestContinuationScorer::test_the_rating_averages_only_the_rated_entries_of_that_series` |
| Does variety after completion key on tags or genre clusters? | Genre clusters only. A 1-star completion claims a rung like any other | A space opera right after a cyberpunk novel, no tag shared: the full top rung, since both sit in the science fiction genre cluster | `tests/test_variety.py::TestVarietyPenaltyFor::test_a_candidate_sharing_no_tag_takes_the_rung_of_its_genre_cluster` |
| | | A 1-star fantasy completion after a 5-star science fiction one: fantasy takes the top rung | `tests/test_variety.py::TestBuildVarietyLadder::test_a_one_star_completion_claims_its_rung_like_a_five_star_one` |
| How big is the `+2` in `tag_overlap`? | Keep 2. A match scores the tags shared over the consumed item's tag count plus 2, so one shared tag never gives full marks and a richer match reaches higher | A 5-star item tagged only `Magic`: 1/3. One tagged `Magic, Wizards, Sorcery`, all three shared: 3/5 | `tests/test_scorers.py::TestTagOverlapScorer::test_a_one_tag_item_never_gives_full_marks_and_a_richer_one_reaches_higher` |
| Does a concept present as a tag and through a genre cluster count twice? | Once. A shared tag counts in full, a tag reached only through a shared genre cluster at half | A 5-star item tagged `Space Warfare, Science Fiction`, a candidate tagged `Space Warfare`: 1.5/4 | `tests/test_scorers.py::TestTagOverlapScorer::test_a_term_counts_once_however_many_clusters_it_shares` |
| Is the score plateau gone? | Yes. A candidate is scored against its best-matching consumed item, never the pooled tags of the whole library, so a larger library cannot flatten scores into one band. To check a live library, run `recommend --type tv_show`: the scores should spread out rather than share one value | A candidate sharing 1, 2 and 3 of a 4-tag item's tags: 1.5/6, 2/6 and 3/6, unchanged once 20 unrelated rated items join the library | `tests/test_scorers.py::TestTagOverlapScorer::test_a_larger_library_leaves_each_candidates_score_where_it_was` |
| Where does an item with no metadata rank? | Wherever its score puts it, never pinned last | No genres, tags or creator: a mean of 0.39 | `tests/test_scoring_pipeline.py::TestScoringPipeline::test_an_item_with_no_metadata_scores_the_documented_bare_score` |

The code gives every agreed answer above, so none needs a scoring change.

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
the genre you just finished. `0.0` turns it off, and `5.0` zeroes a
just-finished genre's same-type candidates outright. There is **no score floor**.

Your five most recent completions each own a rung on a ladder that decays by
completion date, and every genre cluster that completion reaches sits on its
rung. A completion whose clusters are all on the ladder already spends no rung —
an older completion takes it instead.

At `5.0` the rungs are 100%, 80%, 60%, 40%, 20%, then nothing, and a lower
setting scales the whole ladder down: `2.0` gives 40%, 32%, 24%, 16%, 8%. A
candidate takes each rung it touches scaled by how much of that rung's clusters
it shares, and keeps the largest: after a fantasy litrpg adventure, the next book
in that series takes the full rung, another litrpg most of it, an epic fantasy a
fraction, a thriller nothing.

The penalty is **per content type**: a fantasy *book* leaves fantasy *movies*
and *games* alone. Every recommendation reports the penalty it took, in the CLI
and in the web **Score Details** panel.

A series gets no exemption from its genre's penalty.

A **game's** clusters come from its genres and its IGDB themes, never its tags.
Game genres name mechanics (RPG, shooter, strategy), so without themes a fantasy
RPG and a sci-fi RPG land on the same rung; store tags are too noisy to fill the
gap ('War' on a racing game). A game enriched before IGDB stated themes
separately has none until `enrichment reset --provider igdb` re-enriches it.
Books, movies and shows still cluster from genres and tags.

A finished **TV season** counts as a completion even while the show is still in
progress, dated by that season's watched date.

The date is the calendar day in the host's timezone, not UTC. See
[DOCKER.md](DOCKER.md#environment-variables) for setting `TZ` on a container.
A re-sync repairs a stored date only when the new zone is east of the old one,
never when it is west: a sync keeps the later of two dates, and setting `TZ` on
its own rewrites nothing. Set the zone before the import that matters.

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
