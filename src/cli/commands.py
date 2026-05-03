"""CLI commands."""

from __future__ import annotations  # noqa: I001

import importlib.metadata
import json
import logging
import os
import sys
import time
import webbrowser
from pathlib import Path
from typing import NoReturn

import click
from tabulate import tabulate

from src.cli.config import get_feature_flags
from src.conversation.engine import ConversationEngine
from src.conversation.profile import ProfileGenerator
from src.enrichment.manager import EnrichmentManager
from src.ingestion.sync import execute_sync
from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    get_enum_value,
)
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.preference_interpreter import (
    LLMPreferenceInterpreter,
    PatternBasedInterpreter,
)
from src.recommendations.scorers import SCORER_NAME_MAP
from src.storage.credential_migration import migrate_config_credentials
from src.storage.manager import StorageManager
from src.utils.item_serialization import item_to_dict
from src.web.epic_auth import (
    exchange_code_for_tokens as exchange_epic_code,
    extract_code_from_input as extract_epic_code,
    get_epic_auth_url,
    has_epic_token,
    is_epic_enabled,
    save_epic_token,
)
from src.web.export import export_items_csv, export_items_json
from src.web.gog_auth import (
    exchange_code_for_tokens as exchange_gog_code,
    extract_code_from_input as extract_gog_code,
    get_gog_auth_url,
    has_gog_token,
    is_gog_enabled,
    save_gog_token,
)
from src.ingestion.plugin_base import ConfigField, SourcePlugin
from src.web.sync_sources import (
    SourceConfigError,
    build_config_view,
    build_schema_view,
    clear_source_secret_value,
    get_available_sync_sources,
    migrate_source,
    resolve_inputs,
    resolve_source_plugin,
    set_source_enabled_state,
    set_source_secret_value,
    update_source_config_values,
    validate_source_config,
)

