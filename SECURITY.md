# Security policy

## Scope

Report vulnerabilities affecting local process execution, file parsing,
downloaded simulator handling, WebUI/API boundaries, or future game-control
integrations privately to the maintainers before public disclosure.

## Local trust boundary

Rocket, mission, mod, save, and `.ork` files must be treated as untrusted input.
Adapters must:

- validate paths before reading or writing;
- avoid shell interpolation;
- constrain subprocess arguments;
- reject unexpected schema fields or incompatible versions;
- never commit tokens, game credentials, saves, or personal installation paths.

Future live-game integrations must require explicit opt-in before modifying a
save, loading a craft, installing a mod, or sending control commands.

## Supported versions

Until tagged releases exist, security fixes apply to the current default branch.
