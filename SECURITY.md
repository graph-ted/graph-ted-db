# Security policy

## Reporting a vulnerability

Please report security vulnerabilities **privately**. Do not open a public issue, discussion, or pull request for a suspected vulnerability.

Email **developer.agent@graph-ted.com** with:

- the graph-ted-db version (or commit SHA) and your Python version and OS
- what an attacker can do, and what access they need first (local account, network reach to the HTTP port, write access to the graph folder, and so on)
- steps or a minimal script to reproduce it
- any suggested fix, if you have one

We will reply to confirm we received your report, keep you updated while we investigate, and credit you in the release notes when the fix ships unless you'd rather stay anonymous. Please give us a reasonable chance to release a fix before you disclose the issue publicly.

## Supported versions

graph-ted-db is pre-1.0. Security fixes land on `main` and go into the next release. Please test against the latest release or `main` before reporting.

## Security model

graph-ted-db stores your graph as **ordinary files**, and the library does **not** encrypt the graph folder. The storage and network you choose are the security boundary. See [docs/security.md](docs/security.md) for the full guidance.

- **Files on disk.** Anyone or any tool that can read the graph folder (OS accounts, sync clients, backups) can read everything in it, including property values. Use disk or volume encryption and folder permissions that match how sensitive the data is.
- **HTTP serve is optional.** It defaults to localhost. Binding beyond loopback requires an explicit host and a token. When a token is set, every endpoint except `GET /health` requires it. The token protects the *port*, not the files on disk. Anyone who can reach the port with the token can run any Cypher the endpoint accepts, including writes.
- **Query parameters.** Values passed as Cypher parameters (`$name`) are bound as values when the query runs. They are never spliced into the query text, so parameter values cannot change the query. Build queries with parameters rather than string formatting.

### In scope

- Ways to read or change a graph through the library or HTTP serve that bypass the boundaries above, for example reaching a non-loopback server without the token
- Parameter values that change how a query is parsed or executed
- Crafted graph files or queries that crash the process, corrupt other data, or execute code

### Out of scope

- Reading a graph folder that you have shared, synced, or left unencrypted (documented behavior)
- Running HTTP serve off loopback with a weak or leaked token
- Vulnerabilities in third-party dependencies with no graph-ted-db-specific impact (please report those upstream)
