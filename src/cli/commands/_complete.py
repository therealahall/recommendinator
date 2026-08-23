"""The ``complete`` command."""

from __future__ import annotations

import click

from src.cli._shared import abort_after_failure, is_blank_review
from src.models.content import (
    MAX_CREATOR_LENGTH,
    MAX_REVIEW_LENGTH,
    MAX_TITLE_LENGTH,
    ConsumptionStatus,
    ContentItem,
    ContentType,
)
from src.utils.text import is_blank

#: What ``POST /api/complete`` answers with; the title and review that failed
#: to write stay in the log.
COMPLETE_FAILED = "Failed to mark content as completed"


@click.command()
@click.option(
    "--type",
    "content_type_str",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
    required=True,
    help="Content type",
)
@click.option("--title", required=True, help="Title of the content")
@click.option("--author", help="Creator: author, director, creator or developer")
@click.option(
    "--rating",
    type=int,
    help="Rating (1-5)",
)
@click.option("--review", help="Review text")
@click.pass_context
def complete(
    ctx: click.Context,
    content_type_str: str,
    title: str,
    author: str | None,
    rating: int | None,
    review: str | None,
) -> None:
    """Mark content as completed.

    A rating or review given here replaces the stored one, so an empty
    --review is refused rather than written over a review you wrote.
    Mirrors the web API POST /api/complete.
    """
    content_type = ContentType.from_string(content_type_str)

    storage = ctx.obj["storage"]

    # Validate rating
    if rating is not None and (rating < 1 or rating > 5):
        click.echo("Error: Rating must be between 1 and 5", err=True)
        raise click.Abort()

    if is_blank_review(review):
        click.echo("Error: --review cannot be empty.", err=True)
        raise click.Abort()

    if is_blank(title):
        click.echo("Error: --title cannot be empty.", err=True)
        raise click.Abort()

    if len(title) > MAX_TITLE_LENGTH:
        click.echo(
            f"Error: --title must be at most {MAX_TITLE_LENGTH} characters.",
            err=True,
        )
        raise click.Abort()

    if author is not None and len(author) > MAX_CREATOR_LENGTH:
        click.echo(
            f"Error: --author must be at most {MAX_CREATOR_LENGTH} characters.",
            err=True,
        )
        raise click.Abort()

    if review is not None and len(review) > MAX_REVIEW_LENGTH:
        click.echo(
            f"Error: --review must be at most {MAX_REVIEW_LENGTH} characters.",
            err=True,
        )
        raise click.Abort()

    item = ContentItem(
        id=None,  # Will be generated
        title=title,
        author=author,
        content_type=content_type,
        status=ConsumptionStatus.COMPLETED,
        rating=rating,
        review=review,
    )

    try:
        db_id = storage.complete_content_item(item)
    except Exception as error:
        abort_after_failure(ctx, COMPLETE_FAILED, error)

    click.echo(f"Marked '{title}' as completed (ID: {db_id})")
