# Game 1016 scope

The user designated `game1016/` as the workspace for all new game work on 2026-10-16.

- Keep new source code, configuration, scripts, documentation, dependencies, recordings, and generated artifacts inside this directory.
- Treat the existing hackathon application outside this directory as reference material. Changes there require a specific user request.
- Read `docs/game-spec.md` before implementing the game. Its controls and milestones are proposals; linked upstream code defines benchmark behavior.
- Target RLWRLD's RoboCasa Kitchen24 implementation. Pin its revision and the actual RoboCasa, robosuite, MuJoCo, and asset versions. Keep RoboCasa365 separate.
- Use a dedicated local environment. Do not mutate dependencies shared with the older hackathon checkout through symlinks.
- Keep product UI in English. Developer documentation may be Korean.
- Keep raw simulator actions distinct from exported dataset actions. Preserve observation-before-action timing, the three canonical cameras, and native task success checks.
- Keep secrets, environments, downloaded assets, datasets, model weights, and generated recordings out of Git. Record source revisions and license information for external dependencies.
- Validate one task, one recorded episode, replay, and loader compatibility before expanding collection.
- The root repository's existing marketplace and agent instructions apply if the user later authorizes integration with those components.
