"""CLI commands."""

import click
import json
from pathlib import Path
from typing import Optional
from tabulate import tabulate

from src.models.content import ContentType, ConsumptionStatus
from src.ingestion.sources.goodreads import parse_goodreads_csv
from src.ingestion.sources.steam import parse_steam_games, SteamAPIError


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
    type=int,
    default=5,
    help="Number of recommendations to generate",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--use-llm",
    is_flag=True,
    help="Use LLM for enhanced recommendation reasoning",
)
@click.pass_context
def recommend(
    ctx: click.Context,
    content_type_str: str,
    count: int,
    output_format: str,
    use_llm: bool,
) -> None:
    """Get personalized recommendations."""
    # Map string to ContentType enum
    type_map = {
        "book": ContentType.BOOK,
        "movie": ContentType.MOVIE,
        "tv_show": ContentType.TV_SHOW,
        "video_game": ContentType.VIDEO_GAME,
    }
    content_type = type_map[content_type_str.lower()]

    engine = ctx.obj["engine"]

    click.echo(f"Generating {count} {content_type_str} recommendations...")

    try:
        recommendations = engine.generate_recommendations(
            content_type=content_type, count=count, use_llm=use_llm
        )

        if not recommendations:
            click.echo(
                "No recommendations available. You may need to add more consumed content."
            )
            return

        if output_format == "json":
            # JSON output
            output = []
            for rec in recommendations:
                item = rec["item"]
                output.append(
                    {
                        "title": item.title,
                        "author": item.author,
                        "score": rec["score"],
                        "similarity_score": rec["similarity_score"],
                        "preference_score": rec["preference_score"],
                        "reasoning": rec["reasoning"],
                    }
                )
            click.echo(json.dumps(output, indent=2))
        else:
            # Table output
            table_data = []
            for i, rec in enumerate(recommendations, 1):
                item = rec["item"]
                author = item.author or "N/A"
                table_data.append(
                    [
                        i,
                        item.title,
                        author,
                        f"{rec['score']:.2f}",
                        rec["reasoning"],
                    ]
                )

            headers = ["#", "Title", "Author", "Score", "Reasoning"]
            click.echo(tabulate(table_data, headers=headers, tablefmt="grid"))

    except Exception as e:
        click.echo(f"Error generating recommendations: {e}", err=True)
        raise click.Abort()


@click.command()
@click.option(
    "--source",
    type=click.Choice(["goodreads", "steam", "all"], case_sensitive=False),
    default="all",
    help="Data source to update",
)
@click.pass_context
def update(ctx: click.Context, source: str) -> None:
    """Update data from input files."""
    storage = ctx.obj["storage"]
    embedding_gen = ctx.obj["embedding_gen"]
    config = ctx.obj["config"]

    click.echo(f"Updating data from {source}...")

    try:
        total_count = 0

        if source == "goodreads" or source == "all":
            inputs_config = config.get("inputs", {})
            goodreads_config = inputs_config.get("goodreads", {})

            if not goodreads_config.get("enabled", False):
                click.echo("Goodreads source is disabled in config.")
            else:
                goodreads_path = Path(
                    goodreads_config.get("path", "inputs/goodreads_library_export.csv")
                )

                if not goodreads_path.exists():
                    click.echo(
                        f"Error: Goodreads file not found: {goodreads_path}", err=True
                    )
                else:
                    click.echo(f"Processing {goodreads_path}...")

                    count = 0
                    for item in parse_goodreads_csv(goodreads_path):
                        # Generate embedding
                        try:
                            embedding = embedding_gen.generate_content_embedding(item)
                            storage.save_content_item(item, embedding)
                            count += 1
                            total_count += 1
                            if count % 10 == 0:
                                click.echo(f"  Processed {count} items...")
                        except Exception as e:
                            click.echo(
                                f"  Warning: Failed to process {item.title}: {e}",
                                err=True,
                            )

                    click.echo(f"✅ Updated {count} items from Goodreads")

        if source == "steam" or source == "all":
            inputs_config = config.get("inputs", {})
            steam_config = inputs_config.get("steam", {})

            if not steam_config.get("enabled", False):
                click.echo("Steam source is disabled in config.")
            else:
                api_key = steam_config.get("api_key", "").strip()
                if not api_key:
                    click.echo(
                        "Error: Steam API key is required. Get one from https://steamcommunity.com/dev/apikey",
                        err=True,
                    )
                else:
                    steam_id = steam_config.get("steam_id", "").strip()
                    vanity_url = steam_config.get("vanity_url", "").strip()
                    min_playtime = steam_config.get("min_playtime_minutes", 0)

                    if not steam_id and not vanity_url:
                        click.echo(
                            "Error: Either steam_id or vanity_url must be provided in config",
                            err=True,
                        )
                    else:
                        click.echo("Fetching games from Steam API...")
                        try:
                            count = 0
                            for item in parse_steam_games(
                                api_key=api_key,
                                steam_id=steam_id if steam_id else None,
                                vanity_url=vanity_url if vanity_url else None,
                                min_playtime_minutes=min_playtime,
                            ):
                                # Generate embedding
                                try:
                                    embedding = (
                                        embedding_gen.generate_content_embedding(item)
                                    )
                                    storage.save_content_item(item, embedding)
                                    count += 1
                                    total_count += 1
                                    if count % 10 == 0:
                                        click.echo(f"  Processed {count} games...")
                                except Exception as e:
                                    click.echo(
                                        f"  Warning: Failed to process {item.title}: {e}",
                                        err=True,
                                    )

                            click.echo(f"✅ Updated {count} items from Steam")
                        except SteamAPIError as e:
                            click.echo(f"Error fetching Steam data: {e}", err=True)
                        except Exception as e:
                            click.echo(f"Error processing Steam data: {e}", err=True)

        if total_count == 0:
            click.echo(
                "No items were updated. Check your configuration and source settings."
            )

    except Exception as e:
        click.echo(f"Error updating data: {e}", err=True)
        raise click.Abort()


@click.command()
@click.option(
    "--type",
    "content_type_str",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
    required=True,
    help="Content type",
)
@click.option("--title", required=True, help="Title of the content")
@click.option("--author", help="Author (for books)")
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
    author: Optional[str],
    rating: Optional[int],
    review: Optional[str],
) -> None:
    """Mark content as completed."""
    from src.models.content import ContentItem

    # Map string to ContentType enum
    type_map = {
        "book": ContentType.BOOK,
        "movie": ContentType.MOVIE,
        "tv_show": ContentType.TV_SHOW,
        "video_game": ContentType.VIDEO_GAME,
    }
    content_type = type_map[content_type_str.lower()]

    storage = ctx.obj["storage"]
    embedding_gen = ctx.obj["embedding_gen"]

    # Validate rating
    if rating is not None and (rating < 1 or rating > 5):
        click.echo("Error: Rating must be between 1 and 5", err=True)
        raise click.Abort()

    # Create content item
    item = ContentItem(
        id=None,  # Will be generated
        title=title,
        author=author if content_type == ContentType.BOOK else None,
        content_type=content_type,
        status=ConsumptionStatus.COMPLETED,
        rating=rating,
        review=review,
    )

    try:
        # Generate embedding and save
        embedding = embedding_gen.generate_content_embedding(item)
        db_id = storage.save_content_item(item, embedding)

        click.echo(f"✅ Marked '{title}' as completed (ID: {db_id})")
    except Exception as e:
        click.echo(f"Error marking content as completed: {e}", err=True)
        raise click.Abort()
