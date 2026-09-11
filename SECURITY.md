# Security policy

## Reporting a vulnerability

Do not disclose credentials, private intelligence, personal data, or an
exploitable vulnerability in a public issue. Use GitHub's private vulnerability
reporting feature when it is enabled for this repository. If no private channel
is available, do not publish vulnerability details; wait for the repository
owner to enable GitHub Private Vulnerability Reporting.

Never include live tokens, `.env` files, operational databases, browser
profiles, SMTP credentials, source responses containing private data, or user
research records in a report.

## Current security boundary

Venture Intelligence is a local-first research prototype. It defaults to
loopback binding and does not provide production authentication, authorization,
tenant isolation, or a hardened internet-facing deployment. Do not expose it to
an untrusted network without adding and reviewing those controls.

Collection is limited to public or explicitly authorized sources. Private/local
network targets, login automation, CAPTCHA bypass, proxy evasion, credential
harvesting, and direct restricted-social automation are outside the supported
boundary. Credentials stay server-side and must be supplied through local
environment configuration.

Security fixes should preserve immutable evidence, provenance, source policy,
request budgets, and candidate-integrity gates. See [source policy](docs/SOURCES.md)
and [contribution guidance](CONTRIBUTING.md).
