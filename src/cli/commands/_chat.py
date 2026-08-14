"""The ``chat`` group."""

from __future__ import annotations

import json

import click

from src.cli._shared import abort_after_failure, report_failure
from src.config.service import get_feature_flags
from src.conversation.engine import ConversationEngine
from src.models.content import ContentType

#: What the chat SSE stream sends when the engine raises mid-answer, so both
#: interfaces refuse a message in the same words.
CHAT_FAILED = "An internal error occurred"

#: The log's half of that pair, one per verb, since the wording above names
#: neither the command nor the operation.
CHAT_SEND_LOGGED = "Chat send failed"
CHAT_MESSAGE_LOGGED = "Chat message processing failed"


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
    """Create a ConversationEngine from CLI context.

    Through the provider rather than a section snapshot, so the engine reads
    the ``conversation`` section it was previously blind to — it ran on the
    hardcoded defaults whatever the database and config.yaml said.
    """
    storage = ctx.obj["storage"]
    ollama_client = ctx.obj["llm_client"]
    engine = ctx.obj["engine"]
    return ConversationEngine(
        storage_manager=storage,
        ollama_client=ollama_client,
        recommendation_engine=engine,
        config_provider=lambda: ctx.obj["config"],
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
        except Exception as error:
            # Reported rather than aborted: one bad message must not end the
            # session the operator is still typing into.
            report_failure(ctx, CHAT_FAILED, error, CHAT_MESSAGE_LOGGED)


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
    except Exception as error:
        abort_after_failure(ctx, CHAT_FAILED, error, CHAT_SEND_LOGGED)


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
