# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import re
from os import getcwd, statvfs
from posixpath import abspath, basename, dirname, isabs, join, normpath, splitext
from typing import Iterable

from cloud_dog_storage.backends.local import LocalStorage
from cloud_dog_storage.models import StorageEntry


_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def is_absolute_path(path: str) -> bool:
    """Return whether the supplied path is absolute on POSIX or Windows."""
    raw = str(path or "").strip()
    if not raw:
        return False
    return raw.startswith(("/", "\\")) or bool(_WINDOWS_ABSOLUTE_RE.match(raw))


def resolve_path(path: str, *, base_dir: str = "") -> str:
    """Resolve a possibly-relative path against the provided base directory."""
    raw = str(path or "").strip()
    if not raw:
        return ""
    if is_absolute_path(raw):
        return normpath(raw)
    base = str(base_dir or "").strip() or abspath(getcwd())
    return normpath(join(base, raw))


def repo_root_from_file(file_path: str, *, levels: int) -> str:
    """Walk up `levels` parents from a file path and return the resolved directory."""
    current = resolve_path(file_path)
    for _ in range(max(0, int(levels))):
        current = dirname(current)
    return current


def join_path(*parts: str) -> str:
    """Join path fragments while tolerating empty values."""
    if not parts:
        return ""
    current = str(parts[0] or "")
    for part in parts[1:]:
        current = join(current, str(part or ""))
    return normpath(current)


def parent_dir(path: str) -> str:
    """Return the resolved parent directory for a path."""
    resolved = resolve_path(path)
    return dirname(resolved) or "/"


def file_name(path: str) -> str:
    """Return the final path component without trailing slash noise."""
    return basename(str(path or "").rstrip("/"))


def file_stem(path: str) -> str:
    """Return a filename without its extension."""
    name = file_name(path)
    stem, suffix = splitext(name)
    return stem if suffix else name


def storage_for_root(root_path: str) -> LocalStorage:
    """Create a local storage backend rooted at the supplied directory."""
    return LocalStorage(root_path=str(root_path or "."))


def storage_for_file(path: str) -> tuple[LocalStorage, str]:
    """Return the storage backend and key pair for a concrete file path."""
    resolved = resolve_path(path)
    return storage_for_root(parent_dir(resolved)), "/" + file_name(resolved)


def ensure_directory(path: str) -> str:
    """Initialise a local storage root and return the resolved directory path."""
    resolved = resolve_path(path)
    storage_for_root(resolved)
    return resolved


def read_bytes(path: str) -> bytes:
    """Read raw bytes from the storage-backed file path."""
    storage, key = storage_for_file(path)
    return storage.read_bytes(key)


def read_text(path: str, *, encoding: str = "utf-8", errors: str = "strict") -> str:
    """Read text content from a storage-backed file path."""
    return read_bytes(path).decode(encoding, errors=errors)


def write_bytes(path: str, data: bytes, *, overwrite: bool = True) -> None:
    """Write raw bytes to a storage-backed file path."""
    storage, key = storage_for_file(path)
    storage.write_bytes(key, data, overwrite=overwrite)


def write_text(
    path: str,
    text: str,
    *,
    encoding: str = "utf-8",
    overwrite: bool = True,
) -> None:
    """Encode and write text content to a storage-backed file path."""
    write_bytes(path, str(text).encode(encoding), overwrite=overwrite)


def append_text(path: str, text: str, *, encoding: str = "utf-8") -> None:
    """Append text content by reading the current file and rewriting the result."""
    storage, key = storage_for_file(path)
    existing = storage.read_bytes(key) if storage.exists(key) else b""
    storage.write_bytes(key, existing + str(text).encode(encoding), overwrite=True)


def path_exists(path: str) -> bool:
    """Return whether the storage-backed file exists."""
    storage, key = storage_for_file(path)
    return storage.exists(key)


def delete_file(path: str, *, missing_ok: bool = False) -> None:
    """Delete a storage-backed file path."""
    storage, key = storage_for_file(path)
    storage.delete_path(key, missing_ok=missing_ok)


def list_dir(root_path: str, relative_path: str = "/") -> list[StorageEntry]:
    """List direct children beneath a storage root and relative path."""
    storage = storage_for_root(root_path)
    return storage.list_dir(relative_path or "/", recursive=False)


def list_matching_paths(
    root_path: str,
    relative_path: str,
    *,
    suffix: str = "",
) -> list[str]:
    """Return sorted file paths beneath a directory, optionally filtered by suffix."""
    matches: list[str] = []
    for entry in list_dir(root_path, relative_path):
        if entry.is_dir:
            continue
        if suffix and not str(entry.path).endswith(suffix):
            continue
        matches.append(str(entry.path))
    return sorted(matches)


def disk_usage_percent(path: str) -> float | None:
    """Return filesystem usage for the resolved path, if available."""
    resolved = resolve_path(path)
    try:
        stats = statvfs(resolved)
    except OSError:
        return None
    total = int(stats.f_blocks) * int(stats.f_frsize)
    available = int(stats.f_bavail) * int(stats.f_frsize)
    if total <= 0:
        return None
    used = total - max(0, available)
    return round((used / total) * 100.0, 2)


def first_existing_path(candidates: Iterable[str]) -> str | None:
    """Return the first candidate path that currently exists."""
    for candidate in candidates:
        resolved = resolve_path(candidate)
        if path_exists(resolved):
            return resolved
    return None