logger = logging.getLogger(__name__)


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
    "--use-llm/--no-use-llm",
    default=True,
    help="Use LLM for enhanced recommendation reasoning (default: enabled).",
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
    use_llm: bool,
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

    click.echo(f"Generating {count} {content_type_str} recommendations...")

    try:
        # Load user preferences
        user_preference_config = storage.get_user_preference_config(user_id)

        recommendations = engine.generate_recommendations(
            content_type=content_type,
            count=count,
            use_llm=use_llm,
            user_preference_config=user_preference_config,
        )

        if not recommendations:
            if output_format == "json":
                # Emit an empty JSON array (matches web GET /api/recommendations).
                click.echo(json.dumps([]))
            else:
                click.echo(
                    "No recommendations available. Recommendations are based "
                    "on items you haven't consumed yet — try adding new items "
                    "to your wishlist or library."
                )
            return

        if output_format == "json":
            # JSON output matches web API RecommendationResponse shape
            output = []
            for rec in recommendations:
                item = rec["item"]
                output.append(
                    {
                        "db_id": item.db_id,
                        "title": item.title,
                        "author": item.author,
                        "score": rec["score"],
                        "similarity_score": rec["similarity_score"],
                        "preference_score": rec["preference_score"],
                        "reasoning": rec["reasoning"],
                        "llm_reasoning": rec.get("llm_reasoning"),
                        "score_breakdown": rec.get("score_breakdown", {}),
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

    except Exception as error:
        click.echo(f"Error generating recommendations: {error}", err=True)
        raise click.Abort() from error


@click.command()
@click.option(
    "--source",
    default="all",
    help="Data source to update (use 'list' to see available sources, 'all' for everything)",
)
@click.pass_context
def update(ctx: click.Context, source: str) -> None:
    """Update data from configured sources."""
    storage = ctx.obj["storage"]
    embedding_gen = ctx.obj["embedding_gen"]
    config = ctx.obj["config"]

    inputs_config = config.get("inputs", {})

    # Handle 'list' to show available sources (read-only — no migration needed)
    if source == "list":
        if not inputs_config:
            click.echo("No sources configured.")
            return

        click.echo("Available sources:")
        for source_id, entry in inputs_config.items():
            if not isinstance(entry, dict):
                continue
            plugin_name = entry.get("plugin", "?")
            enabled = entry.get("enabled", False)
            status = "enabled" if enabled else "disabled"
            click.echo(f"  {source_id:20s} plugin={plugin_name} [{status}]")
        return

    # Migrate any config-file credentials to encrypted DB storage
    migrate_config_credentials(config, storage)

    # Check if embeddings are enabled
    use_embeddings = get_feature_flags(config)["use_embeddings"]

    # Check if auto-enrichment is enabled
    enrichment_config = config.get("enrichment", {})
    auto_enrich = enrichment_config.get("enabled", False) and enrichment_config.get(
        "auto_enrich_on_sync", False
    )

    # Determine which sources to sync
    if source == "all":
        resolved = resolve_inputs(config, storage=storage)
        if not resolved:
            click.echo(
                "No sources enabled in config. Use --source list to see available sources."
            )
            return
    else:
        # Look up a single source by its user-defined key
        entry = inputs_config.get(source)
        if not isinstance(entry, dict):
            click.echo(
                f"Error: Unknown source '{source}'. "
                "Use --source list to see available sources.",
                err=True,
            )
            raise click.Abort()

        if not entry.get("enabled", False):
            click.echo(f"{source} source is disabled in config.")
            return

        validation_errors = validate_source_config(source, config, storage=storage)
        if validation_errors:
            for error in validation_errors:
                click.echo(f"Error: {error}", err=True)
            raise click.Abort()

        resolved = [
            resolved_entry
            for resolved_entry in resolve_inputs(config, storage=storage)
            if resolved_entry.source_id == source
        ]

    click.echo(
        f"Updating data from {', '.join(entry.source_id for entry in resolved)}..."
    )

    try:
        total_count = 0

        for resolved_entry in resolved:
            validation_errors = validate_source_config(
                resolved_entry.source_id, config, storage=storage
            )
            if validation_errors:
                for error in validation_errors:
                    click.echo(
                        f"  {resolved_entry.plugin.display_name}: Error: {error}",
                        err=True,
                    )
                continue

            click.echo(
                f"  Syncing {resolved_entry.plugin.display_name} ({resolved_entry.source_id})..."
            )

            def cli_progress(
                items_processed: int,
                total_items: int | None,
                current_item: str | None,
                current_source: str | None = None,
            ) -> None:
                if total_items and items_processed > 0 and items_processed % 10 == 0:
                    click.echo(f"    Processed {items_processed}/{total_items}...")

            try:
                result = execute_sync(
                    plugin=resolved_entry.plugin,
                    plugin_config=resolved_entry.config,
                    storage_manager=storage,
                    embedding_generator=embedding_gen,
                    use_embeddings=use_embeddings,
                    progress_callback=cli_progress,
                    mark_for_enrichment=auto_enrich,
                )

                click.echo(
                    f"  Updated {result.items_synced} items from "
                    f"{resolved_entry.plugin.display_name} ({resolved_entry.source_id})"
                )
                if result.errors:
                    for error in result.errors:
                        click.echo(f"    Warning: {error}", err=True)

                total_count += result.items_synced

            except Exception as error:
                click.echo(
                    f"  Error syncing {resolved_entry.plugin.display_name}: {error}",
                    err=True,
                )

        if total_count == 0:
            click.echo(
                "No items were updated. Check your configuration and source settings."
            )
        else:
            click.echo(f"Total: {total_count} items updated.")

    except Exception as error:
        click.echo(f"Error updating data: {error}", err=True)
        raise click.Abort() from error


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
    author: str | None,
    rating: int | None,
    review: str | None,
) -> None:
    """Mark content as completed."""
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
        # Only generate embedding if AI features are enabled
        embedding = None
        if use_embeddings:
            embedding = embedding_gen.generate_content_embedding(item)
        db_id = storage.save_content_item(item, embedding)

        click.echo(f"Marked '{title}' as completed (ID: {db_id})")
    except Exception as error:
        click.echo(f"Error marking content as completed: {error}", err=True)
        raise click.Abort() from error


# ---------------------------------------------------------------------------
# Preferences command group
# ---------------------------------------------------------------------------


@click.group()
def preferences() -> None:
    """Manage user preference settings."""


@preferences.command("get")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def preferences_get(ctx: click.Context, user_id: int, output_format: str) -> None:
    """Show current user preferences."""
    storage = ctx.obj["storage"]
    preference_config = storage.get_user_preference_config(user_id)
    data = preference_config.to_dict()

    if output_format == "json":
        click.echo(json.dumps(data, indent=2))
    else:
        # Table output
        table_data = []
        for key, value in data.items():
            table_data.append([key, str(value)])
        click.echo(tabulate(table_data, headers=["Setting", "Value"], tablefmt="grid"))


@preferences.command("set-weight")
@click.argument("scorer_name")
@click.argument("weight", type=float)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def preferences_set_weight(
    ctx: click.Context, scorer_name: str, weight: float, user_id: int
) -> None:
    """Set a scorer weight for a user.

    SCORER_NAME is the scorer to adjust (e.g. genre_match, creator_match).
    WEIGHT is the new weight value (e.g. 2.5).
    """
    if scorer_name not in SCORER_NAME_MAP:
        valid_names = ", ".join(sorted(SCORER_NAME_MAP.keys()))
        click.echo(
            f"Error: Unknown scorer '{scorer_name}'. " f"Valid scorers: {valid_names}",
            err=True,
        )
        raise click.Abort()

    storage = ctx.obj["storage"]
    preference_config = storage.get_user_preference_config(user_id)
    preference_config.scorer_weights[scorer_name] = weight
    storage.save_user_preference_config(user_id, preference_config)
    click.echo(f"Set {scorer_name} weight to {weight} for user {user_id}")


@preferences.command("reset")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def preferences_reset(ctx: click.Context, user_id: int) -> None:
    """Reset user preferences to defaults."""
    storage = ctx.obj["storage"]
    storage.save_user_preference_config(user_id, UserPreferenceConfig())
    click.echo(f"Reset preferences to defaults for user {user_id}")


@preferences.command("set-length")
@click.argument(
    "content_type",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
)
@click.argument(
    "length_preference",
    type=click.Choice(["any", "short", "medium", "long"], case_sensitive=False),
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def preferences_set_length(
    ctx: click.Context, content_type: str, length_preference: str, user_id: int
) -> None:
    """Set a length preference for a content type.

    CONTENT_TYPE is the type (book, movie, tv_show, video_game).
    LENGTH_PREFERENCE is the preferred length (any, short, medium, long).
    """
    storage = ctx.obj["storage"]
    preference_config = storage.get_user_preference_config(user_id)
    preference_config.content_length_preferences[content_type.lower()] = (
        length_preference.lower()
    )
    storage.save_user_preference_config(user_id, preference_config)
    click.echo(
        f"Set {content_type} length preference to '{length_preference}' for user {user_id}"
    )


# ---------------------------------------------------------------------------
# Custom rules subgroup
# ---------------------------------------------------------------------------


@preferences.group("custom-rules")
def custom_rules() -> None:
    """Manage custom preference rules."""


@custom_rules.command("list")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def custom_rules_list(ctx: click.Context, user_id: int) -> None:
    """List all custom rules for a user."""
    storage = ctx.obj["storage"]
    preference_config = storage.get_user_preference_config(user_id)
    rules = preference_config.custom_rules

    if not rules:
        click.echo(f"No custom rules set for user {user_id}")
        return

    click.echo(f"Custom rules for user {user_id}:")
    for index, rule in enumerate(rules):
        click.echo(f"  {index}: {rule}")


@custom_rules.command("add")
@click.argument("rule_text")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def custom_rules_add(ctx: click.Context, rule_text: str, user_id: int) -> None:
    """Add a custom preference rule.

    RULE_TEXT is the natural language rule (e.g., "avoid horror", "prefer sci-fi").
    """
    storage = ctx.obj["storage"]
    preference_config = storage.get_user_preference_config(user_id)
    preference_config.custom_rules.append(rule_text)
    storage.save_user_preference_config(user_id, preference_config)
    click.echo(f"Added rule: '{rule_text}' for user {user_id}")
    click.echo(f"Total rules: {len(preference_config.custom_rules)}")


@custom_rules.command("remove")
@click.argument("index", type=int)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def custom_rules_remove(ctx: click.Context, index: int, user_id: int) -> None:
    """Remove a custom rule by index.

    INDEX is the rule number (use 'list' to see indices).
    """
    storage = ctx.obj["storage"]
    preference_config = storage.get_user_preference_config(user_id)

    if index < 0 or index >= len(preference_config.custom_rules):
        click.echo(
            f"Error: Invalid index {index}. "
            f"Valid range: 0-{len(preference_config.custom_rules) - 1}",
            err=True,
        )
        raise click.Abort()

    removed = preference_config.custom_rules.pop(index)
    storage.save_user_preference_config(user_id, preference_config)
    click.echo(f"Removed rule: '{removed}'")
    click.echo(f"Remaining rules: {len(preference_config.custom_rules)}")


@custom_rules.command("clear")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.pass_context
def custom_rules_clear(ctx: click.Context, user_id: int, yes: bool) -> None:
    """Clear all custom rules for a user."""
    storage = ctx.obj["storage"]
    preference_config = storage.get_user_preference_config(user_id)

    if not preference_config.custom_rules:
        click.echo(f"No custom rules to clear for user {user_id}")
        return

    if not yes:
        count = len(preference_config.custom_rules)
        if not click.confirm(f"Clear {count} custom rule(s) for user {user_id}?"):
            click.echo("Aborted.")
            return

    cleared_count = len(preference_config.custom_rules)
    preference_config.custom_rules = []
    storage.save_user_preference_config(user_id, preference_config)
    click.echo(f"Cleared {cleared_count} custom rule(s) for user {user_id}")


@custom_rules.command("interpret")
@click.argument("rule_text")
@click.option(
    "--use-llm",
    is_flag=True,
    help="Use LLM for interpretation (requires AI to be enabled)",
)
@click.pass_context
def custom_rules_interpret(ctx: click.Context, rule_text: str, use_llm: bool) -> None:
    """Interpret a custom rule and show the parsed result.

    RULE_TEXT is the natural language rule to interpret.

    This command shows how the system would interpret a rule without saving it.
    """
    if use_llm:
        # Check if LLM is available
        llm_client = ctx.obj.get("llm_client")
        if llm_client is None:
            click.echo(
                "Warning: LLM not available, falling back to pattern-based interpreter",
                err=True,
            )
            use_llm = False

    if use_llm:
        llm_client = ctx.obj["llm_client"]
        storage = ctx.obj["storage"]
        llm_interpreter = LLMPreferenceInterpreter(
            ollama_client=llm_client,
            storage_manager=storage,
        )
        click.echo("Using LLM interpreter...")
        result = llm_interpreter.interpret(rule_text)
    else:
        pattern_interpreter = PatternBasedInterpreter()
        click.echo("Using pattern-based interpreter...")
        result = pattern_interpreter.interpret(rule_text)

    click.echo(f"\nRule: '{rule_text}'")
    click.echo(f"Confidence: {result.confidence.value}")
    click.echo(f"Notes: {result.interpretation_notes}")
    click.echo("")

    if result.genre_boosts:
        click.echo("Genre boosts:")
        for genre, boost in result.genre_boosts.items():
            click.echo(f"  +{boost:.1f} {genre}")

    if result.genre_penalties:
        click.echo("Genre penalties:")
        for genre, penalty in result.genre_penalties.items():
            click.echo(f"  -{penalty:.1f} {genre}")

    if result.content_type_filters:
        click.echo(f"Content type filters: {', '.join(result.content_type_filters)}")

    if result.content_type_exclusions:
        click.echo(
            f"Content type exclusions: {', '.join(result.content_type_exclusions)}"
        )

    if result.length_preferences:
        click.echo("Length preferences:")
        for content_type, length in result.length_preferences.items():
            click.echo(f"  {content_type}: {length}")

    if result.is_empty():
        click.echo("(No preferences extracted from this rule)")


# ---------------------------------------------------------------------------
# Enrichment command group
# ---------------------------------------------------------------------------


@click.group()
def enrichment() -> None:
    """Manage metadata enrichment."""


@enrichment.command("start")
@click.option(
    "--type",
    "content_type_str",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
    default=None,
    help="Content type to enrich (default: all types)",
)
@click.option(
    "--retry-not-found",
    is_flag=True,
    help="Re-process items previously marked as not_found (matches web API).",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID for filtering items",
)
@click.pass_context
def enrichment_start(
    ctx: click.Context,
    content_type_str: str | None,
    retry_not_found: bool,
    user_id: int,
) -> None:
    """Start background metadata enrichment.

    Enriches content items with genres, tags, and descriptions from
    external APIs (TMDB, OpenLibrary, RAWG).
    """
    storage = ctx.obj["storage"]
    config = ctx.obj["config"]

    # Check if enrichment is enabled
    enrichment_config = config.get("enrichment", {})
    if not enrichment_config.get("enabled", False):
        click.echo(
            "Enrichment is disabled in config. "
            "Set enrichment.enabled: true in config.yaml",
            err=True,
        )
        raise click.Abort()

    # Map string to ContentType enum if provided
    content_type = (
        ContentType.from_string(content_type_str) if content_type_str else None
    )

    manager = EnrichmentManager(storage, config)

    if not manager.start_enrichment(
        content_type=content_type,
        user_id=user_id,
        include_not_found=retry_not_found,
    ):
        click.echo("Enrichment job is already running.", err=True)
        raise click.Abort()

    type_desc = content_type_str if content_type_str else "all types"
    click.echo(f"Started enrichment for {type_desc}...")

    # Poll for completion
    try:
        while True:
            status = manager.get_status()
            if not status.running:
                break

            progress = status.progress_percent
            current = status.current_item or "..."
            click.echo(
                f"  Progress: {progress:.1f}% - Processing: {current[:40]}",
                nl=False,
            )
            click.echo("\r", nl=False)
            time.sleep(1)

        # Final status
        click.echo("")
        if status.cancelled:
            click.echo("Enrichment cancelled.")
        else:
            click.echo("Enrichment completed.")

        click.echo(f"  Items processed: {status.items_processed}")
        click.echo(f"  Items enriched: {status.items_enriched}")
        click.echo(f"  Items not found: {status.items_not_found}")
        click.echo(f"  Items failed: {status.items_failed}")
        click.echo(f"  Elapsed time: {status.elapsed_seconds:.1f}s")

        if status.errors:
            click.echo("  Errors:")
            for error in status.errors[:5]:
                click.echo(f"    - {error}")
            if len(status.errors) > 5:
                click.echo(f"    ... and {len(status.errors) - 5} more")

    except KeyboardInterrupt:
        click.echo("\nStopping enrichment...")
        manager.stop_enrichment()
        click.echo("Enrichment stopped.")


@enrichment.command("status")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID for filtering stats",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def enrichment_status(ctx: click.Context, user_id: int, output_format: str) -> None:
    """Show enrichment statistics."""
    config = ctx.obj["config"]
    storage = ctx.obj["storage"]

    raw_stats = storage.get_enrichment_stats(user_id=user_id)
    enrichment_enabled = config.get("enrichment", {}).get("enabled", False)
    # Shape matches web API EnrichmentStatsResponse
    stats = {"enabled": enrichment_enabled, **raw_stats}

    if output_format == "json":
        click.echo(json.dumps(stats, indent=2))
    else:
        enabled_label = "enabled" if stats["enabled"] else "disabled"
        click.echo(f"Enrichment Statistics ({enabled_label}):")
        click.echo(f"  Total items: {stats['total']}")
        click.echo(f"  Enriched: {stats['enriched']}")
        click.echo(f"  Pending: {stats['pending']}")
        click.echo(f"  Not found: {stats['not_found']}")
        click.echo(f"  Failed: {stats['failed']}")

        if stats["by_provider"]:
            click.echo("\nBy Provider:")
            for provider, count in stats["by_provider"].items():
                click.echo(f"  {provider}: {count}")

        if stats["by_quality"]:
            click.echo("\nBy Match Quality:")
            for quality, count in stats["by_quality"].items():
                click.echo(f"  {quality}: {count}")


@enrichment.command("reset")
@click.option(
    "--provider",
    type=click.Choice(["tmdb", "openlibrary", "rawg", "all"], case_sensitive=False),
    default="all",
    help="Reset items enriched by specific provider (default: all)",
)
@click.option(
    "--type",
    "content_type_str",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
    default=None,
    help="Reset only items of this content type",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID for filtering items",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.pass_context
def enrichment_reset(
    ctx: click.Context,
    provider: str,
    content_type_str: str | None,
    user_id: int,
    yes: bool,
) -> None:
    """Reset enrichment status to re-queue items for enrichment.

    This marks items as needing enrichment again, allowing them to be
    re-processed by the enrichment providers.
    """
    storage = ctx.obj["storage"]

    # Map string to ContentType enum if provided
    content_type = (
        ContentType.from_string(content_type_str) if content_type_str else None
    )

    provider_filter = None if provider == "all" else provider

    # Confirm action
    desc_parts = []
    if provider_filter:
        desc_parts.append(f"provider={provider_filter}")
    if content_type_str:
        desc_parts.append(f"type={content_type_str}")
    desc = f" ({', '.join(desc_parts)})" if desc_parts else ""

    if not yes:
        if not click.confirm(f"Reset enrichment status for items{desc}?"):
            click.echo("Aborted.")
            return

    count = storage.reset_enrichment_status(
        provider=provider_filter,
        content_type=content_type,
        user_id=user_id,
    )

    click.echo(f"Reset enrichment status for {count} item(s).")


# ---------------------------------------------------------------------------
# Library command group
# ---------------------------------------------------------------------------


@click.group()
def library() -> None:
    """Manage your content library."""


@library.command("list")
@click.option(
    "--type",
    "content_type_str",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
    default=None,
    help="Filter by content type",
)
@click.option(
    "--status",
    "status_str",
    type=click.Choice(
        ["unread", "currently_consuming", "completed"],
        case_sensitive=False,
    ),
    default=None,
    help="Filter by consumption status",
)
@click.option(
    "--sort",
    "sort_by",
    type=click.Choice(
        ["title", "updated_at", "rating", "created_at"], case_sensitive=False
    ),
    default="title",
    help="Sort order (default: title)",
)
@click.option(
    "--show-ignored",
    is_flag=True,
    help="Include ignored items",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1, max=200),
    default=50,
    help="Max items to return (1-200, default 50, matches web API)",
)
@click.option(
    "--offset",
    type=click.IntRange(min=0),
    default=0,
    help="Items to skip (for pagination)",
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
    help="User ID",
)
@click.pass_context
def library_list(
    ctx: click.Context,
    content_type_str: str | None,
    status_str: str | None,
    sort_by: str,
    show_ignored: bool,
    limit: int | None,
    offset: int,
    output_format: str,
    user_id: int,
) -> None:
    """List library items with filters."""
    storage = ctx.obj["storage"]

    content_type = (
        ContentType.from_string(content_type_str) if content_type_str else None
    )
    consumption_status = ConsumptionStatus(status_str) if status_str else None

    items: list[ContentItem] = storage.get_content_items(
        user_id=user_id,
        content_type=content_type,
        status=consumption_status,
        sort_by=sort_by,
        include_ignored=show_ignored,
        limit=limit,
        offset=offset,
    )

    if output_format == "json":
        # Always emit a JSON array, even when empty (matches web GET /api/items).
        output = [item_to_dict(item) for item in items]
        click.echo(json.dumps(output, indent=2))
        return

    if not items:
        click.echo("No items found.")
        return

    table_data = []
    for item in items:
        table_data.append(
            [
                item.db_id,
                item.title,
                item.author or "N/A",
                get_enum_value(item.content_type),
                get_enum_value(item.status),
                "N/A" if item.rating is None else item.rating,
            ]
        )
    headers = ["ID", "Title", "Author", "Type", "Status", "Rating"]
    click.echo(tabulate(table_data, headers=headers, tablefmt="grid"))


@library.command("show")
@click.option("--id", "item_id", type=int, required=True, help="Item database ID")
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
    help="User ID",
)
@click.pass_context
def library_show(
    ctx: click.Context, item_id: int, output_format: str, user_id: int
) -> None:
    """Show details of a single library item."""
    storage = ctx.obj["storage"]

    item = storage.get_content_item(item_id, user_id=user_id)
    if item is None:
        click.echo(f"Error: Item {item_id} not found.", err=True)
        raise click.Abort()

    if output_format == "json":
        click.echo(json.dumps(item_to_dict(item), indent=2))
    else:
        table_data = [
            ["Title", item.title],
            ["Author", item.author or "N/A"],
            ["Type", get_enum_value(item.content_type)],
            ["Status", get_enum_value(item.status)],
            ["Rating", "N/A" if item.rating is None else item.rating],
            ["Review", item.review or "N/A"],
            [
                "Date Completed",
                item.date_completed.isoformat() if item.date_completed else "N/A",
            ],
            ["Ignored", "Yes" if item.ignored else "No"],
        ]
        click.echo(tabulate(table_data, tablefmt="grid"))


@library.command("edit")
@click.option("--id", "item_id", type=int, required=True, help="Item database ID")
@click.option(
    "--status",
    "status_str",
    type=click.Choice(
        ["unread", "currently_consuming", "completed"],
        case_sensitive=False,
    ),
    default=None,
    help="New status",
)
@click.option(
    "--rating",
    type=click.IntRange(min=1, max=5),
    default=None,
    help="New rating (1-5)",
)
@click.option("--review", default=None, help="New review text")
@click.option(
    "--seasons-watched",
    default=None,
    help="Comma-separated list of watched season numbers",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_edit(
    ctx: click.Context,
    item_id: int,
    status_str: str | None,
    rating: int | None,
    review: str | None,
    seasons_watched: str | None,
    user_id: int,
) -> None:
    """Edit an item's status, rating, or review."""
    if (
        status_str is None
        and rating is None
        and review is None
        and seasons_watched is None
    ):
        click.echo(
            "Error: Provide at least one of --status, --rating, --review, "
            "--seasons-watched.",
            err=True,
        )
        raise click.Abort()

    storage = ctx.obj["storage"]

    # Look up the item to get current status if not provided
    item = storage.get_content_item(item_id, user_id=user_id)
    if item is None:
        click.echo(f"Error: Item {item_id} not found.", err=True)
        raise click.Abort()

    effective_status = status_str if status_str else get_enum_value(item.status)

    parsed_seasons: list[int] | None = None
    if seasons_watched is not None:
        try:
            parsed_seasons = [int(s.strip()) for s in seasons_watched.split(",")]
        except ValueError:
            click.echo(
                "Error: --seasons-watched must be comma-separated integers (e.g. 1,2,3).",
                err=True,
            )
            raise click.Abort() from None

    updated = storage.update_item_from_ui(
        db_id=item_id,
        status=effective_status,
        rating=rating,
        review=review,
        seasons_watched=parsed_seasons,
        user_id=user_id,
    )

    if updated:
        click.echo(f"Updated item {item_id} ({item.title}).")
    else:
        click.echo(f"Error: Failed to update item {item_id}.", err=True)
        raise click.Abort()


@library.command("ignore")
@click.option("--id", "item_id", type=int, required=True, help="Item database ID")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_ignore(ctx: click.Context, item_id: int, user_id: int) -> None:
    """Ignore an item (exclude from recommendations)."""
    storage = ctx.obj["storage"]

    if storage.set_item_ignored(db_id=item_id, ignored=True, user_id=user_id):
        click.echo(f"Ignored item {item_id}.")
    else:
        click.echo(f"Error: Item {item_id} not found.", err=True)
        raise click.Abort()


@library.command("unignore")
@click.option("--id", "item_id", type=int, required=True, help="Item database ID")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_unignore(ctx: click.Context, item_id: int, user_id: int) -> None:
    """Unignore an item (include in recommendations again)."""
    storage = ctx.obj["storage"]

    if storage.set_item_ignored(db_id=item_id, ignored=False, user_id=user_id):
        click.echo(f"Unignored item {item_id}.")
    else:
        click.echo(f"Error: Item {item_id} not found.", err=True)
        raise click.Abort()


@library.command("export")
@click.option(
    "--type",
    "content_type_str",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
    required=True,
    help="Content type to export",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["csv", "json"], case_sensitive=False),
    default="csv",
    help="Export format (default: csv)",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),  # type: ignore[type-var]
    default=None,
    help="Output file path (default: stdout)",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def library_export(
    ctx: click.Context,
    content_type_str: str,
    output_format: str,
    output_path: Path | None,
    user_id: int,
) -> None:
    """Export library items as CSV or JSON."""
    storage = ctx.obj["storage"]
    content_type = ContentType.from_string(content_type_str)

    items: list[ContentItem] = storage.get_content_items(
        user_id=user_id,
        content_type=content_type,
        include_ignored=True,
    )

    if output_format == "json":
        data = export_items_json(items, content_type)
    else:
        data = export_items_csv(items, content_type)

    if output_path:
        output_path.write_text(data, encoding="utf-8")
        click.echo(f"Exported {len(items)} items to {output_path}")
    else:
        click.echo(data, nl=False)


# ---------------------------------------------------------------------------
# Auth command group
# ---------------------------------------------------------------------------

# Maps CLI --source name to the internal storage source_id. The CLI accepts
# "epic" for brevity but credentials are stored under "epic_games" to match
# the plugin source identifier used across ingestion and storage.
_SOURCE_ID_MAP = {"gog": "gog", "epic": "epic_games"}


@click.group()
def auth() -> None:
    """Manage authentication for data sources."""


@auth.command("status")
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def auth_status(ctx: click.Context, user_id: int) -> None:
    """Show authentication status for OAuth sources."""
    config = ctx.obj["config"]
    storage = ctx.obj["storage"]

    found = False
    if is_gog_enabled(config):
        found = True
        connected = has_gog_token(config, storage=storage, user_id=user_id)
        click.echo(f"  gog: {'connected' if connected else 'not connected'}")
    if is_epic_enabled(config):
        found = True
        connected = has_epic_token(config, storage=storage, user_id=user_id)
        click.echo(f"  epic: {'connected' if connected else 'not connected'}")

    if not found:
        click.echo("No OAuth sources are enabled in config.")


@auth.command("connect")
@click.option(
    "--source",
    type=click.Choice(["gog", "epic"], case_sensitive=False),
    required=True,
    help="Source to authenticate",
)
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically")
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def auth_connect(
    ctx: click.Context, source: str, no_browser: bool, user_id: int
) -> None:
    """Connect an OAuth source by authenticating in browser."""
    config = ctx.obj["config"]
    storage = ctx.obj["storage"]

    if source == "gog":
        is_enabled_fn, get_auth_url_fn = is_gog_enabled, get_gog_auth_url
        extract_code_fn = extract_gog_code
        exchange_fn, save_fn = exchange_gog_code, save_gog_token
    else:
        is_enabled_fn, get_auth_url_fn = is_epic_enabled, get_epic_auth_url
        extract_code_fn = extract_epic_code
        exchange_fn, save_fn = exchange_epic_code, save_epic_token

    if not is_enabled_fn(config):
        click.echo(f"Error: {source} is not enabled in config.", err=True)
        raise click.Abort()

    auth_url = get_auth_url_fn()
    click.echo(f"\nAuthorize {source} at:\n  {auth_url}\n")

    if not no_browser:
        try:
            webbrowser.open(auth_url)
            click.echo("(Browser opened automatically)")
        except Exception:
            logger.debug("Failed to open browser", exc_info=True)
            click.echo("(Could not open browser — copy the URL above)")

    code = click.prompt("Paste the authorization code or redirect URL")

    try:
        extracted_code = extract_code_fn(code.strip())
        tokens = exchange_fn(extracted_code)
        refresh_token = tokens.get("refresh_token")
        if not (refresh_token and refresh_token.strip()):
            click.echo("Error: No refresh token received.", err=True)
            raise click.Abort()
        save_fn(storage, refresh_token.strip(), user_id=user_id)
        click.echo(f"\n{source} connected successfully.")
    except click.Abort:
        raise
    except Exception:
        logger.error("Failed to connect %s", source, exc_info=True)
        click.echo(
            f"Error: Failed to connect {source}. Check logs for details.", err=True
        )
        raise click.Abort() from None


@auth.command("disconnect")
@click.option(
    "--source",
    type=click.Choice(["gog", "epic"], case_sensitive=False),
    required=True,
    help="Source to disconnect",
)
@click.option("--yes", is_flag=True, help="Skip confirmation")
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def auth_disconnect(ctx: click.Context, source: str, yes: bool, user_id: int) -> None:
    """Disconnect an OAuth source by removing stored credentials."""
    storage = ctx.obj["storage"]

    if not yes:
        if not click.confirm(f"Disconnect {source} for user {user_id}?"):
            click.echo("Aborted.")
            return

    source_id = _SOURCE_ID_MAP[source]

    deleted = storage.delete_credential(user_id, source_id, "refresh_token")
    if deleted:
        click.echo(f"{source} disconnected.")
    else:
        # Mirror DELETE /api/{source}/token which returns 404 when missing.
        click.echo(f"No active {source} connection found.", err=True)
        raise click.Abort()


# ---------------------------------------------------------------------------
# Memory command group
# ---------------------------------------------------------------------------


@click.group()
def memory() -> None:
    """Manage core memories for personalization."""


@memory.command("list")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format",
)
@click.option(
    "--include-inactive",
    is_flag=True,
    help="Include inactive memories (default shows active only, matches web API)",
)
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def memory_list(
    ctx: click.Context, output_format: str, include_inactive: bool, user_id: int
) -> None:
    """List core memories."""
    storage = ctx.obj["storage"]
    memories = storage.get_core_memories(user_id, active_only=not include_inactive)

    if output_format == "json":
        # JSON output matches web API MemoryResponse shape
        output = [
            {
                "id": mem["id"],
                "memory_text": mem["memory_text"],
                "memory_type": mem["memory_type"],
                "confidence": mem["confidence"],
                "is_active": mem["is_active"],
                "source": mem["source"],
                "created_at": mem["created_at"],
            }
            for mem in memories
        ]
        click.echo(json.dumps(output, indent=2, default=str))
    else:
        if not memories:
            click.echo("No memories found.")
            return
        table_data = []
        for mem in memories:
            text = mem["memory_text"]
            table_data.append(
                [
                    mem["id"],
                    text[:60] + ("..." if len(text) > 60 else ""),
                    mem["memory_type"],
                    "active" if mem["is_active"] else "inactive",
                ]
            )
        headers = ["ID", "Text", "Type", "Status"]
        click.echo(tabulate(table_data, headers=headers, tablefmt="grid"))


