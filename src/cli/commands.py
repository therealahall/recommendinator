"""CLI commands."""

import json

import click
from tabulate import tabulate

from src.ingestion.plugin_base import SourceError
from src.ingestion.registry import get_registry
from src.ingestion.sync import execute_sync
from src.models.content import ConsumptionStatus, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.scorers import SCORER_NAME_MAP
from src.web.sync_sources import transform_source_config, validate_source_config


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
    # Map string to ContentType enum
    type_map = {
        "book": ContentType.BOOK,
        "movie": ContentType.MOVIE,
        "tv_show": ContentType.TV_SHOW,
        "video_game": ContentType.VIDEO_GAME,
    }
    content_type = type_map[content_type_str.lower()]

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
        raise click.Abort() from e


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

    registry = get_registry()
    inputs_config = config.get("inputs", {})

    # Handle 'list' to show available sources
    if source == "list":
        enabled_plugins = registry.get_enabled_plugins(config)
        all_plugins = registry.get_all_plugins()

        if not all_plugins:
            click.echo("No plugins discovered.")
            return

        click.echo("Available sources:")
        for name in sorted(all_plugins):
            plugin = all_plugins[name]
            source_config = inputs_config.get(name, {})
            enabled = (
                isinstance(source_config, dict)
                and source_config.get("enabled", False)
            )
            status = "enabled" if enabled else "disabled"
            click.echo(f"  {name:20s} {plugin.description} [{status}]")
        return

    # Check if embeddings are enabled
    features_config = config.get("features", {})
    ai_enabled = features_config.get("ai_enabled", False)
    embeddings_enabled = features_config.get("embeddings_enabled", False)
    use_embeddings = ai_enabled and embeddings_enabled

    # Determine which sources to sync
    if source == "all":
        enabled_plugins = registry.get_enabled_plugins(config)
        if not enabled_plugins:
            click.echo("No sources enabled in config. Use --source list to see available sources.")
            return
        sources_to_sync = [(plugin.name, plugin) for plugin in enabled_plugins]
    else:
        found_plugin = registry.get_plugin(source)
        if found_plugin is None:
            click.echo(
                f"Error: Unknown source '{source}'. "
                "Use --source list to see available sources.",
                err=True,
            )
            raise click.Abort()

        source_config = inputs_config.get(source, {})
        if not isinstance(source_config, dict) or not source_config.get("enabled", False):
            click.echo(f"{source} source is disabled in config.")
            return

        validation_errors = validate_source_config(source, inputs_config)
        if validation_errors:
            for error in validation_errors:
                click.echo(f"Error: {error}", err=True)
            raise click.Abort()

        sources_to_sync = [(source, found_plugin)]

    click.echo(f"Updating data from {', '.join(name for name, _ in sources_to_sync)}...")

    try:
        total_count = 0

        for source_id, plugin in sources_to_sync:
            source_config = inputs_config.get(source_id, {})
            plugin_config = transform_source_config(source_id, source_config)

            validation_errors = validate_source_config(source_id, inputs_config)
            if validation_errors:
                for error in validation_errors:
                    click.echo(f"  {plugin.display_name}: Error: {error}", err=True)
                continue

            click.echo(f"  Syncing {plugin.display_name}...")

            def cli_progress(
                items_processed: int,
                total_items: int | None,
                current_item: str | None,
            ) -> None:
                if total_items and items_processed > 0 and items_processed % 10 == 0:
                    click.echo(f"    Processed {items_processed}/{total_items}...")

            try:
                result = execute_sync(
                    plugin=plugin,
                    plugin_config=plugin_config,
                    storage_manager=storage,
                    embedding_generator=embedding_gen,
                    use_embeddings=use_embeddings,
                    progress_callback=cli_progress,
                )

                click.echo(
                    f"  Updated {result.items_synced} items from {plugin.display_name}"
                )
                if result.errors:
                    for error in result.errors:
                        click.echo(f"    Warning: {error}", err=True)

                total_count += result.items_synced

            except SourceError as error:
                click.echo(f"  Error syncing {plugin.display_name}: {error}", err=True)
            except Exception as error:
                click.echo(f"  Error syncing {plugin.display_name}: {error}", err=True)

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
    config = ctx.obj["config"]

    # Check if embeddings are enabled
    features_config = config.get("features", {})
    ai_enabled = features_config.get("ai_enabled", False)
    embeddings_enabled = features_config.get("embeddings_enabled", False)
    use_embeddings = ai_enabled and embeddings_enabled

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
    except Exception as e:
        click.echo(f"Error marking content as completed: {e}", err=True)
        raise click.Abort() from e


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
    from src.recommendations.preference_interpreter import PatternBasedInterpreter

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
        from src.recommendations.preference_interpreter import LLMPreferenceInterpreter

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
