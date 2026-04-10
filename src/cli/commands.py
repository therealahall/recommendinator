"""CLI commands."""

import importlib.metadata
import json
import time
from pathlib import Path

import click
from tabulate import tabulate

from src.cli.config import get_feature_flags
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
from src.web.export import export_items_csv, export_items_json
from src.web.sync_sources import resolve_inputs, validate_source_config


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
    """Show system health, component readiness, and feature flags."""
    version = importlib.metadata.version("recommendinator")
    config = ctx.obj["config"]

    # Component readiness
    components = {
        "engine": ctx.obj.get("engine") is not None,
        "storage": ctx.obj.get("storage") is not None,
        "embedding_gen": ctx.obj.get("embedding_gen") is not None,
        "llm_client": ctx.obj.get("llm_client") is not None,
    }

    # Feature flags
    flags = get_feature_flags(config)

    # Max recommendation count
    rec_config = config.get("recommendations", {})
    max_count = rec_config.get("max_count", 20)

    if output_format == "json":
        output = {
            "version": version,
            "components": components,
            "features": flags,
            "recommendations": {"max_count": max_count},
        }
        click.echo(json.dumps(output, indent=2))
    else:
        click.echo(f"\nRecommendinator v{version}\n")

        click.echo("Components:")
        for name, ready in components.items():
            label = "ready" if ready else "not available"
            click.echo(f"  {name}: {label}")

        click.echo("\nFeatures:")
        for name, enabled in flags.items():
            label = "enabled" if enabled else "disabled"
            click.echo(f"  {name}: {label}")

        click.echo(f"\nMax recommendations: {max_count}")


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
    type=click.IntRange(min=1, max=20),
    default=5,
    help="Number of recommendations to generate (1-20)",
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
            click.echo(
                "No recommendations available. Recommendations are based on items "
                "you haven't consumed yet — try adding new items to your "
                "wishlist or library."
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
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID for filtering items",
)
@click.pass_context
def enrichment_start(
    ctx: click.Context, content_type_str: str | None, user_id: int
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

    if not manager.start_enrichment(content_type=content_type, user_id=user_id):
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
    storage = ctx.obj["storage"]

    stats = storage.get_enrichment_stats(user_id=user_id)

    if output_format == "json":
        click.echo(json.dumps(stats, indent=2))
    else:
        click.echo("Enrichment Statistics:")
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
        ["unread", "currently_consuming", "completed", "abandoned"],
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
@click.option("--limit", type=int, default=None, help="Max items to return")
@click.option("--offset", type=int, default=0, help="Items to skip")
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

    if not items:
        click.echo("No items found.")
        return

    if output_format == "json":
        output = [
            {
                "db_id": item.db_id,
                "title": item.title,
                "author": item.author,
                "content_type": get_enum_value(item.content_type),
                "status": get_enum_value(item.status),
                "rating": item.rating,
                "ignored": bool(item.ignored),
            }
            for item in items
        ]
        click.echo(json.dumps(output, indent=2))
    else:
        table_data = []
        for item in items:
            table_data.append(
                [
                    item.db_id,
                    item.title,
                    item.author or "N/A",
                    get_enum_value(item.content_type),
                    get_enum_value(item.status),
                    item.rating or "N/A",
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
        output = {
            "db_id": item.db_id,
            "title": item.title,
            "author": item.author,
            "content_type": get_enum_value(item.content_type),
            "status": get_enum_value(item.status),
            "rating": item.rating,
            "review": item.review,
            "date_completed": (
                item.date_completed.isoformat() if item.date_completed else None
            ),
            "ignored": bool(item.ignored),
        }
        click.echo(json.dumps(output, indent=2))
    else:
        table_data = [
            ["Title", item.title],
            ["Author", item.author or "N/A"],
            ["Type", get_enum_value(item.content_type)],
            ["Status", get_enum_value(item.status)],
            ["Rating", item.rating or "N/A"],
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
        ["unread", "currently_consuming", "completed", "abandoned"],
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
    storage = ctx.obj["storage"]

    # Look up the item to get current status if not provided
    item = storage.get_content_item(item_id, user_id=user_id)
    if item is None:
        click.echo(f"Error: Item {item_id} not found.", err=True)
        raise click.Abort()

    effective_status = status_str if status_str else get_enum_value(item.status)

    parsed_seasons: list[int] | None = None
    if seasons_watched is not None:
        parsed_seasons = [int(s.strip()) for s in seasons_watched.split(",")]

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
