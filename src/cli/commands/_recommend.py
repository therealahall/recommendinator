"""The ``recommend`` command."""

from __future__ import annotations

import json

import click
from tabulate import tabulate

from src.cli._shared import abort_after_failure
from src.models.content import ContentType, get_enum_value
from src.models.detail_fields import DETAIL_FIELDS

#: What ``GET /api/recommendations`` answers with. The engine walks the
#: library, so its faults quote item titles.
RECOMMEND_FAILED = "Failed to generate recommendations"


@click.command()
@click.option(
    "--type",
    "content_type_str",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
    required=True,
    help="Content type to get recommendations for",
)
@click.option(
    "--count",
    type=click.IntRange(min=1),
    default=5,
    help="Number of recommendations to generate (capped by config 'recommendations.max_count').",
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
    help="User ID for personalized preferences",
)
@click.pass_context
def recommend(
    ctx: click.Context,
    content_type_str: str,
    count: int,
    output_format: str,
    user_id: int,
) -> None:
    """Get personalized recommendations."""
    content_type = ContentType.from_string(content_type_str)

    # Enforce config-driven max_count (matches web API /api/recommendations).
    max_count = ctx.obj["config"].get("recommendations", {}).get("max_count", 20)
    if count > max_count:
        click.echo(
            f"Error: --count {count} exceeds configured max_count={max_count}.",
            err=True,
        )
        raise click.Abort()

    engine = ctx.obj["engine"]
    storage = ctx.obj["storage"]

    # Chatter goes to stderr because stdout is the data channel: this line ran
    # ahead of the format branch, so `--format json` piped into a parser broke
    # on it.
    click.echo(f"Generating {count} {content_type_str} recommendations...", err=True)

    try:
        # Load user preferences
        user_preference_config = storage.get_user_preference_config(user_id)

        recommendations = engine.generate_recommendations(
            content_type=content_type,
            count=count,
            user_preference_config=user_preference_config,
        )

        if not recommendations:
            if output_format == "json":
                # Emit an empty JSON array (matches web GET /api/recommendations).
                click.echo(json.dumps([]))
            else:
                # Worded exactly as the web zero-result state, which names the
                # type for the same reason: an empty run and an empty library
                # read identically otherwise.
                label = content_type_str.replace("_", " ")
                click.echo(
                    f"No {label} recommendations. They come from items you have "
                    "not consumed yet — try syncing a source, or adding items "
                    "to your library."
                )
            return

        if output_format == "json":
            # The shared payload is the web API RecommendationResponse shape
            output = [rec.to_payload() for rec in recommendations]
            click.echo(json.dumps(output, indent=2))
        else:
            # Table output
            table_data = []
            for rank, rec in enumerate(recommendations, 1):
                item = rec.item
                author = item.author or "N/A"
                reasoning = rec.reasoning
                penalty = rec.variety_penalty
                if penalty > 0:
                    # Surface the stepped variety penalty inline so CLI users
                    # can see why a recently finished genre was demoted.
                    reasoning = (
                        f"{reasoning}\n(variety penalty -{round(penalty * 100)}%)"
                    )
                table_data.append(
                    [
                        rank,
                        item.title,
                        author,
                        f"{rec.score:.2f}",
                        reasoning,
                    ]
                )

            # Every row is the one requested type, so the column is labelled
            # the way that type names its creator: "Director" for a movie.
            creator = DETAIL_FIELDS[get_enum_value(content_type)].creator_column
            headers = ["#", "Title", creator.title(), "Score", "Reasoning"]
            click.echo(tabulate(table_data, headers=headers, tablefmt="grid"))

    except Exception as error:
        abort_after_failure(ctx, RECOMMEND_FAILED, error)
