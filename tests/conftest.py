"""Configuration for the cross-cutting tests under ``tests/``.

Session-wide isolation fixtures live in the repository-root ``conftest.py`` so
that the plugin-local tests under ``src/`` get them too.
"""

# Make shared fakes / fixtures available to every test file without an import
# (which otherwise collides with pytest's fixture-name-as-parameter idiom).
pytest_plugins = ["tests.fakes.source_plugins"]
