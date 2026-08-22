# CLI Reference

Every Recommendinator feature is reachable from the CLI. It is a peer of the web
UI, not a subset. Run commands as `python3.11 -m src.cli <command>`. Most
read-only commands accept `--format json`.

Diagnostics go to the same log file the web server writes, at the level
`logging.level` names, so a startup message seen once from either interface is
readable from the other. The console gets warnings and errors alone, whatever
`logging.level` says, and gets them on stderr — stdout carries only the
command's own output, so `--format json` and `library export` stay pipeable.

Progress lines are on stderr for the same reason, so `recommend --format json`
pipes cleanly while a plain run still shows what it is doing.

Bytes the locale cannot decode are dropped from an argument before the command
reads it, so a review pasted out of a Latin-1 file stores and prints as text.
The trade: a file path in that shape is refused as missing.

Tracebacks are written to the log file only. On the console a command prints
its own error line, which is why several of them say to check the log. If the
log file cannot be opened at all — a root-owned `logs/` bind mount, say — the
command says so once on stderr and runs anyway, with its warnings and errors on
stderr, tracebacks still withheld, and nothing on disk.

A failing command refuses in the same words the matching web route answers
with, and the underlying error goes to the log. `--verbose` puts that error on
the terminal too. It is a root option, so it comes before the subcommand:

```bash
python3.11 -m src.cli --verbose recommend --type book
```

Startup is the exception, reported in full: it happens before there is a log
file to send anyone to.

## Import and recommend

### `update`

Syncs one source or all of them.

```bash
python3.11 -m src.cli update --source all
python3.11 -m src.cli update --source roms --format json
```

Each source reports how many items the plugin found, how many were saved, and
how those split into added, updated and unchanged — so a second run of the same
sync reads as 40 unchanged rather than as another 40 items. `--format json`
emits the document `GET /api/sync/status` serves for the same run.

### `import`

Reads one file: no source, no cadence, no sync run. `--importer` picks the
format and `--content-type` the type where the format decides none.
`--format json` emits what `POST /api/import` answers: five counts,
`total_rows` and a line per refused row, capped at the first 200 with a
`… and N more` tally after them, plus `notes` for what happened to the file.
`import-formats` lists the formats; `import-template` writes one to
`--output` or stdout, or lists them.

```bash
python3.11 -m src.cli import movies.csv --importer csv_import --content-type movie
```

### `recommend`

```bash
python3.11 -m src.cli recommend --type book --count 10
```

### `complete`

Marks something finished, adding it to the library if it is not there yet.

```bash
python3.11 -m src.cli complete --type book --title "Project Hail Mary" --rating 5
```

It takes no date. An item with no date is stamped today, and an existing date is
kept, so completing something an import already dated does not re-date it.

`--review` replaces the stored review, so a blank one is refused rather than
written. To erase a review, use `library edit --clear-review`.

## System status

`status` reports system health, component readiness and feature flags.

```bash
python3.11 -m src.cli status --format json
```

## Library management

### `library list`

Filters and sorts the library.

```bash
python3.11 -m src.cli library list --type book --status completed --sort rating --limit 20
python3.11 -m src.cli library list --enrichment not_enriched
python3.11 -m src.cli library list --search "die hard"
python3.11 -m src.cli library list --needs-rating --type movie
```

- `--enrichment` takes `enriched` or `not_enriched`, matching the table's
  **Enriched** column.
- `--search` matches title or creator and combines with the other filters. A
  term matches exactly, as a substring, or through typo tolerance, and all
  three are always in play. It is capped at 200 characters, the same bound
  `GET /api/items` and the web search box enforce.
- `--needs-rating` forces completed status, overriding `--status`, and composes
  with `--type`.

### `library show`

```bash
python3.11 -m src.cli library show --id 42
```

### `library edit`