@memory.command("add")
@click.option("--text", required=True, help="Memory text")
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def memory_add(ctx: click.Context, text: str, user_id: int) -> None:
    """Add a new core memory."""
    storage = ctx.obj["storage"]
    memory_id = storage.save_core_memory(
        user_id=user_id,
        memory_text=text,
        memory_type="user_stated",
        source="manual",
        confidence=1.0,
    )
    click.echo(f"Memory {memory_id} created.")


@memory.command("edit")
@click.option("--id", "memory_id", type=int, required=True, help="Memory ID")
@click.option("--text", default=None, help="New memory text")
@click.option(
    "--active/--inactive",
    "is_active",
    default=None,
    help="Set active status (matches web API PUT /api/memories/{id})",
)
@click.pass_context
def memory_edit(
    ctx: click.Context, memory_id: int, text: str | None, is_active: bool | None
) -> None:
    """Edit a core memory's text and/or active status."""
    if text is None and is_active is None:
        click.echo("Error: specify --text and/or --active/--inactive.", err=True)
        raise click.Abort()
    storage = ctx.obj["storage"]
    updated = storage.update_core_memory(
        memory_id=memory_id, memory_text=text, is_active=is_active
    )
    if updated:
        click.echo(f"Memory {memory_id} updated.")
    else:
        click.echo(f"Error: Memory {memory_id} not found.", err=True)
        raise click.Abort()


