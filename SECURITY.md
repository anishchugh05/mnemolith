# Security

Mnemolith runs on your computer, with no server and no telemetry. The part that can put data at risk is team
memory, which writes shared memories into your repo. [Team memory security](https://mnemolith.dev/team-security.html)
explains what it shares, what it never shares, and the secret check that runs before anything is written.

## Reporting a problem

If you find a way to get a secret past the check, or anything else that could leak data, please report it
privately: email hello@mnemolith.dev, or open a
[private security advisory](https://github.com/anishchugh05/mnemolith/security/advisories/new). Please don't open a
public issue for it. You'll get a reply within a few days.

## Supported versions

Fixes go into the latest release. Update with `uv tool upgrade mnemolith` (or reinstall the plugin).