```bash
python3.11 -m src.cli library edit --id 42 --rating 5 --status completed
python3.11 -m src.cli library edit --id 42 --clear-rating
python3.11 -m src.cli library edit --id 42 --clear-review
python3.11 -m src.cli library edit --id 42 --seasons-watched 1,2,3
python3.11 -m src.cli library edit --id 42 --genre Action --tag co-op --description "A grand adventure."
python3.11 -m src.cli library edit --id 42 --clear-genres --clear-tags --description ""
python3.11 -m src.cli library edit --id 42 --release-year 1993 --creator "id Software"
```

**Only the flags you pass are written**, so a status-only edit cannot erase a
rating. Passing one replaces it.

A TV show is the exception, because status and the season list are one fact
there. Omit `--seasons-watched` and the status fills it in: `--status unread`
empties it, `--status completed` ticks every season. Pass both and both are
written as given.

Emptying a field is a separate instruction. `--clear-rating`, `--clear-review`,
`--clear-genres` and `--clear-tags` are the only way to store nothing there, and
none may be combined with its value flag. `--review ""` is refused, pointing you
at `--clear-review`, because an empty string is far more often a shell accident
than an intention. A description is the exception: `--description ""` is its
clear, whitespace included, matching the emptied box the web sends.

`--seasons-watched` takes comma-separated season numbers, each 1-200. Repeated
`--genre` and `--tag` replace the existing lists rather than appending. Any of
`--genre`, `--tag` or `--description` marks the item enriched through the
`manual` provider, dropping it out of `not_enriched` and out of the automatic
queue — `library show` says so, and `enrichment reset --id` undoes it.

`--release-year` and `--creator` correct the two fields a title match is vetoed
on, so a row still holding a released merge's wrong year takes the next source
stating the true one instead of growing the library another row. Neither marks
the item enriched. A year runs 1800-2200 and a creator 500 characters; a book
takes no `--release-year`, because `year_published` dates the edition rather
than the work. Both set a value and neither clears one: pass the name or year
you want stored.

### `library ignore` / `library unignore`

Ignored items are excluded from recommendations.

```bash
python3.11 -m src.cli library ignore --id 42
python3.11 -m src.cli library unignore --id 42
```

### Duplicates and merges

```bash
python3.11 -m src.cli library duplicates --type book --limit 25
python3.11 -m src.cli library merge --survivor 42 --absorbed 77
python3.11 -m src.cli library merges
python3.11 -m src.cli library unmerge --merge-id 3
python3.11 -m src.cli library decline-duplicate --one 42 --other 77 --other 91
python3.11 -m src.cli library declined-duplicates
python3.11 -m src.cli library undecline-duplicate --one 42 --other 77
```

`duplicates` lists one block per work, naming every copy of it, with Keep ID
the copy proposed to keep — the oldest — and Other copies the ones a merge
would fold in. Evidence heads `same title` where the save door's own key
matched them, and `same title apart from a qualifier` where only the looser key
did, dropping a trailing parenthetical: the ones to look twice at. It offers 25
works at a time by default, saying how many are left. Two copies a creator,
year or region veto separates are never in one block. A copy in two blocks says
so under its title in both, a veto or a dismissal having split the group. A
work carrying more than 40 copies, or whose copies split more ways than a page
holds, is not offered at all, and the count line says how many are in that
state.

A merge keeps `--survivor` and folds the other row into it; run it once per
copy, from the one listing. Nothing is deleted, and `unmerge` puts the absorbed
row back — newest merge first, refusing any other order. `decline-duplicate`
sets `--one` apart from every `--other` named, for the life of the library, and
leaves the copies it did not name still offered together; it takes at most 39
of them, one short of the largest block offered.
`undecline-duplicate` lifts one of those refusals, and refuses while a merge
holds either row, naming the merge to undo first: a refusal is only liftable
back onto a pair the list can offer.

### `library export`

```bash
python3.11 -m src.cli library export --type book --format csv --output books.csv
python3.11 -m src.cli library export --output library.csv   # every type
```

Without `--type` the file covers the whole library. A CSV header then carries
all four types' columns plus a `content_type` column naming each row's type, so
each row leaves the columns its own type does not have blank.
Ignored items are exported either way, as the web Export button does.

