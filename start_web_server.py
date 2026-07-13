#!/usr/bin/env python3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cloud_dog_chat_client.servers.web_server import main

# W28A-654: Patch cloud_dog_logging ContextVar defaults at module import time.
# ContextVars are task-scoped in asyncio — set_environment() in one task does NOT
# propagate to AuditMiddleware in another. Patching defaults ensures all tasks inherit.
try:
    import contextvars as _ctxvars, os as _patch_os
    from cloud_dog_logging import correlation as _cmod
    _cmod._environment_var = _ctxvars.ContextVar(
        "environment", default=_patch_os.environ.get("CLOUD_DOG_ENVIRONMENT", "dev"))
    _cmod._service_name_var = _ctxvars.ContextVar(
        "service_name", default="chat-client-mcp-server")
    _cmod._service_instance_var = _ctxvars.ContextVar(
        "service_instance", default=_patch_os.environ.get("HOSTNAME", "chat-client-local"))
    del _ctxvars, _patch_os, _cmod
except Exception:
    pass  # cloud_dog_logging not installed or incompatible version



if __name__ == "__main__":
    main()
