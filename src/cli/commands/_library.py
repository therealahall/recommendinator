"""The ``library`` group."""

from __future__ import annotations

import json
from collections.abc import Collection
from pathlib import Path
from typing import cast

import click
from tabulate import tabulate

from src.cli._shared import (
    abort_with,
    emit_view,
    is_blank_review,
    series_label,
    write_output_file,
)
from src.models.content import (
    MAX_CREATOR_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_GENRE_TAG_LENGTH,
    MAX_GENRES,
    MAX_RELEASE_YEAR,
    MAX_REVIEW_LENGTH,
    MAX_TAGS,
    MIN_RELEASE_YEAR,
    ConsumptionStatus,
    ContentItem,
    ContentType,
    EnrichmentFilter,
    get_enum_value,
)
from src.models.detail_fields import DETAIL_FIELDS
from src.storage.manager import (
    MAX_DECLINE_OTHERS,
    SUGGESTION_PAGE_DEFAULT,
    SUGGESTION_PAGE_MAX,
    DeclinedPair,
    DuplicateSide,
    DuplicateSuggestion,
    MergeError,
    MergeEvidence,
    UncorrectableFieldError,
    unset_if_none,
)
from src.utils.duplicate_serialization import (
    ALSO_OFFERED_NOTE,
    decline_refusal_message,
    declined_pair_to_dict,
    merge_evidence_label,
    merge_to_dict,
    skipped_works_note,
    suggestion_evidence_label,
    suggestion_page_to_dict,
)
from src.utils.export import export_items_csv, export_items_json
from src.utils.item_serialization import ignore_result_to_dict, item_to_dict
from src.utils.series import MAX_SEASONS
from src.utils.sorting import MAX_SEARCH_LENGTH


@click.group()
def library() -> None:
    """Manage your content library."""


