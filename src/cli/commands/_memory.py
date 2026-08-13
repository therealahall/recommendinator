"""The ``memory`` group."""

from __future__ import annotations

import json

import click
from tabulate import tabulate


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
