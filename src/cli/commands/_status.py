"""The ``status`` command."""

from __future__ import annotations

import importlib.metadata
import json

import click


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
    version = importlib.metadata.version("recommendinator")
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

    if output_format == "json":
        output = {
            "status": status_str,
            "version": version,
            "components": components,
            "recommendations_config": recommendations_config,
        }
        click.echo(json.dumps(output, indent=2))
    else:
        click.echo(f"\nRecommendinator v{version} ({status_str})\n")

        click.echo("Components:")
        for name, ready in components.items():
            label = "ready" if ready else "not available"
            click.echo(f"  {name}: {label}")

        click.echo(
            f"\nRecommendations: max={recommendations_config['max_count']}, "
            f"default={recommendations_config['default_count']}"
        )
