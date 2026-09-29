"""Parse plain ``KEY=value`` secrets files without ever exposing their values.

Key files live at the outer workspace root, outside every Git repository, and are mounted read-only
into containers as Docker Compose secrets. Error messages cite line numbers only, never contents.
"""

from __future__ import annotations


class SecretsFileError(RuntimeError):
    """Raised when a secrets file is malformed. Never contains a key."""


def parse_secrets_file_text(secrets_file_text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines. Blank lines and ``#`` comments are ignored.

    Accepts an optional leading ``export`` and strips one pair of matching quotes
    around the value. Error messages cite line numbers only, never line contents.
    """
    parsed_variables: dict[str, str] = {}
    for line_number, raw_line in enumerate(secrets_file_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        variable_name, separator, variable_value = line.partition("=")
        if not separator or not variable_name.strip():
            raise SecretsFileError(f"secrets file line {line_number} is not in KEY=value form")
        variable_value = variable_value.strip()
        if len(variable_value) >= 2 and variable_value[0] == variable_value[-1] and variable_value[0] in "\"'":
            variable_value = variable_value[1:-1]
        parsed_variables[variable_name.strip()] = variable_value
    return parsed_variables