@library.command("list")
@click.option(
    "--type",
    "content_type_str",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
    default=None,
    help="Filter by content type",
)
@click.option(
    "--status",
    "status_str",
    type=click.Choice(
        ["unread", "currently_consuming", "completed"],
        case_sensitive=False,
    ),
    default=None,
    help="Filter by consumption status",
)
@click.option(
    "--sort",
    "sort_by",
    type=click.Choice(
        ["title", "updated_at", "rating", "created_at"], case_sensitive=False
    ),
    default="title",
    help="Sort order (default: title)",
)
@click.option(
    "--search",
    default=None,
    help="Filter by title, creator or series (matches web API search)",
)
@click.option(
    "--show-ignored",
    is_flag=True,
    help="Include ignored items",
)
@click.option(
    "--enrichment",
    "enrichment_str",
    type=click.Choice(["enriched", "not_enriched"], case_sensitive=False),
    default=None,
    help="Filter by enrichment state (default: all)",
)
@click.option(
    "--needs-rating",
    is_flag=True,
    help="Only completed items with no rating (overrides --status)",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1, max=200),
    default=50,
    help="Max items to return (1-200, default 50, matches web API)",
)
@click.option(
    "--offset",
    type=click.IntRange(min=0),
    default=0,
    help="Items to skip (for pagination)",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_list(
    ctx: click.Context,
    content_type_str: str | None,
    status_str: str | None,
    sort_by: str,
    search: str | None,
    show_ignored: bool,
    enrichment_str: str | None,
    needs_rating: bool,
    limit: int | None,
    offset: int,
    output_format: str,
    user_id: int,
) -> None:
    """List library items with filters."""
    if search is not None and len(search) > MAX_SEARCH_LENGTH:
        click.echo(
            f"Error: --search must be at most {MAX_SEARCH_LENGTH} characters.",
            err=True,
        )
        raise click.Abort()

    storage = ctx.obj["storage"]

    content_type = (
        ContentType.from_string(content_type_str) if content_type_str else None
    )
    consumption_status = ConsumptionStatus(status_str) if status_str else None
    enrichment: EnrichmentFilter | None = (
        cast(EnrichmentFilter, enrichment_str.lower()) if enrichment_str else None
    )

    # needs_rating means "completed AND unrated": completed status is implied
    # and takes precedence over any explicitly-passed --status (matches web API).
    if needs_rating:
        consumption_status = ConsumptionStatus.COMPLETED

    items: list[ContentItem] = storage.get_content_items(
        user_id=user_id,
        content_type=content_type,
        status=consumption_status,
        unrated_only=needs_rating,
        sort_by=sort_by,
        search=search,
        include_ignored=show_ignored,
        limit=limit,
        offset=offset,
        enrichment=enrichment,
    )

    if output_format == "json":
        # Always emit a JSON array, even when empty (matches web GET /api/items).
        output = [item_to_dict(item) for item in items]
        click.echo(json.dumps(output, indent=2))
        return

    if not items:
        click.echo("No items found.")
        return

    table_data = []
    for item in items:
        table_data.append(
            [
                item.db_id,
                item.title,
                series_label(item.metadata),
                item.author or "N/A",
                get_enum_value(item.content_type),
                get_enum_value(item.status),
                "N/A" if item.rating is None else item.rating,
                "Yes" if item.enriched else "No",
            ]
        )
    # One listing mixes the types, so the column takes the name they share
    # rather than any one type's ("Author" over a director, and so on).
    headers = [
        "ID",
        "Title",
        "Series",
        "Creator",
        "Type",
        "Status",
        "Rating",
        "Enriched",
    ]
    click.echo(tabulate(table_data, headers=headers, tablefmt="grid"))


def _enriched_label(item: ContentItem) -> str:
    """Yes/No, saying when it is the manual state ``enrichment reset`` undoes."""
    if not item.enriched:
        return "No"
    return "Yes (manual)" if item.manually_enriched else "Yes"


@library.command("show")
@click.option("--id", "item_id", type=int, required=True, help="Item database ID")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_show(
    ctx: click.Context, item_id: int, output_format: str, user_id: int
) -> None:
    """Show details of a single library item."""
    storage = ctx.obj["storage"]

    item = storage.get_content_item(item_id, user_id=user_id)
    if item is None:
        click.echo(f"Error: Item {item_id} not found.", err=True)
        raise click.Abort()

    if output_format == "json":
        click.echo(json.dumps(item_to_dict(item), indent=2))
    else:
        serialized = item_to_dict(item)
        genres = serialized["genres"]
        tags = serialized["tags"]
        description = serialized["description"]
        content_type = get_enum_value(item.content_type)
        # A book has an author and a movie a director, so the row is labelled
        # with the creator column the type declares: "director" as "Director".
        creator_label = DETAIL_FIELDS[content_type].creator_column.title()
        release_year = serialized["release_year"]
        table_data = [
            ["Title", item.title],
            ["Series", series_label(item.metadata)],
            [creator_label, item.author or "N/A"],
            ["Release Year", "N/A" if release_year is None else release_year],
            ["Type", content_type],
            ["Status", get_enum_value(item.status)],
            ["Rating", "N/A" if item.rating is None else item.rating],
            ["Review", item.review or "N/A"],
            [
                "Date Completed",
                item.date_completed.isoformat() if item.date_completed else "N/A",
            ],
            [
                "External IDs",
                ", ".join(
                    f"{pair.source}: {pair.external_id}" for pair in item.external_ids
                )
                or "N/A",
            ],
            ["Ignored", "Yes" if item.ignored else "No"],
            ["Enriched", _enriched_label(item)],
            ["Genres", ", ".join(cast(list[str], genres)) or "N/A"],
            ["Tags", ", ".join(cast(list[str], tags)) or "N/A"],
            ["Description", description or "N/A"],
        ]
        click.echo(tabulate(table_data, tablefmt="grid"))


@library.command("edit")
@click.option("--id", "item_id", type=int, required=True, help="Item database ID")
@click.option(
    "--status",
    "status_str",
    type=click.Choice(
        ["unread", "currently_consuming", "completed"],
        case_sensitive=False,
    ),
    default=None,
    help="New status",
)
@click.option(
    "--rating",
    type=click.IntRange(min=1, max=5),
    default=None,
    help="New rating (1-5)",
)
@click.option(
    "--clear-rating",
    is_flag=True,
    help="Remove the rating, putting the item back in --needs-rating",
)
@click.option("--review", default=None, help="New review text")
@click.option(
    "--clear-review",
    is_flag=True,
    help="Remove the review text",
)
@click.option(
    "--seasons-watched",
    default=None,
    help=f"Comma-separated list of watched season numbers (1-{MAX_SEASONS})",
)
@click.option(
    "--clear-seasons",
    is_flag=True,
    help="Remove every watched season",
)
@click.option(
    "--genre",
    "genres",
    multiple=True,
    help="Manual genre (repeatable); replaces existing genres and marks enriched",
)
@click.option(
    "--clear-genres",
    is_flag=True,
    help="Remove every genre",
)
@click.option(
    "--tag",
    "tags",
    multiple=True,
    help="Manual tag (repeatable); replaces existing tags and marks enriched",
)
@click.option(
    "--clear-tags",
    is_flag=True,
    help="Remove every tag",
)
@click.option(
    "--description",
    default=None,
    help="Manual description; replaces the existing one and marks enriched",
)
@click.option(
    "--release-year",
    type=click.IntRange(min=MIN_RELEASE_YEAR, max=MAX_RELEASE_YEAR),
    default=None,
    help="Correct the year the work came out (books state none)",
)
@click.option(
    "--creator",
    default=None,
    help="Correct the author, director, creators or developer",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_edit(
    ctx: click.Context,
    item_id: int,
    status_str: str | None,
    rating: int | None,
    clear_rating: bool,
    review: str | None,
    clear_review: bool,
    seasons_watched: str | None,
    clear_seasons: bool,
    genres: tuple[str, ...],
    clear_genres: bool,
    tags: tuple[str, ...],
    clear_tags: bool,
    description: str | None,
    release_year: int | None,
    creator: str | None,
    output_format: str,
    user_id: int,
) -> None:
    """Edit an item's status, rating, review, release year, creator or metadata."""
    if (
        status_str is None
        and rating is None
        and not clear_rating
        and review is None
        and not clear_review
        and seasons_watched is None
        and not clear_seasons
        and not genres
        and not clear_genres
        and not tags
        and not clear_tags
        and description is None
        and release_year is None
        and creator is None
    ):
        click.echo(
            "Error: Provide at least one of --status, --rating, --clear-rating, "
            "--review, --clear-review, --seasons-watched, --clear-seasons, "
            "--genre, --clear-genres, --tag, --clear-tags, --description, "
            "--release-year, --creator.",
            err=True,
        )
        raise click.Abort()

    if rating is not None and clear_rating:
        click.echo(
            "Error: --rating and --clear-rating cannot be used together.", err=True
        )
        raise click.Abort()
    if review is not None and clear_review:
        click.echo(
            "Error: --review and --clear-review cannot be used together.", err=True
        )
        raise click.Abort()
    if seasons_watched is not None and clear_seasons:
        click.echo(
            "Error: --seasons-watched and --clear-seasons cannot be used together.",
            err=True,
        )
        raise click.Abort()
    if genres and clear_genres:
        click.echo(
            "Error: --genre and --clear-genres cannot be used together.", err=True
        )
        raise click.Abort()
    if tags and clear_tags:
        click.echo("Error: --tag and --clear-tags cannot be used together.", err=True)
        raise click.Abort()
    if is_blank_review(review):
        click.echo(
            "Error: --review cannot be empty. Use --clear-review to remove one.",
            err=True,
        )
        raise click.Abort()
    if creator is not None:
        creator = creator.strip()
        if not creator:
            abort_with("--creator cannot be empty.")
        if len(creator) > MAX_CREATOR_LENGTH:
            abort_with(f"--creator must be at most {MAX_CREATOR_LENGTH} characters.")

    storage = ctx.obj["storage"]

    item = storage.get_content_item(item_id, user_id=user_id)
    if item is None:
        click.echo(f"Error: Item {item_id} not found.", err=True)
        raise click.Abort()

    parsed_seasons: list[int] | None = [] if clear_seasons else None
    if seasons_watched is not None:
        try:
            parsed_seasons = [
                int(token.strip()) for token in seasons_watched.split(",")
            ]
        except ValueError:
            click.echo(
                "Error: --seasons-watched must be comma-separated integers (e.g. 1,2,3).",
                err=True,
            )
            raise click.Abort() from None
        if len(parsed_seasons) > MAX_SEASONS:
            click.echo(
                f"Error: --seasons-watched accepts at most {MAX_SEASONS} seasons.",
                err=True,
            )
            raise click.Abort()
        if any(not 1 <= season <= MAX_SEASONS for season in parsed_seasons):
            click.echo(
                f"Error: --seasons-watched values must each be between 1 and {MAX_SEASONS}.",
                err=True,
            )
            raise click.Abort()

    genre_list = [] if clear_genres else (list(genres) if genres else None)
    tag_list = [] if clear_tags else (list(tags) if tags else None)
    if genre_list is not None and len(genre_list) > MAX_GENRES:
        click.echo(
            f"Error: --genre accepts at most {MAX_GENRES} values.",
            err=True,
        )
        raise click.Abort()
    if tag_list is not None and len(tag_list) > MAX_TAGS:
        click.echo(
            f"Error: --tag accepts at most {MAX_TAGS} values.",
            err=True,
        )
        raise click.Abort()
    if any(len(value) > MAX_GENRE_TAG_LENGTH for value in genres + tags):
        click.echo(
            f"Error: each --genre/--tag must be at most {MAX_GENRE_TAG_LENGTH} "
            "characters.",
            err=True,
        )
        raise click.Abort()
    if description is not None and len(description) > MAX_DESCRIPTION_LENGTH:
        click.echo(
            f"Error: --description must be at most {MAX_DESCRIPTION_LENGTH} "
            "characters.",
            err=True,
        )
        raise click.Abort()
    if review is not None and len(review) > MAX_REVIEW_LENGTH:
        click.echo(
            f"Error: --review must be at most {MAX_REVIEW_LENGTH} characters.",
            err=True,
        )
        raise click.Abort()

    try:
        updated = storage.update_item_from_ui(
            db_id=item_id,
            status=unset_if_none(status_str),
            rating=None if clear_rating else unset_if_none(rating),
            review=None if clear_review else unset_if_none(review),
            seasons_watched=parsed_seasons,
            genres=genre_list,
            tags=tag_list,
            description=description,
            release_year=release_year,
            creator=creator,
            user_id=user_id,
        )
    except UncorrectableFieldError as error:
        abort_with(str(error))

    if not updated:
        click.echo(f"Error: Failed to update item {item_id}.", err=True)
        raise click.Abort()

    def refreshed() -> dict[str, object]:
        edited = storage.get_content_item(item_id, user_id=user_id)
        if edited is None:
            abort_with(f"Item {item_id} not found after update.")
        return item_to_dict(edited)

    emit_view(output_format, refreshed, f"Updated item {item_id} ({item.title}).")


def _apply_ignored(
    ctx: click.Context,
    item_id: int,
    user_id: int,
    output_format: str,
    ignored: bool,
) -> None:
    storage = ctx.obj["storage"]

    item = storage.get_content_item(item_id, user_id=user_id)
    if item is None or not storage.set_item_ignored(
        db_id=item_id, ignored=ignored, user_id=user_id
    ):
        abort_with(f"Item {item_id} not found.")

    title = item.title
    emit_view(
        output_format,
        lambda: ignore_result_to_dict(item_id, title, ignored),
        f"{'Ignored' if ignored else 'Unignored'} item {item_id}.",
    )


@library.command("ignore")
@click.option("--id", "item_id", type=int, required=True, help="Item database ID")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_ignore(
    ctx: click.Context, item_id: int, output_format: str, user_id: int
) -> None:
    """Ignore an item (exclude from recommendations)."""
    _apply_ignored(ctx, item_id, user_id, output_format, True)


@library.command("unignore")
@click.option("--id", "item_id", type=int, required=True, help="Item database ID")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_unignore(
    ctx: click.Context, item_id: int, output_format: str, user_id: int
) -> None:
    """Unignore an item (include in recommendations again)."""
    _apply_ignored(ctx, item_id, user_id, output_format, False)


@library.command("export")
@click.option(
    "--type",
    "content_type_str",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
    default=None,
    help="Content type to export (default: every type)",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["csv", "json"], case_sensitive=False),
    default="csv",
    help="Export format (default: csv)",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),  # type: ignore[type-var]
    default=None,
    help="Output file path (default: stdout)",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Overwrite an existing --output file without asking",
)
@click.pass_context
def library_export(
    ctx: click.Context,
    content_type_str: str | None,
    output_format: str,
    output_path: Path | None,
    user_id: int,
    yes: bool,
) -> None:
    """Export library items as CSV or JSON. No --type exports every type."""
    storage = ctx.obj["storage"]
    content_type = (
        ContentType.from_string(content_type_str) if content_type_str else None
    )

    items: list[ContentItem] = storage.get_content_items(
        user_id=user_id,
        content_type=content_type,
        include_ignored=True,
    )

    if output_format == "json":
        data = export_items_json(items, content_type)
    else:
        data = export_items_csv(items, content_type)

    if output_path:
        if not write_output_file(
            ctx, output_path, data.encode("utf-8"), assume_yes=yes
        ):
            return
        click.echo(f"Exported {len(items)} items to {output_path}")
    else:
        click.echo(data, nl=False)


