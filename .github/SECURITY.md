# Security Policy

## Supported versions

Security fixes target the latest supported `0.x` release. Preview builds are not
stable releases; each release page must state whether that exact build is
supported.

## Reporting a vulnerability

Do not create a public issue containing credentials, full email addresses,
cookies, tokens, account snapshots, or unredacted logs. If private vulnerability
reporting is enabled for this repository, use the **Security** tab to contact the
maintainers privately. Otherwise use a verified contact method on the repository
owner's GitHub profile. Send only a minimal reproduction and sanitized diagnostics.

## Security boundaries

QuotaLab's local dashboard listens on `127.0.0.1`/`localhost`, rejects
cross-origin state-changing requests, and does not support public-network
binding. It reads data through the Codex client already present on the same
computer. Do not expose its local port through a reverse proxy, tunnel, or router.