@memory.command("toggle")
@click.option("--id", "memory_id", type=int, required=True, help="Memory ID")
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def memory_toggle(ctx: click.Context, memory_id: int, user_id: int) -> None:
    """Toggle a memory between active and inactive."""
    storage = ctx.obj["storage"]
    all_memories = storage.get_core_memories(user_id, active_only=False)
    target = next((m for m in all_memories if m["id"] == memory_id), None)
    if target is None:
        click.echo(f"Error: Memory {memory_id} not found.", err=True)
        raise click.Abort()

    new_active = not target["is_active"]
    storage.update_core_memory(memory_id=memory_id, is_active=new_active)
    state = "active" if new_active else "inactive"
    click.echo(f"Memory {memory_id} is now {state}.")


@memory.command("delete")
@click.option("--id", "memory_id", type=int, required=True, help="Memory ID")
@click.option("--yes", is_flag=True, help="Skip confirmation")
@click.pass_context
def memory_delete(ctx: click.Context, memory_id: int, yes: bool) -> None:
    """Delete a core memory."""
    if not yes:
        if not click.confirm(f"Delete memory {memory_id}?"):
            click.echo("Aborted.")
            return

    storage = ctx.obj["storage"]
    deleted = storage.delete_core_memory(memory_id=memory_id)
    if deleted:
        click.echo(f"Memory {memory_id} deleted.")
    else:
        click.echo(f"Error: Memory {memory_id} not found.", err=True)
        raise click.Abort()


