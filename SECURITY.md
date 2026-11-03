# Security policy

**Report anything that lets a company reference reach an exported file, or lets DocSan reach the network, privately and without real documents.**

## What to report

| Report | Example |
|---|---|
| A leak | An exported file that passed every check still contains a name from the profile, in its text, metadata or pictures |
| A bypass of the release checks | A file that should be blocked is exported, or an exported file differs from the verified one |
| Network access | The app, the engine or a helper opens a connection |
| Private data escaping storage | Originals, profiles or mappings written outside the app's private storage |

A name that is not in the profile and that DocSan never asked about is a known limit, not a vulnerability. Open an issue for it, using a made-up example.

## How to report

Use GitHub's private vulnerability reporting when the **Security** tab offers **Report a vulnerability**. Do not open a public issue with vulnerability details. If that option is unavailable, open an issue containing only a request for a private contact route; wait for the maintainer before sending any details.

Include the DocSan version or commit, your macOS version and a way to reproduce the problem with a **made-up** document and profile. Never send a real document, company profile or exported file.

## What happens next

The maintainer reviews reports on a best-effort basis. This pre-release project has no guaranteed response time. A confirmed leak or bypass should receive a synthetic regression test, and the fix can credit the reporter with their permission.

## Supported versions

DocSan is pre-release. Fixes target the latest source; older snapshots have no backport commitment. A successful test run does not establish that arbitrary documents are safe. See [known limitations](docs/limitations.md).
