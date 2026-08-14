"""The ``profile`` group."""

from __future__ import annotations

import json

import click

from src.recommendations.profile import ProfileGenerator


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
    profile_record = storage.get_preference_profile(user_id)

    if profile_record is None:
        if output_format == "json":
            # Emit an empty ProfileResponse (matches web GET /api/profile).
            click.echo(
                json.dumps(
                    {
                        "user_id": user_id,
                        "genre_affinities": {},
                        "theme_preferences": [],
                        "anti_preferences": [],
                        "cross_media_patterns": [],
                        "generated_at": None,
                    },
                    indent=2,
                )
            )
        else:
            click.echo(
                "No profile generated yet. Run 'profile regenerate' to create one."
            )
        return

    # StorageManager.get_preference_profile wraps the profile in a record:
    # {"id", "user_id", "profile": {...actual data...}, "generated_at"}.
    # Unwrap to match the web API's ProfileResponse shape.
    profile = profile_record.get("profile", {})
    generated_at = profile_record.get("generated_at")

    if output_format == "json":
        # Explicit field extraction matches web ProfileResponse exactly,
        # immune to any extra keys the stored profile blob may contain.
        output = {
            "user_id": profile_record.get("user_id"),
            "genre_affinities": profile.get("genre_affinities", {}),
            "theme_preferences": profile.get("theme_preferences", []),
            "anti_preferences": profile.get("anti_preferences", []),
            "cross_media_patterns": profile.get("cross_media_patterns", []),
            "generated_at": generated_at,
        }
        click.echo(json.dumps(output, indent=2, default=str))
    else:
        affinities = profile.get("genre_affinities", {})
        if affinities:
            click.echo("Genre Affinities:")
            for genre, score in sorted(
                affinities.items(), key=lambda pair: pair[1], reverse=True
            ):
                click.echo(f"  {genre}: {score:.1f}")

        themes = profile.get("theme_preferences", [])
        if themes:
            click.echo("\nTheme Preferences:")
            for theme in themes:
                click.echo(f"  - {theme}")

        anti_preferences = profile.get("anti_preferences", [])
        if anti_preferences:
            click.echo("\nAnti-Preferences:")
            for preference in anti_preferences:
                click.echo(f"  - {preference}")

        patterns = profile.get("cross_media_patterns", [])
        if patterns:
            click.echo("\nCross-Media Patterns:")
            for pattern in patterns:
                click.echo(f"  - {pattern}")

        if generated_at:
            click.echo(f"\nGenerated: {generated_at}")


@profile.command("regenerate")
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def profile_regenerate(ctx: click.Context, user_id: int) -> None:
    """Regenerate your preference profile from library data."""
    storage = ctx.obj["storage"]

    click.echo("Analyzing your library...", err=True)
    generator = ProfileGenerator(storage)
    profile_result = generator.regenerate_and_save(user_id)
    click.echo(
        f"Profile regenerated with {len(profile_result.genre_affinities)} genre affinities."
    )