# ---------------------------------------------------------------------------
# Profile command group
# ---------------------------------------------------------------------------


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

    click.echo("Analyzing your library...")
    generator = ProfileGenerator(storage)
    profile_result = generator.regenerate_and_save(user_id)
    click.echo(
        f"Profile regenerated with {len(profile_result.genre_affinities)} genre affinities."
    )


# ---------------------------------------------------------------------------
# Chat command group
# ---------------------------------------------------------------------------


@click.group()
def chat() -> None:
    """Chat with the recommendation AI."""


def _require_ai(ctx: click.Context) -> None:
    """Check that AI features are enabled."""
    if not get_feature_flags(ctx.obj["config"])["ai_enabled"]:
        click.echo(
            "Error: AI features are not enabled. "
            "Set features.ai_enabled: true in config.",
            err=True,
        )
        raise click.Abort()


def _create_conversation_engine(ctx: click.Context) -> ConversationEngine:
    """Create a ConversationEngine from CLI context."""
    storage = ctx.obj["storage"]
    ollama_client = ctx.obj["llm_client"]
    engine = ctx.obj["engine"]
    return ConversationEngine(
        storage_manager=storage,
        ollama_client=ollama_client,
        recommendation_engine=engine,
    )


@chat.command("start")
@click.option(
    "--type",
    "content_type_str",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
    default=None,
    help="Filter suggestions to a content type",
)
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def chat_start(ctx: click.Context, content_type_str: str | None, user_id: int) -> None:
    """Start an interactive chat session."""
    _require_ai(ctx)
    conv_engine = _create_conversation_engine(ctx)
    content_type = (
        ContentType.from_string(content_type_str) if content_type_str else None
    )

    click.echo("Chat session started. Type your message, or Ctrl+D to exit.\n")

    while True:
        try:
            click.echo("You> ", nl=False)
            message = input()
        except (EOFError, KeyboardInterrupt):
            click.echo("\nChat session ended.")
            break

        if not message.strip():
            continue

        try:
            response = conv_engine.process_message_sync(
                user_id=user_id, message=message, content_type=content_type
            )
            click.echo(f"\nAssistant: {response}\n")
        except Exception:
            logger.error("Chat message processing failed", exc_info=True)
            click.echo(
                "\nError: Could not process message. Please try again.\n",
                err=True,
            )


