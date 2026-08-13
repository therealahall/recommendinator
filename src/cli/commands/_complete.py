"""The ``complete`` command."""

from __future__ import annotations

import click

from src.cli._shared import is_blank_review
from src.config.service import get_feature_flags
from src.models.content import ConsumptionStatus, ContentItem, ContentType


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
    embedding_gen = ctx.obj["embedding_gen"]
    config = ctx.obj["config"]

    # Check if embeddings are enabled
    use_embeddings = get_feature_flags(config)["use_embeddings"]

    # Validate rating
    if rating is not None and (rating < 1 or rating > 5):
        click.echo("Error: Rating must be between 1 and 5", err=True)
        raise click.Abort()

    if is_blank_review(review):
        click.echo("Error: --review cannot be empty.", err=True)
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
        # Only generate embedding if AI features are enabled
        embedding = None
        if use_embeddings:
            embedding = embedding_gen.generate_content_embedding(item)
        db_id = storage.complete_content_item(item, embedding=embedding)
    except Exception as error:
        click.echo(f"Error marking content as completed: {error}", err=True)
        raise click.Abort() from error

    click.echo(f"Marked '{title}' as completed (ID: {db_id})")
