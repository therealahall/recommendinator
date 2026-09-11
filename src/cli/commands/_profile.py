from __future__ import annotations

import json

import click

from src.cli._shared import abort_after_failure
from src.recommendations.profile import (
    GenreAffinity,
    ProfilePayload,
    profile_payload,
    regenerated_payload,
)

#: What the web says when ``GET /api/profile`` fails.
PROFILE_LOAD_FAILED = "Failed to load profile"

#: What the web says when ``POST /api/profile/regenerate`` fails. The generator
#: walks the library, so its faults quote item titles.
PROFILE_REGENERATE_FAILED = "Failed to regenerate profile"

ANTI_FLAG = "(not your style)"

#: What an empty profile means depends on which command asked: only one of them
#: leaves ``profile regenerate`` still worth running.
NO_PROFILE_STORED = "No profile generated yet. Run 'profile regenerate' to create one."
NOTHING_TO_PROFILE = "No profile generated: nothing in your library is rated yet."


def _genre_line(entry: GenreAffinity) -> str:
    score = "" if entry["score"] is None else f": {entry['score']:.1f}"
    flag = f"  {ANTI_FLAG}" if entry["anti"] else ""
    return f"  {entry['genre']}{score}{flag}"


def _emit_profile(
    payload: ProfilePayload, output_format: str, empty_message: str
) -> None:
    if output_format == "json":
        click.echo(json.dumps(payload, indent=2))
        return

    if not payload["has_content"]:
        click.echo(empty_message)
        return

    if payload["genre_affinities"]:
        click.echo("Genre and Tag Affinities:")
        for entry in payload["genre_affinities"]:
            click.echo(_genre_line(entry))

    if payload["author_affinities"]:
        click.echo("\nAuthor Affinities:")
        for author in payload["author_affinities"]:
            click.echo(f"  {author['author']}: {author['score']:.1f}")

    if payload["theme_preferences"]:
        click.echo("\nTheme Preferences:")
        for theme in payload["theme_preferences"]:
            click.echo(f"  - {theme}")

    if payload["cross_media_patterns"]:
        click.echo("\nCross-Media Patterns:")
        for pattern in payload["cross_media_patterns"]:
            click.echo(f"  - {pattern}")

    if payload["generated_at"]:
        click.echo(f"\nGenerated: {payload['generated_at']}")


@click.group()
def profile() -> None:
    """View and manage your preference profile."""


@profile.command("show")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def profile_show(ctx: click.Context, output_format: str, user_id: int) -> None:
    """Show your preference profile."""
    storage = ctx.obj["storage"]
    try:
        profile_record = storage.profiles.get(user_id)
    except Exception as error:
        abort_after_failure(ctx, PROFILE_LOAD_FAILED, error)

    _emit_profile(
        profile_payload(user_id, profile_record), output_format, NO_PROFILE_STORED
    )


@profile.command("regenerate")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def profile_regenerate(ctx: click.Context, output_format: str, user_id: int) -> None:
    """Regenerate your preference profile from library data."""
    storage = ctx.obj["storage"]

    click.echo("Analyzing your library...", err=True)
    try:
        payload = regenerated_payload(storage, user_id)
    except Exception as error:
        abort_after_failure(ctx, PROFILE_REGENERATE_FAILED, error)

    _emit_profile(payload, output_format, NOTHING_TO_PROFILE)
