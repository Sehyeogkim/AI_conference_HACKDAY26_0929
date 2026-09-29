"""Confirm the World Labs key file is present and well-formed, without any network call.

Run inside the jobs container: ``docker compose run --rm jobs python -m wefarm.check_secrets``.
Prints only whether the key was found; never the key or its length.
"""

from __future__ import annotations

import sys

from wefarm.worlds.worldlabs.api_key import (
    WORLD_LABS_API_KEY_VARIABLE_NAME,
    WorldLabsSecretsError,
    load_world_labs_api_key,
    resolve_world_labs_secrets_file_path,
)


def main() -> int:
    try:
        load_world_labs_api_key()
    except WorldLabsSecretsError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"OK: {WORLD_LABS_API_KEY_VARIABLE_NAME} found in {resolve_world_labs_secrets_file_path()} (value not shown)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
