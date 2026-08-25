"""The ``status`` command."""

from __future__ import annotations

import json

import click

from src import __version__ as APP_VERSION
from src.utils.dependencies import dependency_drift


@click.command()
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def status(ctx: click.Context, output_format: str) -> None:
    """Show system health and component readiness.

    Mirrors the web API GET /api/status StatusResponse shape.
    """
    config = ctx.obj["config"]

    # Component readiness (keys match web API)
    components = {
        "engine": ctx.obj.get("engine") is not None,
        "storage": ctx.obj.get("storage") is not None,
    }

    rec_config = config.get("recommendations", {})
    recommendations_config = {
        "max_count": rec_config.get("max_count", 20),
        "default_count": rec_config.get("default_count", 5),
    }

    all_ready = all(components.values())
    status_str = "ready" if all_ready else "initializing"
    drift = dependency_drift()

    if output_format == "json":
        output = {
            "status": status_str,
            "version": APP_VERSION,
            "components": components,
            "recommendations_config": recommendations_config,
            "dependency_drift": [entry.model_dump() for entry in drift],
        }
        click.echo(json.dumps(output, indent=2))
    else:
        click.echo(f"\nRecommendinator v{APP_VERSION} ({status_str})\n")

        click.echo("Components:")
        for name, ready in components.items():
            label = "ready" if ready else "not available"
            click.echo(f"  {name}: {label}")

        if drift:
            click.echo("\nDependency drift:")
            for entry in drift:
                click.echo(f"  {entry.message}")

        click.echo(
            f"\nRecommendations: max={recommendations_config['max_count']}, "
            f"default={recommendations_config['default_count']}"
        )
