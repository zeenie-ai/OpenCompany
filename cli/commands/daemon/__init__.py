"""``company daemon`` -- run the backend as a detached process.

Pure-Python, all-platforms. No NSSM / systemd / launchd integration.
Spawns ``uvicorn main:app`` in a new session (POSIX) or detached
process group (Windows), writes the PID under the project's
user-data dir (see :func:`cli.platform_.user_data_dir`), and uses
``psutil`` for tree-kill on stop.

Package layout (verb-per-file, following pdm's ``commands/venv/`` and
hatch's per-verb-folder convention):

  - :mod:`._state`  -- ``pid_dir`` / ``pid_file`` / ``read_pid`` /
                       ``kill_tree`` / ``detached_kwargs`` (lazy --
                       no module-level side effects)
  - :mod:`.start`   -- ``daemon start`` verb
  - :mod:`.stop`    -- ``daemon stop`` verb
  - :mod:`.status`  -- ``daemon status`` verb
  - :mod:`.restart` -- ``daemon restart`` verb

For boot-time auto-start, configure your OS service manager separately
(``systemctl``, ``launchctl``, Task Scheduler) -- this CLI does not
register itself with the system.
"""

from __future__ import annotations

import typer

# The ``app`` Typer instance is declared FIRST so the verb modules can
# import it via ``from . import app`` and register themselves via
# ``@app.command``. The verb-module imports BELOW are what trigger that
# registration -- without them, ``company daemon`` would be empty.
app = typer.Typer(
    name="daemon",
    help="Run the OpenCompany backend as a detached process.",
    no_args_is_help=True,
    add_completion=False,
)

# Import order matters only for ``restart`` (which imports ``start_command``
# + ``stop_command``); list those two first for clarity, then the rest.
from . import start  # noqa: E402,F401
from . import stop  # noqa: E402,F401
from . import status  # noqa: E402,F401
from . import restart  # noqa: E402,F401

__all__ = ["app"]
