"""The ``status`` command."""

from __future__ import annotations

import importlib.metadata
import json

import click

from src.config.service import get_feature_flags


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
    """Show system health, component readiness, and feature flags.

    Mirrors the web API GET /api/status StatusResponse shape.
    """
    version = importlib.metadata.version("recommendinator")
    config = ctx.obj["config"]
    flags = get_feature_flags(config)
    ai_enabled = flags["ai_enabled"]

    # Component readiness (keys and AI-gating match web API)
    components = {
        "engine": ctx.obj.get("engine") is not None,
        "storage": ctx.obj.get("storage") is not None,
        "embedding_generator": (
            ctx.obj.get("embedding_gen") is not None if ai_enabled else True
        ),
    }

    # Features (key set matches web FeaturesStatus exactly)
    features = {
        "ai_enabled": ai_enabled,
        "embeddings_enabled": flags["embeddings_enabled"],
        "llm_reasoning_enabled": flags["llm_reasoning_enabled"],
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
            "features": features,
            "recommendations_config": recommendations_config,
        }
        click.echo(json.dumps(output, indent=2))
    else:
        click.echo(f"\nRecommendinator v{version} ({status_str})\n")

        click.echo("Components:")
        for name, ready in components.items():
            label = "ready" if ready else "not available"
            click.echo(f"  {name}: {label}")

        click.echo("\nFeatures:")
        for name, enabled in features.items():
            label = "enabled" if enabled else "disabled"
            click.echo(f"  {name}: {label}")

        click.echo(
            f"\nRecommendations: max={recommendations_config['max_count']}, "
            f"default={recommendations_config['default_count']}"
        )