@chat.command("send")
@click.option("--message", required=True, help="Message to send")
@click.option(
    "--type",
    "content_type_str",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
    default=None,
    help="Filter suggestions to a content type",
)
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def chat_send(
    ctx: click.Context, message: str, content_type_str: str | None, user_id: int
) -> None:
    """Send a single message and get a response."""
    _require_ai(ctx)
    conv_engine = _create_conversation_engine(ctx)
    content_type = (
        ContentType.from_string(content_type_str) if content_type_str else None
    )

    try:
        response = conv_engine.process_message_sync(
            user_id=user_id, message=message, content_type=content_type
        )
        click.echo(response)
    except Exception:
        logger.error("Chat send failed", exc_info=True)
        click.echo("Error: Failed to get a response. Check logs for details.", err=True)
        raise click.Abort() from None


@chat.command("history")
@click.option(
    "--limit",
    type=click.IntRange(min=1, max=200),
    default=50,
    help="Number of messages to show (1-200, matches web API)",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format",
)
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def chat_history(
    ctx: click.Context, limit: int, output_format: str, user_id: int
) -> None:
    """Show recent conversation history."""
    storage = ctx.obj["storage"]
    messages = storage.get_conversation_history(user_id, limit=limit)

    if output_format == "json":
        # JSON output matches web API MessageResponse shape
        output = [
            {
                "id": msg["id"],
                "role": msg["role"],
                "content": msg["content"],
                "tool_calls": msg.get("tool_calls"),
                "created_at": msg["created_at"],
            }
            for msg in messages
        ]
        click.echo(json.dumps(output, indent=2, default=str))
    else:
        if not messages:
            click.echo("No conversation history.")
            return
        for msg in messages:
            role = msg["role"].capitalize()
            content = msg["content"]
            click.echo(f"{role}: {content}\n")