def _side_summary(side: DuplicateSide, also_offered: Collection[int] = ()) -> str:
    summary = f"{side.title} ({side.creator or 'N/A'}, {side.source or 'N/A'})"
    if side.db_id not in also_offered:
        return summary
    return f"{summary}\n{ALSO_OFFERED_NOTE}"


def _proposed(suggestion: DuplicateSuggestion) -> DuplicateSide:
    return next(
        side for side in suggestion.copies if side.db_id == suggestion.survivor_id
    )


def _pair_summary(pair: DeclinedPair) -> str:
    return (
        f"{pair.one_title} (#{pair.one_id}) and"
        f" {pair.other_title} (#{pair.other_id})"
    )


@library.command("duplicates")
@click.option(
    "--type",
    "content_type_str",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
    default=None,
    help="Filter by content type",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1, max=SUGGESTION_PAGE_MAX),
    default=SUGGESTION_PAGE_DEFAULT,
    help=f"Max works to offer (1-{SUGGESTION_PAGE_MAX}, matches web API)",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_duplicates(
    ctx: click.Context,
    content_type_str: str | None,
    limit: int,
    output_format: str,
    user_id: int,
) -> None:
    """List each work's suspected copies and what matched them."""
    storage = ctx.obj["storage"]

    page = storage.list_duplicate_suggestions(
        user_id=user_id,
        content_type=(
            ContentType.from_string(content_type_str) if content_type_str else None
        ),
        limit=limit,
    )

    if output_format == "json":
        click.echo(json.dumps(suggestion_page_to_dict(page), indent=2))
        return

    skipped = skipped_works_note(page.skipped_works)
    if not page.suggestions:
        click.echo(skipped or "No suspected duplicates.")
        return

    table_data = [
        [
            suggestion.survivor_id,
            _side_summary(_proposed(suggestion), page.also_offered),
            "\n".join(
                f"#{side.db_id} {_side_summary(side, page.also_offered)}"
                for side in suggestion.copies
                if side.db_id != suggestion.survivor_id
            ),
            suggestion.content_type,
            suggestion_evidence_label(suggestion.evidence),
        ]
        for suggestion in page.suggestions
    ]
    headers = ["Keep ID", "Keep", "Other copies", "Type", "Evidence"]
    click.echo(tabulate(table_data, headers=headers, tablefmt="grid"))
    counted = f"Showing {len(page.suggestions)} of {page.total} suspected duplicates."
    click.echo(f"{counted} {skipped}" if skipped else counted)


