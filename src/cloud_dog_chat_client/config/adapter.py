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

import argparse
import copy
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from cloud_dog_config import export_config, load_config  # type: ignore[import-untyped]
from cloud_dog_config.coercion import coerce  # type: ignore[import-untyped]
from cloud_dog_config.errors import ConfigImmutableError  # type: ignore[import-untyped]
from cloud_dog_config.naming import env_to_path  # type: ignore[import-untyped]
from cloud_dog_logging import get_logger  # type: ignore[import-untyped]

from ..storage_fs import repo_root_from_file, resolve_path


class ConfigManager:
    """Thin project adapter over cloud_dog_config.

    This keeps legacy call-shape (`get`, `get_all`, `env_file`, `project_root`) while
    delegating all loading, merge, and compile behaviour to the platform package.
    """
    # Covers: R2, R8, NFR5

    def __init__(
        self,
        config_file: Optional[str] = None,
        env_file: Optional[str] = None,
        project_root: Optional[Path] = None,
        *,
        env_files: Optional[Iterable[str]] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ):
        """Load immutable configuration from defaults, config, env files, and overrides."""
        self.logger = get_logger(__name__)
        self.project_root = (
            project_root.resolve() if project_root else self._detect_project_root()
        )
        self.config_file = str(config_file) if config_file else None
        self.env_files = [str(p) for p in (env_files or []) if str(p).strip()]
        if env_file and env_file not in self.env_files:
            self.env_files.append(env_file)
        self._overrides = dict(overrides or {})

        self.env_file: Optional[str] = None
        self._config_dict: Dict[str, Any] = {}
        self._load_config()

    @staticmethod
    def _detect_project_root() -> Path:
        """Resolve the project root by probing the working directory first."""
        cwd = Path.cwd().resolve()
        if (cwd / "defaults.yaml").exists():
            return cwd
        return cwd.__class__(repo_root_from_file(__file__, levels=4))

    def _resolve_env_path(self, env_path: str) -> Path:
        """Resolve an env-file path relative to the project root."""
        return self.project_root.__class__(
            resolve_path(env_path, base_dir=str(self.project_root))
        )

    def _resolved_paths(self, env_files: Iterable[str]) -> list[str]:
        """Return only env-file paths that exist on disk."""
        out: list[str] = []
        for item in env_files:
            p = self._resolve_env_path(str(item))
            if p.exists():
                out.append(str(p))
            else:
                self.logger.warning(f"Environment file {p} not found")
        return out

    def _apply_overrides_transform(self, tree: dict[str, Any]) -> dict[str, Any]:
        """Apply dotted-path overrides onto a copied config tree."""
        if not self._overrides:
            return tree
        out = copy.deepcopy(tree)
        for path, value in self._overrides.items():
            self._set_path(out, str(path), value)
        return out

    @staticmethod
    def _normalise_indexed_collections(value: Any) -> Any:
        """Convert contiguous integer-key dictionaries into list values."""
        if isinstance(value, list):
            return [ConfigManager._normalise_indexed_collections(v) for v in value]
        if isinstance(value, dict):
            normalised = {
                str(k): ConfigManager._normalise_indexed_collections(v)
                for k, v in value.items()
            }
            keys = list(normalised.keys())
            if keys and all(k.isdigit() for k in keys):
                idxs = sorted(int(k) for k in keys)
                if idxs == list(range(len(idxs))):
                    return [normalised[str(i)] for i in idxs]
            return normalised
        return value

    def _load_once(self, env_files: list[str]) -> None:
        """Load one merged config snapshot with the supplied env files."""
        defaults_yaml = str((self.project_root / "defaults.yaml").resolve())
        config_yaml = str((self.project_root / "config.yaml").resolve())
        if self.config_file:
            config_yaml = resolve_path(self.config_file, base_dir=str(self.project_root))

        transforms = [self._apply_overrides_transform] if self._overrides else None
        global_cfg = load_config(
            env_files=env_files,
            config_yaml=config_yaml,
            defaults_yaml=defaults_yaml,
            transforms=transforms,
        )
        exported = export_config(global_cfg, redact=False)

        self._config_dict = self._normalise_indexed_collections(exported)
        self.env_files = list(env_files)
        self.env_file = self.env_files[-1] if self.env_files else None

    def _load_config(self) -> None:
        """Load config, then optionally re-load using discovered `app.env_file`."""
        explicit_env_files = self._resolved_paths(self.env_files)
        self._load_once(explicit_env_files)

        if explicit_env_files:
            return

        discovered_env = str(self.get("app.env_file") or "").strip()
        if not discovered_env:
            return

        discovered_path = self._resolve_env_path(discovered_env)
        if not discovered_path.exists():
            self.logger.warning(f"Configured app.env_file not found: {discovered_path}")
            return

        self._load_once([str(discovered_path)])

    @staticmethod
    def _set_path(tree: dict[str, Any], dotted_path: str, value: Any) -> None:
        """Set a nested value in `tree` using dotted and numeric path segments."""
        parts = [p for p in dotted_path.split(".") if p]
        if not parts:
            raise ValueError("path must be non-empty")

        current: Any = tree
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            next_part = parts[i + 1] if not is_last else None
            next_is_index = bool(next_part and next_part.isdigit())

            if part.isdigit():
                idx = int(part)
                if not isinstance(current, list):
                    raise ValueError(f"Path segment '{part}' requires a list container")
                while len(current) <= idx:
                    current.append([] if next_is_index else {})
                if is_last:
                    current[idx] = value
                    return
                if next_is_index and not isinstance(current[idx], list):
                    current[idx] = []
                elif not next_is_index and not isinstance(current[idx], dict):
                    current[idx] = {}
                current = current[idx]
                continue

            if not isinstance(current, dict):
                raise ValueError(
                    f"Path segment '{part}' requires a dictionary container"
                )
            if is_last:
                current[part] = value
                return
            if part not in current:
                current[part] = [] if next_is_index else {}
            elif next_is_index and not isinstance(current[part], list):
                current[part] = []
            elif not next_is_index and not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]

    def get(self, path: str, default: Any = None) -> Any:
        """Return a config value by dotted path, falling back to `default`."""
        try:
            value: Any = self._config_dict
            for part in path.split("."):
                if isinstance(value, dict):
                    value = value[part]
                elif isinstance(value, list) and part.isdigit():
                    value = value[int(part)]
                else:
                    raise KeyError(part)
            # Env layering can surface boolean-like values as strings when
            # keys are not typed in defaults/config. Normalise strict literals
            # so callers do not accidentally treat "false" as truthy.
            if isinstance(value, str):
                lower = value.strip().lower()
                if lower == "true":
                    return True
                if lower == "false":
                    return False
            return value
        except (KeyError, IndexError, TypeError, ValueError):
            return default

    def get_all(self) -> Dict[str, Any]:
        """Return a deep copy of the compiled config tree."""
        return copy.deepcopy(self._config_dict)

    def set(self, path: str, value: Any) -> None:
        """Reject runtime mutation to enforce immutable config semantics."""
        raise ConfigImmutableError(
            f"Config is immutable; attempted set('{path}'). Use env/config file updates and reload."
        )


