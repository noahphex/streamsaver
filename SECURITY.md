# Security

Streamsaver is intended for a home network or behind a VPN or reverse proxy
with authentication. It is not hardened for direct internet exposure.

Credentials are encrypted at rest with your passphrase. An optional environment
variable can unlock them for unattended jobs; protect that environment and keep
your passphrase private. Viewing data, settings, and exported backups are not
encrypted by Streamsaver.

To report a vulnerability privately, open a
[GitHub security advisory](https://github.com/noahphex/streamsaver/security/advisories/new).
Please avoid posting credentials or exploit details in public issues.
