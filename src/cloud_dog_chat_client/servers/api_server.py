# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

from __future__ import annotations

from ..api.server import create_app
from .common import bind_host, bind_port, configure_logging, load_config, run_uvicorn


def main() -> None:
    cfg = load_config()
    configure_logging(
        cfg,
        section="api_server",
        default_log_name="api_server.log",
        app_name="cloud_dog_chat_api",
    )
    host = bind_host(cfg, "api_server")
    port = bind_port(cfg, "api_server")
    log_level = str(cfg.get("log.level") or "INFO")
    run_uvicorn(create_app(cfg), host=host, port=port, log_level=log_level)
