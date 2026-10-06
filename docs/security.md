# Security

graph-ted-db stores your graph as **ordinary files**. The library does **not** encrypt the graph folder.

Security matches the environment you choose:

- **Disk / volume encryption** and OS account permissions
- **Who can read the folder** (including sync clients and backups)
- **Whether anything listens on the network**

## Practical guidance

- **Airgapped host with an encrypted volume** — can be very strong; treat that environment as your boundary.
- **Synced, shared, or cloud-backed folders** — anyone or any tool with access to the folder can read what you stored, including property values.
- **HTTP serve** — defaults to localhost. Binding beyond loopback requires an explicit host and a token. That protects the *port*, not the files on disk.

The library will not make an insecure disk secure. Choose storage and network to match how sensitive the data is.
