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

import asyncio
from pathlib import Path
import sys
from typing import Optional

import typer

from ..config import ConfigManager, parse_overrides
from ..session import SessionManager
from ..session.transcript import TranscriptEvent
from ..storage_fs import join_path, repo_root_from_file
from ..utils import setup_logging
from .interactive import run_chat_loop


app = typer.Typer(add_completion=False)


def _resolve_env_files(env: list[str], *, config: Optional[str] = None) -> list[str]:
    """Internal helper to env files for this module."""
    if env:
        return env

    try:
        probe = ConfigManager(config_file=str(config) if config else None, env_files=[])
    except Exception:
        probe = None
    if probe is not None and probe.env_file:
        return [probe.env_file]

    default_env = join_path(repo_root_from_file(__file__, levels=4), "private", "env-local")
    if Path.cwd().__class__(default_env).exists():
        return [default_env]
    return []


def _parse_set_overrides(overrides: list[str]) -> dict[str, object]:
    """Internal helper to set overrides for this module."""
    try:
        return parse_overrides(overrides)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _build_cfg(
    env_files: list[str],
    *,
    config: Optional[str] = None,
    overrides: Optional[dict[str, object]] = None,
) -> ConfigManager:
    """Internal helper to configuration for this module."""
    return ConfigManager(
        config_file=str(config) if config else None,
        env_files=env_files,
        overrides=overrides or {},
    )


@app.command()
def chat(
    env: list[str] = typer.Option(
        None,
        "--env",
        help="Environment file path (can be specified multiple times)",
    ),
    config: Optional[str] = typer.Option(
        None, "--config", help="Optional config.yaml override"
    ),
    no_rich: bool = typer.Option(False, "--no-rich", help="Disable Rich UI"),
    stream: bool = typer.Option(
        False, "--stream", help="Override: force streaming mode"
    ),
    no_stream: bool = typer.Option(
        False, "--no-stream", help="Override: force non-streaming mode"
    ),
    context_file: Optional[str] = typer.Option(
        None,
        "--context-file",
        "--load-context",
        help="Load a context markdown/text file at startup",
    ),
    save_context: Optional[str] = typer.Option(
        None,
        "--save-context",
        help="Write a context snapshot on exit",
    ),
    session_id: Optional[str] = typer.Option(
        None,
        "--session-id",
        help="Resume an existing session id from the log folder",
    ),
    print_session_id: bool = typer.Option(
        False, "--print-session-id", help="Print the session id on startup"
    ),
    set_vars: list[str] = typer.Option(
        None,
        "--set",
        help="Override config values (KEY=VALUE). Can be provided multiple times.",
    ),
) -> None:
    """Handle chat for the current runtime context."""
    env_files = _resolve_env_files(env, config=config)
    overrides = _parse_set_overrides(set_vars or [])

    cfg = _build_cfg(env_files, config=config, overrides=overrides)

    stream_override: Optional[bool] = None
    if stream and no_stream:
        raise typer.BadParameter("Only one of --stream or --no-stream may be provided")
    if stream:
        stream_override = True
    elif no_stream:
        stream_override = False

    log_folder = str(cfg.get("app.logfolder"))
    log_level = str(cfg.get("log.level"))
    setup_logging(
        log_level=log_level,
        log_file=join_path(log_folder, "client.log"),
        log_console=False,
        app_name="cloud_dog_chat",
        service_instance=str(cfg.get("app.server_id") or "") or None,
        environment=str(cfg.get("log.environment", "dev") or "dev"),
    )

    session_manager = SessionManager(log_folder)
    if session_id:
        session_id = session_manager.load_session(session_id)
        session_manager.append_event(
            session_id,
            TranscriptEvent(
                event_type="client_resumed",
                data={"env": env_files or None, "no_rich": no_rich},
            ),
        )
    else:
        session_id = session_manager.create_session(
            metadata={"env": env_files or None, "no_rich": no_rich}
        )
    session_manager.append_event(
        session_id,
        TranscriptEvent(event_type="client_started", data={"argv": sys.argv}),
    )
    if print_session_id:
        sys.stdout.write(f"[session-id] {session_id}\n")

    if context_file:
        session_manager.load_context_file(session_id, str(context_file))

    try:
        if no_rich:

            def _write_out(s: str) -> None:
                """Internal helper to out for this module."""
                sys.stdout.write(s)

            def _flush() -> None:
                """Internal helper to flush for this module."""
                sys.stdout.flush()

            def _read_in() -> str:
                """Internal helper to in for this module."""
                return input("> ")

            asyncio.run(
                run_chat_loop(
                    config=cfg,
                    session_manager=session_manager,
                    session_id=session_id,
                    stream_override=stream_override,
                    write_out=_write_out,
                    write_out_flush=_flush,
                    read_in=_read_in,
                )
            )
            return

        try:
            from rich.console import Console
            from rich.prompt import Prompt
        except Exception:
            raise typer.BadParameter("Rich is not available; use --no-rich")

        console = Console()

        def _write_out(s: str) -> None:
            """Internal helper to out for this module."""
            console.out(s, end="")

        def _flush() -> None:
            """Internal helper to flush for this module."""
            return

        def _read_in() -> str:
            """Internal helper to in for this module."""
            return Prompt.ask(">")

        asyncio.run(
            run_chat_loop(
                config=cfg,
                session_manager=session_manager,
                session_id=session_id,
                stream_override=stream_override,
                write_out=_write_out,
                write_out_flush=_flush,
                read_in=_read_in,
            )
        )
    finally:
        if save_context:
            session_manager.write_context_snapshot(session_id, str(save_context))