@chat.command("reset")
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def chat_reset(ctx: click.Context, user_id: int) -> None:
    """Clear conversation history (preserves memories)."""
    storage = ctx.obj["storage"]
    count = storage.clear_conversation_history(user_id)
    click.echo(f"Cleared {count} message(s). Core memories preserved.")


# ``recommendinator source`` group: per-source configuration management.
# Mirrors the web `/api/sync/sources/{id}/...` endpoints, sharing the same
# business logic from ``src.web.sync_sources`` so CLI and web stay in lockstep.


_SOURCE_DEFAULT_USER_ID = 1
_SECRET_VALUE_ENV = "RECOMMENDINATOR_SECRET_VALUE"


def _abort_with(message: str) -> NoReturn:
    click.echo(f"Error: {message}", err=True)
    raise click.Abort()


def _resolve_cli_plugin(ctx: click.Context, source_id: str) -> SourcePlugin:
    plugin = resolve_source_plugin(
        source_id,
        ctx.obj.get("config"),
        ctx.obj.get("storage"),
        user_id=_SOURCE_DEFAULT_USER_ID,
    )
    if plugin is None:
        _abort_with(f"Unknown source: {source_id}")
    return plugin


def _require_storage(ctx: click.Context) -> StorageManager:
    storage = ctx.obj.get("storage")
    if storage is None:
        _abort_with("Storage unavailable")
    if not isinstance(storage, StorageManager):  # pragma: no cover - defensive
        _abort_with("Storage is not a StorageManager instance")
    return storage


def _coerce_set_value(field: ConfigField, raw: str) -> object:
    if field.field_type is bool:
        lowered = raw.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        _abort_with(f"Field '{field.name}' is bool — pass true/false")
    if field.field_type is int:
        try:
            return int(raw)
        except ValueError:
            _abort_with(f"Field '{field.name}' must be an integer")
    if field.field_type is float:
        try:
            return float(raw)
        except ValueError:
            _abort_with(f"Field '{field.name}' must be a number")
    if field.field_type is list:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return raw


def _emit_config_view(
    ctx: click.Context,
    source_id: str,
    plugin: SourcePlugin,
    output_format: str,
    success_message: str,
) -> None:
    """Render the post-update SourceConfigResponse-shaped view for a source."""
    storage = _require_storage(ctx)
    view = build_config_view(
        source_id,
        plugin,
        ctx.obj.get("config"),
        storage,
        user_id=_SOURCE_DEFAULT_USER_ID,
    )
    if output_format == "json":
        click.echo(json.dumps(view, indent=2))
    else:
        click.echo(success_message)


@click.group()
def source() -> None:
    """Manage data source configuration."""


@source.command("list")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_list(ctx: click.Context, output_format: str) -> None:
    """List configured data sources (mirrors GET /api/sync/sources)."""
    config = ctx.obj.get("config") or {}
    storage = ctx.obj.get("storage")
    sources = get_available_sync_sources(
        config, storage=storage, user_id=_SOURCE_DEFAULT_USER_ID
    )
    payload = [
        {
            "id": entry.id,
            "display_name": entry.display_name,
            "plugin_display_name": entry.plugin_display_name,
        }
        for entry in sources
    ]

    if output_format == "json":
        click.echo(json.dumps(payload, indent=2))
        return

    if not payload:
        click.echo("No sync sources configured.")
        return

    rows = [
        [item["id"], item["display_name"], item["plugin_display_name"]]
        for item in payload
    ]
    click.echo(
        tabulate(rows, headers=["ID", "Display Name", "Plugin"], tablefmt="grid")
    )


@source.command("show")
@click.argument("source_id")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_show(ctx: click.Context, source_id: str, output_format: str) -> None:
    """Show current values for a source (mirrors GET /api/sync/sources/<id>/config)."""
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = ctx.obj.get("storage")
    view = build_config_view(
        source_id,
        plugin,
        ctx.obj.get("config"),
        storage,
        user_id=_SOURCE_DEFAULT_USER_ID,
    )

    if output_format == "json":
        click.echo(json.dumps(view, indent=2))
        return

    rows: list[list[str]] = [
        ["plugin", view["plugin"]],
        ["enabled", str(view["enabled"])],
        ["migrated", str(view["migrated"])],
        ["migrated_at", str(view["migrated_at"] or "—")],
    ]
    for name, value in view["field_values"].items():
        rows.append([name, json.dumps(value)])
    for name, is_set in view["secret_status"].items():
        rows.append([f"{name} (secret)", "set" if is_set else "unset"])
    click.echo(tabulate(rows, headers=["Field", "Value"], tablefmt="grid"))


