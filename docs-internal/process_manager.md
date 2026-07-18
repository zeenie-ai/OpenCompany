# Process Manager

`processManager` owns long-running workflow subprocesses such as development
servers and watchers. Processes are isolated by workflow ID and name and are
visible in the node's dedicated full-height middle panel.

## Port-safe startup

Server processes should declare every listener in the `ports` parameter. The
service also recognizes `--port N`, `--port=N`, `-p N`, `PORT`, and `*_PORT`
for compatibility. Positional numbers are deliberately not guessed.

Startup is serialized through one admission lock. Before spawning, the service
checks both ports reserved by managed processes and operating-system TCP
listeners. A collision returns `PORT_IN_USE` with the occupied ports and known
owner PIDs. It never kills an unrelated listener or silently changes the port.
Explicit ports and environment settings survive restart.

The unavoidable boundary is a process that neither declares a port nor uses a
recognized command/environment form. Connectors and agents should always fill
`ports` for server commands.

## Middle panel

The panel is scoped to the current workflow and polls the in-memory process
registry for live state. It shows running capacity, status, PID, declared
ports, start time, elapsed time, output counts, command, and recent stdout.
Operators can stop or restart a process. Stopped elapsed time freezes at
`stopped_at`; running elapsed time updates once per second.

Process output is stored under `<workspace>/.processes/<name>/` while the
process remains inspectable. Completed entries and logs retain the existing
60-second cleanup policy.