def _run_api(
    *,
    env_files: list[str],
    log_name: str,
    config: Optional[str] = None,
    set_vars: Optional[list[str]] = None,
) -> None:
    """Internal helper to run API for this module."""
    overrides = _parse_set_overrides(set_vars or [])
    cfg = _build_cfg(env_files, config=config, overrides=overrides)

    log_folder = str(cfg.get("app.logfolder"))
    log_level = str(cfg.get("log.level"))
    setup_logging(
        log_level=log_level,
        log_file=join_path(log_folder, log_name),
        log_console=True,
        app_name="cloud_dog_chat_api",
        service_instance=str(cfg.get("app.server_id") or "") or None,
        environment=str(cfg.get("log.environment", "dev") or "dev"),
    )

    host = str(cfg.get("client_api.host") or cfg.get("api_server.host") or "0.0.0.0")
    port = int(cfg.get("client_api.port") or cfg.get("api_server.port") or 0)

    import uvicorn

    from ..api.server import create_app

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(cfg),
            host=host,
            port=port,
            log_level=str(log_level).lower(),
        )
    )
    server.run()


@app.command()
def api(
    env: list[str] = typer.Option(
        None,
        "--env",
        help="Environment file path (can be specified multiple times)",
    ),
    config: Optional[str] = typer.Option(
        None, "--config", help="Optional config.yaml override"
    ),
    set_vars: list[str] = typer.Option(
        None,
        "--set",
        help="Override config values (KEY=VALUE). Can be provided multiple times.",
    ),
) -> None:
    """Handle API for the current runtime context."""
    env_files = _resolve_env_files(env, config=config)
    _run_api(
        env_files=env_files, log_name="client_api.log", config=config, set_vars=set_vars
    )


@app.command("server")
def server_only(
    env: list[str] = typer.Option(
        None,
        "--env",
        help="Environment file path (can be specified multiple times)",
    ),
    config: Optional[str] = typer.Option(
        None, "--config", help="Optional config.yaml override"
    ),
    set_vars: list[str] = typer.Option(
        None,
        "--set",
        help="Override config values (KEY=VALUE). Can be provided multiple times.",
    ),
) -> None:
    """Handle server only for the current runtime context."""
    env_files = _resolve_env_files(env, config=config)
    _run_api(
        env_files=env_files, log_name="client_api.log", config=config, set_vars=set_vars
    )


@app.command("test-server")
def test_server(
    env: list[str] = typer.Option(
        None,
        "--env",
        help="Environment file path (can be specified multiple times)",
    ),
    config: Optional[str] = typer.Option(
        None, "--config", help="Optional config.yaml override"
    ),
    set_vars: list[str] = typer.Option(
        None,
        "--set",
        help="Override config values (KEY=VALUE). Can be provided multiple times.",
    ),
) -> None:
    """Handle test server for the current runtime context."""
    env_files = _resolve_env_files(env, config=config)
    _run_api(
        env_files=env_files,
        log_name="client_api_test.log",
        config=config,
        set_vars=set_vars,
    )


def main() -> None:
    """Handle main for the current runtime context."""
    app()