## Source management

Add, edit, enable, disable and remove data sources without touching YAML.

```bash
python3.11 -m src.cli source plugins            # available plugin types
python3.11 -m src.cli source create my_roms roms
python3.11 -m src.cli source set my_roms paths inputs/roms
python3.11 -m src.cli source migrate my_roms    # or move a YAML source in, once
python3.11 -m src.cli source show my_roms       # also schema, enable, disable, remove
python3.11 -m src.cli source schedule my_roms weekly   # off, hourly, 6h, daily, weekly
```

`source schedule` takes a migrated source and one of those five keys. The web
server syncs each source on its cadence while it is running; `off` leaves it to
`update`. Unscheduled, a migrated source uses its plugin's default, and one that
has never synced is due on the next tick. One still in `config.yaml` stays off.

Every `source` subcommand except `set-secret` and `clear-secret` accepts
`--format json`.

`source apply` is the atomic multi-field update, the equivalent of
`PUT /api/sync/sources/<id>/config`. It reads a JSON dict from a file or stdin:

```bash
echo '{"paths": ["inputs/roms"]}' \
  | python3.11 -m src.cli source apply my_roms --from-json -
```

`source set-secret` prompts with hidden input. For Docker entrypoints and CI, set
`RECOMMENDINATOR_SECRET_VALUE` instead, which keeps the value out of shell
history and the visible process list:

```bash
RECOMMENDINATOR_SECRET_VALUE="$STEAM_API_KEY" \
  python3.11 -m src.cli source set-secret my_steam api_key
```

`source clear-secret` and `source remove` destroy a stored credential, so both
prompt first. Pass `--yes` to skip the prompt in a script.

## Global settings

Manages `recommendations`, `sync`, `enrichment`, `web` and `logging`.
`web.host`, `web.port` and `web.debug` are the
exception, staying in `config.yaml` because the server binds its socket before
the database opens.

The web **Settings** page is the same thing over the same
`src/settings/service.py`. Values persist to the `settings` table.

### `settings list` / `settings get`

```bash
python3.11 -m src.cli settings list
python3.11 -m src.cli settings list --advanced
python3.11 -m src.cli settings list --section recommendations
python3.11 -m src.cli settings get recommendations.default_count
```

Advanced infra and security settings (CORS origins, logging) stay hidden unless
`--advanced` or a specific `--section` asks for them. Neither command ever prints
a secret's value, only whether one is set.

### `settings set` / `settings reset`

```bash
python3.11 -m src.cli settings set recommendations.default_count 8
python3.11 -m src.cli settings set enrichment.enabled true
python3.11 -m src.cli settings reset recommendations.default_count
```

The value is parsed to the setting's type, with booleans as true/false and lists
comma-separated, and bounds and choices are enforced. `reset` drops the database
override.

`set`, `apply` and `reset` accept `--format json`, emitting the full refreshed
view rather than a one-line confirmation.

Restart-required settings (`web.*`, `logging.*`) persist and apply
on the next boot. The CLI says so when a change needs one.

### `settings apply`

The atomic multi-key update, the equivalent of `PUT /api/settings`:

```bash
echo '{"recommendations.default_count": 8, "recommendations.max_count": 30}' \
  | python3.11 -m src.cli settings apply --from-json -
```

Every key is validated up front, so **one bad key rejects the whole batch** and
nothing is written. The offending key and reason are printed and the command
exits non-zero. Sensitive keys are rejected here too.

### Secrets

Provider API keys are stored encrypted in the `credentials` table, never in
plaintext, so `settings set` refuses them. `set-secret` prompts with hidden
input, or reads `RECOMMENDINATOR_SECRET_VALUE` for non-interactive use.

```bash
python3.11 -m src.cli settings set-secret enrichment.providers.tmdb.api_key
python3.11 -m src.cli settings clear-secret enrichment.providers.tmdb.api_key
```

## Preferences

