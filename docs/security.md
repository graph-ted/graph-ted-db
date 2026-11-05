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

## Signatures (optional)

With the `signing` extra and a device key, each device signs the bundles it
publishes (see [Signatures](format.md#signatures-optional-gtsig-version-1)).

- **Stops:** one device publishing under another device's writer id, and edits
  to a sealed bundle after it was published. Both are reported as "doesn't
  match"; the records still load.
- **Does not stop:** deleting whole files, reading the folder, a brand-new
  writer that never claimed to be someone else (its bundles are unsigned or
  from an unknown signer), clock games, or several users behind one shared
  store process (one device key signs for all of them).
- **Keys** stay in local app data (mode 600), never in the graph folder,
  exports or backups. There is no recovery copy: a lost key means a new signer
  and a new writer id; the old claim stays in the folder and its bundles keep
  verifying.
- **Trust on first use:** each device trusts the first claim it sees for a
  writer. A forged claim that reaches a device first is trusted on that device.
