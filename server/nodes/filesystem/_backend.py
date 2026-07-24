"""Native workspace filesystem backend shared by filesystem plugins.

Filesystem operations use virtual POSIX paths rooted at the workflow
workspace. Resolution follows existing symlinks, then verifies that the
resolved target remains below that root. Shell execution retains the
historical host-shell behavior and uses Nushell when it is available.
"""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import os
import posixpath
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Dict, Optional
from uuid import uuid4
from weakref import WeakValueDictionary


EMPTY_CONTENT_WARNING = "System reminder: File exists but has empty contents"


@dataclass
class ExecuteResponse:
    output: str
    exit_code: Optional[int] = None
    truncated: bool = False


@dataclass
class ReadResult:
    error: Optional[str] = None
    file_data: Optional[Dict[str, Any]] = None


_path_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def get_path_lock(path: os.PathLike[str] | str) -> asyncio.Lock:
    """Return the process-local lock for one resolved workspace path."""
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    lock = _path_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _path_locks[key] = lock
    return lock


async def run_sync_until_complete(
    func: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run sync filesystem work without abandoning it on cancellation."""
    worker = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    cancellation: Optional[asyncio.CancelledError] = None

    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
        except BaseException:
            break

    if cancellation is not None:
        if not worker.cancelled():
            try:
                worker.result()
            except BaseException:
                pass
        raise cancellation
    return worker.result()


def _file_identity(status: os.stat_result) -> tuple[int, int]:
    return int(status.st_dev), int(status.st_ino)


def _is_reparse_point(status: os.stat_result) -> bool:
    attributes = int(getattr(status, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return stat.S_ISLNK(status.st_mode) or bool(attributes & reparse_flag)


def _anchored_write_supported() -> bool:
    return (
        os.name != "nt"
        and hasattr(os, "O_DIRECTORY")
        and os.open in os.supports_dir_fd
        and os.mkdir in os.supports_dir_fd
        and os.rename in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
    )


def _open_posix_directory(
    root: Path,
    components: tuple[str, ...],
    *,
    create: bool,
) -> int:
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    directory_fd = os.open(root, flags)
    try:
        for component in components:
            try:
                next_fd = os.open(
                    component,
                    flags,
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, mode=0o777, dir_fd=directory_fd)
                next_fd = os.open(
                    component,
                    flags,
                    dir_fd=directory_fd,
                )
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except BaseException:
        os.close(directory_fd)
        raise


def _verify_posix_directory(
    root: Path,
    components: tuple[str, ...],
    expected_fd: int,
) -> None:
    current_fd = _open_posix_directory(root, components, create=False)
    try:
        if _file_identity(os.fstat(current_fd)) != _file_identity(
            os.fstat(expected_fd)
        ):
            raise ValueError(
                "Workspace parent directory changed during atomic write"
            )
    finally:
        os.close(current_fd)


def _write_utf8_to_fd(
    file_fd: int,
    content: str,
    existing_mode: Optional[int],
) -> None:
    with os.fdopen(file_fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        if existing_mode is not None and hasattr(os, "fchmod"):
            os.fchmod(handle.fileno(), existing_mode)


def _replace_posix_entry(
    temporary_name: str,
    target_name: str,
    parent_fd: int,
) -> None:
    os.rename(
        temporary_name,
        target_name,
        src_dir_fd=parent_fd,
        dst_dir_fd=parent_fd,
    )


def _atomic_write_text_posix(
    root: Path,
    relative: Path,
    content: str,
) -> None:
    parent_components = tuple(relative.parts[:-1])
    target_name = relative.parts[-1]
    parent_fd = _open_posix_directory(
        root,
        parent_components,
        create=True,
    )
    temporary_name: Optional[str] = None
    try:
        existing_mode: Optional[int] = None
        try:
            target_status = os.stat(
                target_name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            if _is_reparse_point(target_status):
                raise ValueError(
                    f"Refusing to replace symlink target '{relative.as_posix()}'"
                )
            if stat.S_ISDIR(target_status.st_mode):
                raise IsADirectoryError(
                    f"Cannot write to {relative.as_posix()}: path is a directory"
                )
            if not stat.S_ISREG(target_status.st_mode):
                raise ValueError(
                    f"Refusing to replace non-regular file "
                    f"'{relative.as_posix()}'"
                )
            existing_mode = stat.S_IMODE(target_status.st_mode)

        temporary_fd: Optional[int] = None
        for _ in range(16):
            candidate = f".opencompany-write-{uuid4().hex}"
            try:
                temporary_fd = os.open(
                    candidate,
                    (
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0)
                    ),
                    0o600,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_fd is None or temporary_name is None:
            raise FileExistsError(
                "Could not allocate a unique atomic-write temporary file"
            )

        _write_utf8_to_fd(temporary_fd, content, existing_mode)
        _verify_posix_directory(root, parent_components, parent_fd)
        _replace_posix_entry(
            temporary_name,
            target_name,
            parent_fd,
        )
        temporary_name = None
        try:
            os.fsync(parent_fd)
        except OSError:
            pass
        _verify_posix_directory(root, parent_components, parent_fd)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except OSError:
                pass
        os.close(parent_fd)


def _windows_parent_snapshot(
    root: Path,
    components: tuple[str, ...],
    *,
    create: bool,
) -> tuple[Path, tuple[tuple[int, int], ...]]:
    current = root
    identities: list[tuple[int, int]] = []
    for index, component in enumerate(("", *components)):
        if index:
            current = current / component
            try:
                status = os.stat(current, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(current)
                status = os.stat(current, follow_symlinks=False)
        else:
            status = os.stat(current, follow_symlinks=False)

        if _is_reparse_point(status):
            raise ValueError(
                f"Workspace path contains a symlink or junction: {current}"
            )
        if not stat.S_ISDIR(status.st_mode):
            raise NotADirectoryError(
                f"Workspace parent component is not a directory: {current}"
            )
        identities.append(_file_identity(status))

    try:
        current.resolve(strict=True).relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            f"Workspace parent resolves outside root: {current}"
        ) from exc
    return current, tuple(identities)


def _verify_windows_temp(
    temporary: Path,
    expected_identity: tuple[int, int],
) -> None:
    status = os.stat(temporary, follow_symlinks=False)
    if _is_reparse_point(status) or _file_identity(status) != expected_identity:
        raise ValueError(
            "Atomic-write temporary file changed before replacement"
        )


def _atomic_write_text_windows(
    root: Path,
    relative: Path,
    content: str,
) -> None:
    """Best-effort contained atomic replacement for Windows.

    Python exposes no Windows equivalent of POSIX ``dir_fd``-relative rename.
    This path rejects every observed reparse component and compares parent and
    temporary-file identities immediately before and after ``os.replace``.
    A hostile process that can swap the parent between a check and a path-based
    ``mkdir``/temporary-file operation can cause an empty directory or
    temporary file to be created outside the workspace; payload bytes are not
    written until the temporary identity is revalidated. A swap in the final
    identity-check-to-``os.replace`` interval can redirect the completed
    replacement; the post-check detects that event but cannot safely roll it
    back. Python's standard library exposes no handle-relative Windows rename
    primitive that closes these intervals.
    """

    components = tuple(relative.parts[:-1])
    parent, parent_snapshot = _windows_parent_snapshot(
        root,
        components,
        create=True,
    )
    target = parent / relative.parts[-1]
    existing_mode: Optional[int] = None
    try:
        target_status = os.stat(target, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if _is_reparse_point(target_status):
            raise ValueError(
                f"Refusing to replace symlink or junction target "
                f"'{relative.as_posix()}'"
            )
        if stat.S_ISDIR(target_status.st_mode):
            raise IsADirectoryError(
                f"Cannot write to {relative.as_posix()}: path is a directory"
            )
        if not stat.S_ISREG(target_status.st_mode):
            raise ValueError(
                f"Refusing to replace non-regular file "
                f"'{relative.as_posix()}'"
            )
        existing_mode = stat.S_IMODE(target_status.st_mode)

    temporary_fd, temporary_text = tempfile.mkstemp(
        prefix=".opencompany-write-",
        dir=parent,
    )
    temporary = Path(temporary_text)
    temporary_identity = _file_identity(os.fstat(temporary_fd))
    replaced = False
    try:
        current_parent, current_snapshot = _windows_parent_snapshot(
            root,
            components,
            create=False,
        )
        if current_parent != parent or current_snapshot != parent_snapshot:
            raise ValueError(
                "Workspace parent directory changed during atomic write"
            )
        _verify_windows_temp(temporary, temporary_identity)

        write_fd = temporary_fd
        temporary_fd = -1
        _write_utf8_to_fd(write_fd, content, existing_mode)
        if existing_mode is not None:
            # Windows has no ``os.fchmod``. Apply its supported mode bits
            # (notably the read-only flag) to the still-private temporary
            # entry, then revalidate that entry below before replacement.
            os.chmod(temporary, existing_mode)

        current_parent, current_snapshot = _windows_parent_snapshot(
            root,
            components,
            create=False,
        )
        if current_parent != parent or current_snapshot != parent_snapshot:
            raise ValueError(
                "Workspace parent directory changed during atomic write"
            )
        _verify_windows_temp(temporary, temporary_identity)
        try:
            current_target = os.stat(target, follow_symlinks=False)
        except FileNotFoundError:
            current_target = None
        if current_target is not None and _is_reparse_point(current_target):
            raise ValueError(
                "Atomic-write target became a symlink or junction"
            )

        os.replace(temporary, target)
        replaced = True

        current_parent, current_snapshot = _windows_parent_snapshot(
            root,
            components,
            create=False,
        )
        if current_parent != parent or current_snapshot != parent_snapshot:
            raise ValueError(
                "Workspace parent directory changed during atomic replacement"
            )
        replaced_status = os.stat(target, follow_symlinks=False)
        if (
            _is_reparse_point(replaced_status)
            or _file_identity(replaced_status) != temporary_identity
        ):
            raise ValueError(
                "Atomic-write target identity changed during replacement"
            )
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if not replaced:
            try:
                current_parent, current_snapshot = _windows_parent_snapshot(
                    root,
                    components,
                    create=False,
                )
                if (
                    current_parent == parent
                    and current_snapshot == parent_snapshot
                ):
                    _verify_windows_temp(temporary, temporary_identity)
                    os.unlink(temporary)
            except (OSError, RuntimeError, ValueError):
                pass


def atomic_write_text(
    path: os.PathLike[str] | str,
    content: str,
    *,
    root_dir: os.PathLike[str] | str | None = None,
) -> None:
    """Atomically replace UTF-8 text without traversing mutable symlinks.

    Production callers pass the workspace root. The optional default keeps
    direct helper callers compatible by treating the existing parent as the
    root, but it does not establish a broader containment boundary.
    """

    target = Path(os.path.abspath(os.fspath(path)))
    root = Path(
        os.path.abspath(
            os.fspath(root_dir) if root_dir is not None else target.parent
        )
    )
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Write target '{target}' is outside workspace root '{root}'"
        ) from exc
    if not relative.parts:
        raise IsADirectoryError(f"Cannot replace workspace root: {target}")

    if _anchored_write_supported():
        _atomic_write_text_posix(root, relative, content)
    else:
        _atomic_write_text_windows(root, relative, content)


def _validate_virtual_path(path: str) -> str:
    normalized_input = str(path).replace("\\", "/")
    parts = PurePosixPath(normalized_input).parts
    if ".." in parts or normalized_input.startswith("~"):
        raise ValueError(f"Path traversal not allowed: {path}")
    if re.match(r"^[a-zA-Z]:", normalized_input):
        raise ValueError(f"Windows absolute paths are not supported: {path}")
    normalized = posixpath.normpath(normalized_input)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if ".." in normalized.split("/"):
        raise ValueError(f"Path traversal detected after normalization: {path}")
    return normalized


def normalize_virtual_path(path: str) -> str:
    """Normalize user paths to canonical workspace-relative virtual paths."""
    from services.plugin import NodeUserError

    if not path:
        return path
    windows_path = PureWindowsPath(path)
    if windows_path.drive or windows_path.root:
        relative = (
            "/" + "/".join(windows_path.parts[1:])
            if len(windows_path.parts) > 1
            else "/"
        )
    else:
        relative = path.replace("\\", "/")
    try:
        return _validate_virtual_path(relative)
    except ValueError as exc:
        raise NodeUserError(
            f"{exc}. The filesystem is sandboxed to the per-workflow workspace "
            "root — use a path relative to the workspace (e.g. "
            "'reports/data.csv'); '..' and '~' segments are rejected. To see "
            "what exists, use fs_search with mode='ls'."
        ) from exc


def perform_string_replacement(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> tuple[str, int] | str:
    """Perform exact replacement with ambiguity and EOF-newline diagnostics."""
    occurrences = content.count(old_string)
    if occurrences == 0:
        if (
            old_string.endswith("\n")
            and len(old_string) > 1
            and content.endswith(old_string.removesuffix("\n"))
        ):
            stripped = old_string.removesuffix("\n")
            stripped_count = content.count(stripped)
            if stripped_count == 1:
                return (
                    "Error: old_string ends with a newline, but the file does "
                    "not end with a newline. Retry with the trailing newline "
                    "removed from old_string (and from new_string if it also "
                    "ends with a newline)."
                )
            return (
                "Error: old_string ends with a newline, but the file does "
                "not end with a newline. With the trailing newline removed, "
                f"old_string would appear {stripped_count} times in the file. "
                "Retry with the trailing newline removed and add surrounding "
                "context so the match is unique."
            )
        return f"Error: String not found in file: '{old_string}'"
    if occurrences > 1 and not replace_all:
        return (
            f"Error: String '{old_string}' appears {occurrences} times in file. "
            "Use replace_all=True to replace all instances, or provide a more "
            "specific string with surrounding context."
        )
    return content.replace(old_string, new_string), occurrences


def _find_nu() -> Optional[str]:
    if not hasattr(_find_nu, "_cached"):
        _find_nu._cached = shutil.which("nu")
    return _find_nu._cached


class WorkspaceBackend:
    """Workspace-rooted filesystem operations plus bounded shell execution."""

    def __init__(
        self,
        root_dir: os.PathLike[str] | str,
        *,
        timeout: int = 120,
        max_output_bytes: int = 100_000,
        env: Optional[Dict[str, str]] = None,
        inherit_env: bool = False,
        max_file_size_mb: int = 10,
        **_: Any,
    ) -> None:
        if timeout <= 0:
            raise ValueError(f"timeout must be positive, got {timeout}")
        self.cwd = Path(root_dir).resolve()
        self.cwd.mkdir(parents=True, exist_ok=True)
        self._default_timeout = timeout
        self._max_output_bytes = max_output_bytes
        self._max_file_size_bytes = max_file_size_mb * 1024 * 1024
        self._env = os.environ.copy() if inherit_env else {}
        if env:
            self._env.update(env)
        self._sandbox_id = f"workspace-{uuid4().hex[:8]}"

    @property
    def id(self) -> str:
        return self._sandbox_id

    def _resolve_path(self, key: str) -> Path:
        virtual = _validate_virtual_path(key)
        candidate = self.cwd / virtual.lstrip("/")
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(self.cwd)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError(
                f"Path '{key}' resolves outside workspace root"
            ) from exc
        return resolved

    def _to_virtual_path(self, path: Path) -> str:
        resolved = path.resolve(strict=False)
        relative = resolved.relative_to(self.cwd)
        return "/" + relative.as_posix()

    def _safe_child(self, path: Path) -> Optional[Path]:
        try:
            resolved = path.resolve(strict=False)
            resolved.relative_to(self.cwd)
            return resolved
        except (OSError, RuntimeError, ValueError):
            return None

    def _open_read_fd(self, path: Path) -> int:
        """Open a contained regular file without following mutable symlinks.

        POSIX walks from an already-open workspace directory descriptor and
        applies ``O_NOFOLLOW`` to every component. Windows lacks ``dir_fd``
        traversal, so its fallback verifies the opened file identity against
        a no-follow stat and re-checks containment after opening.
        """
        try:
            relative = path.relative_to(self.cwd)
        except ValueError as exc:
            raise ValueError(
                f"Path '{path}' is outside workspace root"
            ) from exc
        if not relative.parts:
            raise IsADirectoryError(f"Cannot read workspace directory: {path}")

        supports_anchored_open = (
            os.name != "nt"
            and hasattr(os, "O_DIRECTORY")
            and os.open in os.supports_dir_fd
        )
        no_follow = getattr(os, "O_NOFOLLOW", 0)

        if supports_anchored_open:
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | no_follow
            directory_fd = os.open(self.cwd, directory_flags)
            try:
                for component in relative.parts[:-1]:
                    next_fd = os.open(
                        component,
                        directory_flags,
                        dir_fd=directory_fd,
                    )
                    os.close(directory_fd)
                    directory_fd = next_fd
                file_fd = os.open(
                    relative.parts[-1],
                    os.O_RDONLY | no_follow,
                    dir_fd=directory_fd,
                )
            finally:
                os.close(directory_fd)
        else:
            file_fd = os.open(path, os.O_RDONLY | no_follow)
            try:
                opened = os.fstat(file_fd)
                current = os.stat(path, follow_symlinks=False)
                if stat.S_ISLNK(current.st_mode) or (
                    opened.st_dev,
                    opened.st_ino,
                ) != (current.st_dev, current.st_ino):
                    raise ValueError(
                        f"Path '{path}' changed while it was being opened"
                    )
                path.resolve(strict=True).relative_to(self.cwd)
            except BaseException:
                os.close(file_fd)
                raise

        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise IsADirectoryError(f"Path is not a regular file: {path}")
        except BaseException:
            os.close(file_fd)
            raise
        return file_fd

    def read_text_secure(self, path: os.PathLike[str] | str) -> str:
        """Read UTF-8 text from a pre-resolved contained path safely."""
        fd = self._open_read_fd(Path(path))
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            return handle.read()

    def atomic_write_text(
        self,
        path: os.PathLike[str] | str,
        content: str,
    ) -> None:
        """Atomically write a pre-resolved path within this workspace."""
        atomic_write_text(path, content, root_dir=self.cwd)

    @staticmethod
    def _file_info(path: Path, virtual_path: str, *, is_dir: bool) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "path": virtual_path + ("/" if is_dir else ""),
            "is_dir": is_dir,
        }
        try:
            status = path.stat()
            info["size"] = 0 if is_dir else int(status.st_size)
            info["modified_at"] = datetime.fromtimestamp(
                status.st_mtime
            ).isoformat()
        except OSError:
            pass
        return info

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            resolved = self._resolve_path(file_path)
            if not resolved.exists() or not resolved.is_file():
                return ReadResult(error=f"File '{file_path}' not found")
            fd = self._open_read_fd(resolved)
            with os.fdopen(fd, "rb") as handle:
                raw = handle.read()
            try:
                content = raw.decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                content = base64.standard_b64encode(raw).decode("ascii")
                encoding = "base64"
            if encoding == "utf-8":
                if not content or not content.strip():
                    content = EMPTY_CONTENT_WARNING
                else:
                    content = content.replace("\r\n", "\n").replace("\r", "\n")
                    lines = content.splitlines(keepends=True)
                    if offset >= len(lines):
                        return ReadResult(
                            error=(
                                f"Line offset {offset} exceeds file length "
                                f"({len(lines)} lines)"
                            )
                        )
                    content = "".join(lines[offset : offset + limit])
            return ReadResult(
                file_data={"content": content, "encoding": encoding}
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return ReadResult(error=f"Error reading file '{file_path}': {exc}")

    def ls_info(self, path: str) -> list[Dict[str, Any]]:
        directory = self._resolve_path(path)
        if not directory.exists() or not directory.is_dir():
            return []
        entries: list[Dict[str, Any]] = []
        for child in directory.iterdir():
            safe = self._safe_child(child)
            if safe is None:
                continue
            try:
                is_dir = safe.is_dir()
                is_file = safe.is_file()
            except OSError:
                continue
            if not is_dir and not is_file:
                continue
            entries.append(
                self._file_info(
                    safe,
                    self._to_virtual_path(safe),
                    is_dir=is_dir,
                )
            )
        entries.sort(key=lambda item: item["path"])
        return entries

    def glob_info(
        self,
        pattern: str,
        path: str = "/",
    ) -> list[Dict[str, Any]]:
        pattern_parts = PurePosixPath(pattern.replace("\\", "/")).parts
        if ".." in pattern_parts or pattern.startswith("~"):
            raise ValueError("Path traversal not allowed in glob pattern")
        directory = self._resolve_path(path)
        if not directory.exists() or not directory.is_dir():
            return []
        matches: list[Dict[str, Any]] = []
        for candidate in directory.rglob(pattern.lstrip("/")):
            safe = self._safe_child(candidate)
            if safe is None:
                continue
            try:
                if not safe.is_file():
                    continue
            except OSError:
                continue
            matches.append(
                self._file_info(
                    safe,
                    self._to_virtual_path(safe),
                    is_dir=False,
                )
            )
        matches.sort(key=lambda item: item["path"])
        return matches

    def grep_raw(
        self,
        pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None,
    ) -> list[Dict[str, Any]] | str:
        try:
            base = self._resolve_path(path or ".")
        except (OSError, RuntimeError, ValueError) as exc:
            return f"Error searching path '{path or '.'}': {exc}"
        if not base.exists():
            return []
        candidates = [base] if base.is_file() else base.rglob("*")
        matches: list[Dict[str, Any]] = []
        for candidate in candidates:
            safe = self._safe_child(candidate)
            if safe is None:
                continue
            try:
                if not safe.is_file() or safe.stat().st_size > self._max_file_size_bytes:
                    continue
                relative = safe.relative_to(base if base.is_dir() else base.parent)
                if glob and not (
                    fnmatch.fnmatch(relative.as_posix(), glob)
                    or fnmatch.fnmatch(safe.name, glob)
                ):
                    continue
                content = self.read_text_secure(safe)
            except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
                continue
            virtual = self._to_virtual_path(safe)
            for line_number, line in enumerate(content.splitlines(), 1):
                if pattern in line:
                    matches.append(
                        {
                            "path": virtual,
                            "line": line_number,
                            "text": line,
                        }
                    )
        return matches

    def _shape_process_result(
        self,
        *,
        stdout: str,
        stderr: str,
        returncode: int,
    ) -> ExecuteResponse:
        parts = [stdout] if stdout else []
        if stderr:
            parts.extend(
                f"[stderr] {line}" for line in stderr.strip().split("\n")
            )
        output = "\n".join(parts) if parts else "<no output>"
        truncated = False
        if len(output) > self._max_output_bytes:
            output = output[: self._max_output_bytes]
            output += (
                f"\n\n... Output truncated at {self._max_output_bytes} bytes."
            )
            truncated = True
        if returncode != 0:
            output = f"{output.rstrip()}\n\nExit code: {returncode}"
        return ExecuteResponse(
            output=output,
            exit_code=returncode,
            truncated=truncated,
        )

    def execute(
        self,
        command: str,
        *,
        timeout: Optional[int] = None,
    ) -> ExecuteResponse:
        if not command or not isinstance(command, str):
            return ExecuteResponse(
                output="Error: Command must be a non-empty string.",
                exit_code=1,
            )
        effective_timeout = (
            timeout if timeout is not None else self._default_timeout
        )
        if effective_timeout <= 0:
            raise ValueError(
                f"timeout must be positive, got {effective_timeout}"
            )
        nu = _find_nu()
        argv: str | list[str]
        shell = nu is None
        if nu is None:
            argv = command
        else:
            argv = [nu, "-n", "--no-history", "-c", command]
        try:
            result = subprocess.run(
                argv,
                check=False,
                shell=shell,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                timeout=effective_timeout,
                env=self._env,
                cwd=str(self.cwd),
            )
            return self._shape_process_result(
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                returncode=result.returncode,
            )
        except subprocess.TimeoutExpired:
            suffix = (
                " (custom timeout)" if timeout is not None else ""
            )
            return ExecuteResponse(
                output=(
                    f"Error: Command timed out after {effective_timeout} "
                    f"seconds{suffix}. The command may be stuck or require "
                    "more time."
                ),
                exit_code=124,
            )
        except Exception as exc:  # noqa: BLE001
            runner = "nu" if nu else "the host shell"
            return ExecuteResponse(
                output=(
                    f"Error executing command via {runner} "
                    f"({type(exc).__name__}): {exc}"
                ),
                exit_code=1,
            )


# Compatibility name retained for integrations that imported the old class.
NushellBackend = WorkspaceBackend


def get_backend(
    parameters: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> WorkspaceBackend:
    """Return a native backend rooted at the per-workflow workspace."""
    from core.config import Settings
    from core.logging import get_logger

    parameter_directory = parameters.get("working_directory")
    context_directory = context.get("workspace_dir") if context else None
    root = (
        parameter_directory
        or context_directory
        or os.path.join(Settings().workspace_base_resolved, "default")
    )
    os.makedirs(root, exist_ok=True)
    get_logger(__name__).info("[Filesystem] root=%s", root)
    return WorkspaceBackend(root_dir=root, inherit_env=True)