See [SCORING.md](SCORING.md) for what each weight does and
[CUSTOM_RULES.md](CUSTOM_RULES.md) for rule syntax.

```bash
python3.11 -m src.cli preferences get --format json
python3.11 -m src.cli preferences set-weight genre_match 3.0
python3.11 -m src.cli preferences set-length book short
python3.11 -m src.cli preferences set-toggle series_in_order off
python3.11 -m src.cli preferences set-variety 4.0      # 0.0 off to 5.0 full strength
python3.11 -m src.cli preferences reset
```

Every one of these takes `--user`, and a `--user` naming nobody is refused
rather than reporting a write it did not make.

Custom rules are natural-language preferences, matched against genres and tags:

```bash
python3.11 -m src.cli preferences custom-rules add "avoid horror"
python3.11 -m src.cli preferences custom-rules list
python3.11 -m src.cli preferences custom-rules interpret "avoid horror"
python3.11 -m src.cli preferences custom-rules remove 0
python3.11 -m src.cli preferences custom-rules clear --yes
```

At most 50 rules of 500 characters each, the same bound the web API applies.

## Enrichment

Enrichment is critical for recommendation quality. See
[ENRICHMENT_SETUP.md](ENRICHMENT_SETUP.md).

```bash
python3.11 -m src.cli enrichment start
python3.11 -m src.cli enrichment start --type movie
python3.11 -m src.cli enrichment start --retry-not-found   # providers drift over time
python3.11 -m src.cli enrichment status                    # library counts by provider
python3.11 -m src.cli enrichment job                       # the live run, if there is one
python3.11 -m src.cli enrichment stop
python3.11 -m src.cli enrichment reset                     # re-process on the next run
python3.11 -m src.cli enrichment reset --id 42             # one item, back to automatic
```

`enrichment job` and `enrichment stop` reach the run whatever started it — the
Data tab, another terminal, a backgrounded process — because the job lives in
the database rather than in the process that launched it. `job` mirrors
`GET /api/enrichment/status` and returns straight away; `status` is the library
counts, mirroring `GET /api/enrichment/stats`. A stop takes effect after the
item the run is on.

`--id` hands a single item back to automatic enrichment, which is what undoes a
manual edit to its genres, tags or description. It takes no `--provider` or
`--type` beside it, and the web dialog's **Restore automatic enrichment** does
the same thing.

## Authentication (GOG/Epic/Trakt)

```bash
python3.11 -m src.cli auth status                   # every OAuth source, enabled and connected
python3.11 -m src.cli auth connect --source gog     # browser OAuth
python3.11 -m src.cli auth connect --source trakt   # device code, prints a URL
python3.11 -m src.cli auth disconnect --source gog
```

`--source` names the provider; the token belongs to a source id. Pass
`--source-id` when yours is not called after its plugin:

```bash
python3.11 -m src.cli auth connect --source gog --source-id gog_work
python3.11 -m src.cli auth disconnect --source gog --source-id gog_work
```

## Web account

The web UI signs in to one account, which has no email and no reset link. The
CLI reads the database directly rather than going over HTTP, so it is the way
back in when that password is lost.

```bash
python3.11 -m src.cli account show
python3.11 -m src.cli account set-password    # prompts twice, hidden
python3.11 -m src.cli account set-name --username owner --display-name "The Owner"
```

A password is at least 12 characters, the same minimum the web setup screen and
**Settings → Account** apply. The password is never an argument: that would
leave it in the shell history and in every process listing. `set-password` signs
every browser out, so a session someone else holds dies with the password it was
opened under, and it refuses an unclaimed instance — claim that from the web
setup page.

`set-name` writes only the names you pass, and an empty `--display-name` clears
it. A name is trimmed and capped exactly as the web caps it, and both refuse an
over-long one in the same sentence. All three commands take `--user` and
`--format json`.

## User profile

```bash
python3.11 -m src.cli profile show --format json
python3.11 -m src.cli profile regenerate    # rebuild from current library data
```
