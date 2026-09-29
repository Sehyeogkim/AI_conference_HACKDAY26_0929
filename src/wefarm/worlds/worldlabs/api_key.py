"""Load the World Labs API key from its mounted secrets file without ever exposing it.

The key file is ``.env.secrets.worldlabs`` at the outer workspace root (``WLT_API_KEY=<key>``). Inside
the ``world-builder`` container it is mounted read-only at ``/run/secrets/world_labs_api_key``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from wefarm.secrets_file import SecretsFileError, parse_secrets_file_text

WORLD_LABS_API_KEY_VARIABLE_NAME = "WLT_API_KEY"
WORLD_LABS_SECRETS_FILE_ENVIRONMENT_VARIABLE = "WORLD_LABS_SECRETS_FILE"
DEFAULT_WORLD_LABS_SECRETS_FILE_PATH = Path("/run/secrets/world_labs_api_key")
REDACTED_PLACEHOLDER = "<redacted>"


class WorldLabsSecretsError(RuntimeError):
    """Raised when the key file is missing or malformed. Never contains the key."""


@dataclass(frozen=True)
class WorldLabsApiKey:
    """An API key that refuses to reveal itself through str(), repr(), or formatting."""

    _value: str

    def reveal(self) -> str:
        """Return the raw key. Call only when building the request header for the World Labs API host."""
        return self._value

    def scrub(self, text: str) -> str:
        """Replace every occurrence of the key in `text` with a placeholder."""
        if not self._value:
            return text
        return text.replace(self._value, REDACTED_PLACEHOLDER)

    def __repr__(self) -> str:
        return f"WorldLabsApiKey({REDACTED_PLACEHOLDER})"

    __str__ = __repr__

    def __format__(self, format_spec: str) -> str:
        return repr(self)


def resolve_world_labs_secrets_file_path() -> Path:
    """Use WORLD_LABS_SECRETS_FILE if set, otherwise the container secret path."""
    configured_path = os.environ.get(WORLD_LABS_SECRETS_FILE_ENVIRONMENT_VARIABLE)
    return Path(configured_path) if configured_path else DEFAULT_WORLD_LABS_SECRETS_FILE_PATH


def load_world_labs_api_key(secrets_file_path: Path | None = None) -> WorldLabsApiKey:
    """Read the key file and return the World Labs key, redacted by default."""
    path = secrets_file_path or resolve_world_labs_secrets_file_path()
    if not path.is_file():
        raise WorldLabsSecretsError(
            f"World Labs secrets file not found at {path}. Create .env.secrets.worldlabs at the workspace "
            f"root containing a line {WORLD_LABS_API_KEY_VARIABLE_NAME}=<your key>."
        )
    try:
        parsed_variables = parse_secrets_file_text(path.read_text(encoding="utf-8"))
    except SecretsFileError as error:
        raise WorldLabsSecretsError(f"World Labs secrets file is malformed: {error}") from None
    api_key_value = parsed_variables.get(WORLD_LABS_API_KEY_VARIABLE_NAME, "")
    if not api_key_value:
        raise WorldLabsSecretsError(f"{WORLD_LABS_API_KEY_VARIABLE_NAME} is missing or empty in {path}")
    return WorldLabsApiKey(api_key_value)
