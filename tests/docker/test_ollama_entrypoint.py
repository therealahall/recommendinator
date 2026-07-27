"""Behavioural tests for docker/ollama-entrypoint.sh model resolution.

The script previously scraped model names out of config.yaml with sed/grep;
it now takes them from the environment, because the app's global config moved
into the database and the sidecar cannot read it. That rewrite shipped with no
coverage, so these pin the resolution rules.

The script's second half starts a real ``ollama serve`` and blocks, so these
extract the parts under test from the real file rather than running it whole:
the variable-resolution prologue and the pull loop. Both are read out of
``docker/ollama-entrypoint.sh`` at test time, so a change to the script changes
what runs here — a hand-retyped copy would assert against itself and pass no
matter what the script said. No Docker daemon and no network.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from src.settings.metadata import default_of

# parents[2] resolves /tests/docker/test_ollama_entrypoint.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = _REPO_ROOT / "docker" / "ollama-entrypoint.sh"
COMPOSE = _REPO_ROOT / "docker-compose.yml"

# The defaults baked into the script, duplicated deliberately: if either drifts
# from docker-compose.yml or the settings registry, these tests are the thing
# that notices — see TestOllamaDefaultsAgreeAcrossAllThreeSurfaces.
_DEFAULT_MODEL = "mistral:7b"
_DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"

# Matches a compose environment entry of the form VAR=${VAR:-default}. Read from
# the raw text rather than the parsed YAML because the interpolation syntax is
# the thing under test, and a YAML load would hand back the literal either way.
_COMPOSE_ENV = re.compile(r"^\s*-\s*(OLLAMA_\w+)=\$\{\1:-(.*)\}\s*$", re.MULTILINE)


def _compose_env_defaults() -> dict[str, str]:
    """Return every ``OLLAMA_*=${OLLAMA_*:-default}`` default in docker-compose.yml."""
    return dict(_COMPOSE_ENV.findall(COMPOSE.read_text()))


@pytest.fixture()
def resolver(tmp_path: Path):
    """Run only the script's variable-resolution prologue and report the result.

    The script is sourced up to (but not including) the server start, then the
    three resolved names are echoed. This keeps the test on the logic that
    changed without booting an LLM server.
    """
    prologue = "\n".join(
        line
        for line in ENTRYPOINT.read_text().splitlines()
        if line.startswith(("MODEL=", "EMBEDDING_MODEL=", "CONVERSATION_MODEL="))
    )
    script = tmp_path / "resolve.sh"
    script.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        + prologue
        + '\necho "$MODEL|$EMBEDDING_MODEL|$CONVERSATION_MODEL"\n'
    )
    script.chmod(0o755)

    def _resolve(**env: str) -> tuple[str, str, str]:
        result = subprocess.run(
            [str(script)],
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", **env},
            capture_output=True,
            text=True,
            check=True,
        )
        generation, embedding, conversation = result.stdout.strip().split("|")
        return generation, embedding, conversation

    return _resolve


class TestOllamaModelResolution:
    """Which models the sidecar pulls, given the compose environment."""

    def test_no_variables_uses_the_baked_in_defaults(self, resolver) -> None:
        """A bare ``docker run`` with no environment still pulls usable models.

        These defaults must match the ``ollama.model`` / ``ollama.embedding_model``
        registry defaults the app requests, or a default install pulls one model
        and asks for another.
        """
        generation, embedding, conversation = resolver()

        assert generation == _DEFAULT_MODEL
        assert embedding == _DEFAULT_EMBEDDING_MODEL
        # No separate chat model configured: reuse the generation model, which
        # mirrors the app's own fallback when ollama.conversation_model is empty.
        assert conversation == _DEFAULT_MODEL

    def test_empty_conversation_model_falls_back_to_the_generation_model(
        self, resolver
    ) -> None:
        """An explicitly EMPTY value must be treated as unset.

        This is the load-bearing case: docker-compose.yml always sets
        ``OLLAMA_CONVERSATION_MODEL=${OLLAMA_CONVERSATION_MODEL:-}``, so the
        variable is present and empty on every compose run. The script relies on
        ``${VAR:-default}`` rather than ``${VAR-default}`` — one character apart.
        With the wrong form the name resolves to "", the already-downloaded
        check greps for "^" (matching any line), and the chat model is silently
        never pulled. That surfaces much later as a runtime Ollama error.
        """
        generation, _embedding, conversation = resolver(
            OLLAMA_MODEL="llama3.1:8b", OLLAMA_CONVERSATION_MODEL=""
        )

        assert conversation == "llama3.1:8b"
        assert conversation == generation

    def test_explicit_conversation_model_is_used(self, resolver) -> None:
        generation, _embedding, conversation = resolver(
            OLLAMA_MODEL="llama3.1:8b", OLLAMA_CONVERSATION_MODEL="qwen2.5:3b"
        )

        assert generation == "llama3.1:8b"
        assert conversation == "qwen2.5:3b"

    def test_each_model_is_overridable_independently(self, resolver) -> None:
        generation, embedding, _conversation = resolver(
            OLLAMA_EMBEDDING_MODEL="mxbai-embed-large"
        )

        assert embedding == "mxbai-embed-large"
        # Overriding one must not disturb the others.
        assert generation == _DEFAULT_MODEL


class TestOllamaAlreadyDownloadedCheck:
    """The ``ollama list`` grep that decides whether to pull.

    Survived the rewrite uncovered. The anchor is what stops a short model name
    substring-matching a longer one, and the dot-escape stops ``llama3.2``
    matching ``llama3a2``.

    The loop is extracted from the real script, so deleting the anchor or the
    escape there fails these — an inline copy of the grep would not.
    """

    @pytest.mark.parametrize(
        ("model", "listed", "expect_pull"),
        [
            # Anchoring: "text" must NOT match the line "nomic-embed-text",
            # i.e. it is absent and must be pulled.
            ("text", "nomic-embed-text", True),
            ("nomic-embed-text", "nomic-embed-text", False),
            # The escaped dot: "llama3.2" must not match "llama3a2".
            ("llama3.2", "llama3a2", True),
            ("llama3.2", "llama3.2", False),
        ],
    )
    def test_grep_anchors_and_escapes(
        self, tmp_path: Path, model: str, listed: str, expect_pull: bool
    ) -> None:
        # A fake `ollama` on PATH: `list` prints the fixture table, `pull`
        # records what it was asked for. This is what makes the assertion about
        # the real script's behaviour rather than about a retyped regex.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        pulled = tmp_path / "pulled.txt"
        stub = bin_dir / "ollama"
        stub.write_text(
            "#!/bin/bash\n"
            'if [ "$1" = "list" ]; then\n'
            f'    echo "{listed}"\n'
            'elif [ "$1" = "pull" ]; then\n'
            f'    echo "$2" >> "{pulled}"\n'
            "fi\n"
        )
        stub.chmod(0o755)

        # Extract the real already-downloaded conditional, with pull_model
        # reduced to the stub call so no progress parsing is involved.
        source = ENTRYPOINT.read_text()
        start = source.index("for model_name in ")
        loop = source[start : source.index("done", start) + len("done")]
        loop = loop.replace(
            'for model_name in "$MODEL" "$EMBEDDING_MODEL" "$CONVERSATION_MODEL"',
            'for model_name in "$1"',
        )
        script = tmp_path / "check.sh"
        script.write_text(
            "#!/bin/bash\nset -euo pipefail\n"
            "log() { :; }\n"
            'pull_model() { ollama pull "$1"; }\n' + loop + "\n"
        )
        script.chmod(0o755)

        subprocess.run(
            [str(script), model],
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": "/tmp"},
            capture_output=True,
            text=True,
            check=True,
        )

        was_pulled = pulled.exists() and model in pulled.read_text()
        assert was_pulled is expect_pull


class TestOllamaDefaultsAgreeAcrossAllThreeSurfaces:
    """The same model default is written in three files that cannot see each other.

    The sidecar script bakes in defaults, docker-compose.yml re-declares them as
    ``${VAR:-default}``, and the settings registry holds the value the *app*
    requests. Nothing links them, so a bump in one place leaves the sidecar
    pulling one model while the app asks for another — which surfaces only as a
    404 from Ollama at first use, long after the change.

    The module constants above claimed to be the thing that notices that drift,
    but only the script was ever read. These close the loop.
    """

    def test_script_defaults_match_the_settings_registry(self, resolver) -> None:
        generation, embedding, _conversation = resolver()

        assert generation == default_of("ollama.model")
        assert embedding == default_of("ollama.embedding_model")

    def test_registry_conversation_default_is_empty_so_the_fallback_engages(
        self, resolver
    ) -> None:
        """The app's empty default is what makes the sidecar's reuse correct.

        If ``ollama.conversation_model`` ever gains a non-empty default, the
        sidecar would keep pulling the *generation* model for chat and the app
        would request the new one.
        """
        assert default_of("ollama.conversation_model") == ""

        _generation, _embedding, conversation = resolver()
        assert conversation == default_of("ollama.model")

    @pytest.mark.parametrize(
        "variable",
        ["OLLAMA_MODEL", "OLLAMA_EMBEDDING_MODEL"],
    )
    def test_compose_defaults_match_the_script(self, variable: str, resolver) -> None:
        compose_defaults = _compose_env_defaults()
        generation, embedding, _conversation = resolver()
        from_script = {
            "OLLAMA_MODEL": generation,
            "OLLAMA_EMBEDDING_MODEL": embedding,
        }[variable]

        assert compose_defaults[variable] == from_script

    def test_compose_leaves_the_conversation_model_empty(self) -> None:
        """Compose must pass an empty value, not a model name.

        An empty ``OLLAMA_CONVERSATION_MODEL`` is what routes through the
        script's ``${VAR:-$MODEL}`` fallback; naming a model here would pin chat
        to it regardless of what the Settings page says.
        """
        assert _compose_env_defaults()["OLLAMA_CONVERSATION_MODEL"] == ""
