"""The ``library`` group."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import click
from tabulate import tabulate

from src.cli._shared import is_blank_review
from src.models.content import (
    MAX_DESCRIPTION_LENGTH,
    MAX_GENRE_TAG_LENGTH,
    MAX_GENRES,
    MAX_REVIEW_LENGTH,
    MAX_TAGS,
    ConsumptionStatus,
    ContentItem,
    ContentType,
    EnrichmentFilter,
    get_enum_value,
)
from src.models.detail_fields import DETAIL_FIELDS
from src.storage.manager import unset_if_none
from src.utils.export import export_items_csv, export_items_json
from src.utils.item_serialization import item_to_dict
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
    help="Filter by title or creator (matches web API search)",
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
                item.author or "N/A",
                get_enum_value(item.content_type),
                get_enum_value(item.status),
                "N/A" if item.rating is None else item.rating,
                "Yes" if item.enriched else "No",
            ]
        )
    # One listing mixes the types, so the column takes the name they share
    # rather than any one type's ("Author" over a director, and so on).
    headers = ["ID", "Title", "Creator", "Type", "Status", "Rating", "Enriched"]
    click.echo(tabulate(table_data, headers=headers, tablefmt="grid"))


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
        table_data = [
            ["Title", item.title],
            [creator_label, item.author or "N/A"],
            ["Type", content_type],
            ["Status", get_enum_value(item.status)],
            ["Rating", "N/A" if item.rating is None else item.rating],
            ["Review", item.review or "N/A"],
            [
                "Date Completed",
                item.date_completed.isoformat() if item.date_completed else "N/A",
            ],
            ["Ignored", "Yes" if item.ignored else "No"],
            ["Enriched", "Yes" if item.enriched else "No"],
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
    "--genre",
    "genres",
    multiple=True,
    help="Manual genre (repeatable); replaces existing genres and marks enriched",
)
@click.option(
    "--tag",
    "tags",
    multiple=True,
    help="Manual tag (repeatable); replaces existing tags and marks enriched",
)
@click.option(
    "--description",
    default=None,
    help="Manual description; replaces the existing one and marks enriched",
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
    genres: tuple[str, ...],
    tags: tuple[str, ...],
    description: str | None,
    user_id: int,
) -> None:
    """Edit an item's status, rating, review, or manual enrichment metadata."""
    if (
        status_str is None
        and rating is None
        and not clear_rating
        and review is None
        and not clear_review
        and seasons_watched is None
        and not genres
        and not tags
        and description is None
    ):
        click.echo(
            "Error: Provide at least one of --status, --rating, --clear-rating, "
            "--review, --clear-review, --seasons-watched, --genre, --tag, "
            "--description.",
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
    if is_blank_review(review):
        click.echo(
            "Error: --review cannot be empty. Use --clear-review to remove one.",
            err=True,
        )
        raise click.Abort()

    storage = ctx.obj["storage"]

    # Look up the item to get current status if not provided
    item = storage.get_content_item(item_id, user_id=user_id)
    if item is None:
        click.echo(f"Error: Item {item_id} not found.", err=True)
        raise click.Abort()

    effective_status = status_str if status_str else get_enum_value(item.status)

    parsed_seasons: list[int] | None = None
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
        # Mirror the web ItemEditRequest bounds so both interfaces reject the
        # same out-of-range season values instead of silently storing them.
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

    genre_list = list(genres) if genres else None
    tag_list = list(tags) if tags else None
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

    # An unsupplied flag leaves the stored value alone; only --clear-rating /
    # --clear-review send the None that storage writes as NULL, matching the
    # explicit null the web edit dialog sends.
    updated = storage.update_item_from_ui(
        db_id=item_id,
        status=effective_status,
        rating=None if clear_rating else unset_if_none(rating),
        review=None if clear_review else unset_if_none(review),
        seasons_watched=parsed_seasons,
        genres=genre_list,
        tags=tag_list,
        description=description,
        user_id=user_id,
    )

    if updated:
        click.echo(f"Updated item {item_id} ({item.title}).")
    else:
        click.echo(f"Error: Failed to update item {item_id}.", err=True)
        raise click.Abort()


@library.command("ignore")
@click.option("--id", "item_id", type=int, required=True, help="Item database ID")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_ignore(ctx: click.Context, item_id: int, user_id: int) -> None:
    """Ignore an item (exclude from recommendations)."""
    storage = ctx.obj["storage"]

    if storage.set_item_ignored(db_id=item_id, ignored=True, user_id=user_id):
        click.echo(f"Ignored item {item_id}.")
    else:
        click.echo(f"Error: Item {item_id} not found.", err=True)
        raise click.Abort()


@library.command("unignore")
@click.option("--id", "item_id", type=int, required=True, help="Item database ID")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_unignore(ctx: click.Context, item_id: int, user_id: int) -> None:
    """Unignore an item (include in recommendations again)."""
    storage = ctx.obj["storage"]

    if storage.set_item_ignored(db_id=item_id, ignored=False, user_id=user_id):
        click.echo(f"Unignored item {item_id}.")
    else:
        click.echo(f"Error: Item {item_id} not found.", err=True)
        raise click.Abort()


@library.command("export")
@click.option(
    "--type",
    "content_type_str",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
    required=True,
    help="Content type to export",
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
@click.pass_context
def library_export(
    ctx: click.Context,
    content_type_str: str,
    output_format: str,
    output_path: Path | None,
    user_id: int,
) -> None:
    """Export library items as CSV or JSON."""
    storage = ctx.obj["storage"]
    content_type = ContentType.from_string(content_type_str)

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
        output_path.write_text(data, encoding="utf-8")
        click.echo(f"Exported {len(items)} items to {output_path}")
    else:
        click.echo(data, nl=False)
