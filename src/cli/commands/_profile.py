"""The ``profile`` group."""

from __future__ import annotations

import json

import click

from src.cli._shared import abort_after_failure
from src.recommendations.profile import ProfileGenerator, profile_payload

#: What the web says when ``GET /api/profile`` fails.
PROFILE_LOAD_FAILED = "Failed to load profile"

#: What the web says when ``POST /api/profile/regenerate`` fails. The generator
#: walks the library, so its faults quote item titles.
PROFILE_REGENERATE_FAILED = "Failed to regenerate profile"


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

    payload = profile_payload(user_id, profile_record)

    if output_format == "json":
        click.echo(json.dumps(payload, indent=2))
        return

    if profile_record is None:
        click.echo("No profile generated yet. Run 'profile regenerate' to create one.")
        return

    affinities = payload["genre_affinities"]
    if affinities:
        click.echo("Genre Affinities:")
        for genre, score in sorted(
            affinities.items(), key=lambda pair: pair[1], reverse=True
        ):
            click.echo(f"  {genre}: {score:.1f}")

    if payload["theme_preferences"]:
        click.echo("\nTheme Preferences:")
        for theme in payload["theme_preferences"]:
            click.echo(f"  - {theme}")

    if payload["anti_preferences"]:
        click.echo("\nAnti-Preferences:")
        for preference in payload["anti_preferences"]:
            click.echo(f"  - {preference}")

    if payload["cross_media_patterns"]:
        click.echo("\nCross-Media Patterns:")
        for pattern in payload["cross_media_patterns"]:
            click.echo(f"  - {pattern}")

    if payload["generated_at"]:
        click.echo(f"\nGenerated: {payload['generated_at']}")


@profile.command("regenerate")
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def profile_regenerate(ctx: click.Context, user_id: int) -> None:
    """Regenerate your preference profile from library data."""
    storage = ctx.obj["storage"]

    click.echo("Analyzing your library...", err=True)
    generator = ProfileGenerator(storage)
    try:
        profile_result = generator.regenerate_and_save(user_id)
    except Exception as error:
        abort_after_failure(ctx, PROFILE_REGENERATE_FAILED, error)
    click.echo(
        f"Profile regenerated with {len(profile_result.genre_affinities)} genre affinities."
    )