@library.command("merge")
@click.option(
    "--survivor",
    "survivor_id",
    type=int,
    required=True,
    help="Database ID of the item to keep",
)
@click.option(
    "--absorbed",
    "absorbed_id",
    type=int,
    required=True,
    help="Database ID of the item merged into it",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_merge(
    ctx: click.Context,
    survivor_id: int,
    absorbed_id: int,
    output_format: str,
    user_id: int,
) -> None:
    """Merge one item into another, keeping --survivor."""
    storage = ctx.obj["storage"]

    try:
        record = storage.merge_content_items(
            survivor_id, absorbed_id, MergeEvidence.MANUAL, user_id=user_id
        )
    except MergeError as error:
        abort_with(str(error))

    emit_view(
        output_format,
        lambda: merge_to_dict(record),
        f"Merged {record.absorbed_title} (#{record.absorbed_id}) into"
        f" {record.survivor_title} (#{record.survivor_id}) as merge {record.id}.",
    )


@library.command("unmerge")
@click.option(
    "--merge-id",
    "merge_id",
    type=int,
    required=True,
    help="Merge ID, as listed by library merges",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_unmerge(
    ctx: click.Context, merge_id: int, output_format: str, user_id: int
) -> None:
    """Undo one merge, putting the absorbed item back."""
    storage = ctx.obj["storage"]

    try:
        record = storage.unmerge_content_items(merge_id, user_id=user_id)
    except MergeError as error:
        abort_with(str(error))
    if record is None:
        abort_with(f"Merge {merge_id} not found.")

    emit_view(
        output_format,
        lambda: merge_to_dict(record),
        f"Unmerged {record.absorbed_title} (#{record.absorbed_id}) from"
        f" {record.survivor_title} (#{record.survivor_id}).",
    )


@library.command("merges")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_merges(ctx: click.Context, output_format: str, user_id: int) -> None:
    """List the merges in force, newest first."""
    storage = ctx.obj["storage"]

    records = storage.list_content_item_merges(user_id=user_id)

    if output_format == "json":
        click.echo(json.dumps([merge_to_dict(record) for record in records], indent=2))
        return

    if not records:
        click.echo("No merges.")
        return

    table_data = [
        [
            record.id,
            f"{record.absorbed_title} (#{record.absorbed_id})",
            f"{record.survivor_title} (#{record.survivor_id})",
            merge_evidence_label(record),
            record.merged_at,
        ]
        for record in records
    ]
    headers = ["Merge ID", "Absorbed", "Into", "Evidence", "Merged At"]
    click.echo(tabulate(table_data, headers=headers, tablefmt="grid"))


@library.command("decline-duplicate")
@click.option(
    "--one",
    "one_id",
    type=int,
    required=True,
    help="Database ID of the copy that is a different work",
)
@click.option(
    "--other",
    "other_ids",
    type=int,
    required=True,
    multiple=True,
    help="Database ID of a copy it is not, repeated for a whole block",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_decline_duplicate(
    ctx: click.Context,
    one_id: int,
    other_ids: tuple[int, ...],
    output_format: str,
    user_id: int,
) -> None:
    """Keep one copy off the list for good, against every --other named."""
    storage = ctx.obj["storage"]

    if len(other_ids) > MAX_DECLINE_OTHERS:
        abort_with(f"--other accepts at most {MAX_DECLINE_OTHERS} values.")

    pairs = storage.decline_duplicate_suggestion(one_id, other_ids, user_id=user_id)
    if not pairs:
        abort_with(decline_refusal_message(one_id, other_ids))

    if output_format == "json":
        click.echo(
            json.dumps([declined_pair_to_dict(pair) for pair in pairs], indent=2)
        )
        return
    for pair in pairs:
        click.echo(f"{_pair_summary(pair)} will not be offered as duplicates again.")


@library.command("declined-duplicates")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_declined_duplicates(
    ctx: click.Context, output_format: str, user_id: int
) -> None:
    """List the duplicate pairs you have refused, and can undo."""
    storage = ctx.obj["storage"]

    pairs = storage.list_declined_duplicates(user_id=user_id)

    if output_format == "json":
        click.echo(
            json.dumps([declined_pair_to_dict(pair) for pair in pairs], indent=2)
        )
        return

    if not pairs:
        click.echo("No declined duplicates.")
        return

    table_data = [
        [pair.one_id, pair.one_title, pair.other_id, pair.other_title] for pair in pairs
    ]
    headers = ["One ID", "One", "Other ID", "Other"]
    click.echo(tabulate(table_data, headers=headers, tablefmt="grid"))


@library.command("undecline-duplicate")
@click.option(
    "--one",
    "one_id",
    type=int,
    required=True,
    help="Database ID of one item in the pair",
)
@click.option(
    "--other",
    "other_id",
    type=int,
    required=True,
    help="Database ID of the other item in the pair",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_undecline_duplicate(
    ctx: click.Context,
    one_id: int,
    other_id: int,
    output_format: str,
    user_id: int,
) -> None:
    """Offer a refused duplicate pair again."""
    storage = ctx.obj["storage"]

    try:
        pair = storage.undecline_duplicate_suggestion(one_id, other_id, user_id=user_id)
    except MergeError as error:
        abort_with(str(error))
    if pair is None:
        abort_with(f"Items {one_id} and {other_id} are not a declined pair.")

    emit_view(
        output_format,
        lambda: declined_pair_to_dict(pair),
        f"{_pair_summary(pair)} may be offered as duplicates again.",
    )