@source.command("schema")
@click.argument("source_id")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_schema(ctx: click.Context, source_id: str, output_format: str) -> None:
    """Show the plugin schema for a source (mirrors GET /api/sync/sources/<id>/schema)."""
    plugin = _resolve_cli_plugin(ctx, source_id)
    view = build_schema_view(source_id, plugin)

    if output_format == "json":
        click.echo(json.dumps(view, indent=2))
        return

    rows = [
        [
            field["name"],
            field["field_type"],
            "yes" if field["required"] else "no",
            "yes" if field["sensitive"] else "no",
            field["description"],
        ]
        for field in view["fields"]
    ]
    click.echo(
        tabulate(
            rows,
            headers=["Field", "Type", "Required", "Sensitive", "Description"],
            tablefmt="grid",
        )
    )


@source.command("migrate")
@click.argument("source_id")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_migrate(ctx: click.Context, source_id: str, output_format: str) -> None:
    """Migrate a YAML source entry into the database (idempotent)."""
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = _require_storage(ctx)
    try:
        result = migrate_source(
            source_id,
            plugin,
            ctx.obj.get("config"),
            storage,
            user_id=_SOURCE_DEFAULT_USER_ID,
        )
    except SourceConfigError as error:
        _abort_with(error.message)

    if output_format == "json":
        click.echo(json.dumps(result, indent=2))
        return

    click.echo(f"Migrated source '{source_id}' to the database.")
    if result["fields_migrated"]:
        click.echo(f"  Fields: {', '.join(result['fields_migrated'])}")
    if result["secrets_migrated"]:
        click.echo(f"  Secrets: {', '.join(result['secrets_migrated'])}")


@source.command("enable")
@click.argument("source_id")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_enable(ctx: click.Context, source_id: str, output_format: str) -> None:
    """Enable a migrated source (mirrors PUT /api/sync/sources/<id>/enabled)."""
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = _require_storage(ctx)
    try:
        set_source_enabled_state(
            source_id, storage, True, user_id=_SOURCE_DEFAULT_USER_ID
        )
    except SourceConfigError as error:
        _abort_with(error.message)
    _emit_config_view(
        ctx, source_id, plugin, output_format, f"Enabled source '{source_id}'."
    )


@source.command("disable")
@click.argument("source_id")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_disable(ctx: click.Context, source_id: str, output_format: str) -> None:
    """Disable a migrated source (mirrors PUT /api/sync/sources/<id>/enabled)."""
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = _require_storage(ctx)
    try:
        set_source_enabled_state(
            source_id, storage, False, user_id=_SOURCE_DEFAULT_USER_ID
        )
    except SourceConfigError as error:
        _abort_with(error.message)
    _emit_config_view(
        ctx, source_id, plugin, output_format, f"Disabled source '{source_id}'."
    )


@source.command("set")
@click.argument("source_id")
@click.argument("field_name")
@click.argument("value")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_set(
    ctx: click.Context,
    source_id: str,
    field_name: str,
    value: str,
    output_format: str,
) -> None:
    """Set a non-sensitive config field for a migrated source."""
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = _require_storage(ctx)

    schema = {f.name: f for f in plugin.get_config_schema()}
    field = schema.get(field_name)
    if field is None:
        _abort_with(f"Unknown field: {field_name}")
    if field.sensitive:
        _abort_with(f"Field '{field_name}' is sensitive — use 'source set-secret'")

    coerced = _coerce_set_value(field, value)
    try:
        update_source_config_values(
            source_id,
            plugin,
            storage,
            {field_name: coerced},
            user_id=_SOURCE_DEFAULT_USER_ID,
        )
    except SourceConfigError as error:
        _abort_with(error.message)
    _emit_config_view(
        ctx,
        source_id,
        plugin,
        output_format,
        f"Set {source_id}.{field_name} = {coerced!r}",
    )


@source.command("apply")
@click.argument("source_id")
@click.option(
    "--from-json",
    "from_json",
    required=True,
    help=(
        "Path to a JSON file containing a values dict, or '-' to read from "
        "stdin. Mirrors PUT /api/sync/sources/<id>/config — applies all "
        "fields atomically."
    ),
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_apply(
    ctx: click.Context, source_id: str, from_json: str, output_format: str
) -> None:
    """Apply a JSON dict of non-sensitive fields atomically (bulk update).

    The web ``PUT /api/sync/sources/<id>/config`` endpoint accepts an
    arbitrary ``values`` dict and updates every key in a single
    transaction. This command mirrors that path so scripts can perform
    multi-field updates without N round-trip CLI invocations.
    """
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = _require_storage(ctx)

    raw = (
        sys.stdin.read()
        if from_json == "-"
        else Path(from_json).read_text(encoding="utf-8")
    )
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as error:
        _abort_with(f"Invalid JSON: {error}")
    if not isinstance(values, dict):
        _abort_with("JSON payload must be an object mapping field names to values")

    try:
        update_source_config_values(
            source_id, plugin, storage, values, user_id=_SOURCE_DEFAULT_USER_ID
        )
    except SourceConfigError as error:
        _abort_with(error.message)

    _emit_config_view(
        ctx,
        source_id,
        plugin,
        output_format,
        f"Applied {len(values)} field(s) to {source_id}.",
    )


@source.command("set-secret")
@click.argument("source_id")
@click.argument("field_name")
@click.pass_context
def source_set_secret(ctx: click.Context, source_id: str, field_name: str) -> None:
    """Store a sensitive field's value.

    Reads from the ``RECOMMENDINATOR_SECRET_VALUE`` environment variable for
    non-interactive use (env vars are not exposed in shell history or in the
    process list to other users); otherwise prompts with hidden input.
    """
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = _require_storage(ctx)

    value = os.environ.get(_SECRET_VALUE_ENV)
    if value is None:
        value = click.prompt(
            f"New value for {source_id}.{field_name}",
            hide_input=True,
            confirmation_prompt=False,
        )

    try:
        set_source_secret_value(
            source_id,
            plugin,
            storage,
            field_name,
            value,
            user_id=_SOURCE_DEFAULT_USER_ID,
        )
    except SourceConfigError as error:
        _abort_with(error.message)
    click.echo(f"Stored secret for {source_id}.{field_name}.")


@source.command("clear-secret")
@click.argument("source_id")
@click.argument("field_name")
@click.pass_context
def source_clear_secret(ctx: click.Context, source_id: str, field_name: str) -> None:
    """Delete a sensitive field's stored value."""
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = _require_storage(ctx)
    try:
        clear_source_secret_value(
            source_id,
            plugin,
            storage,
            field_name,
            user_id=_SOURCE_DEFAULT_USER_ID,
        )
    except SourceConfigError as error:
        _abort_with(error.message)
    click.echo(f"Cleared secret for {source_id}.{field_name}.")