_config_instance: Optional[ConfigManager] = None


def get_config(
    config_file: Optional[str] = None,
    env_file: Optional[str] = None,
    reload: bool = False,
    project_root: Optional[Path] = None,
    *,
    env_files: Optional[Iterable[str]] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> ConfigManager:
    """Return the shared `ConfigManager` instance."""
    global _config_instance
    if _config_instance is None or reload:
        _config_instance = ConfigManager(
            config_file=config_file,
            env_file=env_file,
            project_root=project_root,
            env_files=env_files,
            overrides=overrides,
        )
    return _config_instance


def override_to_path(key: str) -> str:
    """Translate an env-style override key into dotted config path notation."""
    path = env_to_path(key, prefix="CLOUD_DOG")
    if path:
        return path
    path = env_to_path(key)
    if path:
        return path
    return key.lower() if "." not in key else key


def parse_overrides(overrides: Iterable[str]) -> Dict[str, Any]:
    """Parse `--set KEY=VALUE` CLI items into typed override values."""
    out: Dict[str, Any] = {}
    for item in overrides:
        if "=" not in item:
            raise ValueError(f"--set must be KEY=VALUE, got: {item}")
        key, raw_value = item.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            raise ValueError(f"--set must be KEY=VALUE, got: {item}")
        out[override_to_path(key)] = coerce(raw_value)
    return out


def parse_command_line_args() -> argparse.Namespace:
    """Parse basic config and env CLI arguments."""
    parser = argparse.ArgumentParser(description="Cloud-Dog Chat Client")
    parser.add_argument(
        "--config", "-c", type=str, help="Path to configuration YAML file"
    )
    parser.add_argument("--env", "-e", type=str, help="Path to environment file")
    return parser.parse_args()
