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

"""Cloud-Dog chat client package.

Requirements: R1, R2, R3, R4, R5, R5.1, R6, R7, R7.1, R8, R9, R10, R11, R12,
R13, R14, R15, R16, R16.1, R16.2, R16.3, R16.4, R16.5, R16.6, R16.7, R16.8,
R-DB-01, R-DB-02, R-DB-03, R-DB-04, R-DB-05, R-DB-06, R-DB-07, R-DB-08,
R-DB-09, R-DB-10.

Traceability aliases: CS-001, CS-002, CS-003, CS-004, CS-005, CS-006, CS-007,
CS-008, CS-009, CS-010, CS-011, CS-012, CS-013, FR-001, FR-002, FR-003,
FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012,
FR-013, FR-014, FR-015, FR-016, FR-017, FR-018, NF-001, NF-002, NF-003,
NF-004, NF-005, NF-006, NF-007, NF-008, R-7, R-16, R7.3.
"""

from importlib.metadata import PackageNotFoundError, version as _dist_version

from ._runtime import enforce_runtime

enforce_runtime()

# CC8 (W28C-1703): single source of truth for the chat-client version. Every
# version-bearing surface (/version, /api/version, /api/status, /health and the
# SPA runtime-config.js APP_VERSION) MUST resolve to this value so the four
# endpoints never drift again. The string is the installed distribution version
# (driven by pyproject `version`); a source checkout without installed dist
# metadata falls back to a clearly-marked sentinel rather than a stale literal.
try:
    __version__ = _dist_version("cloud-dog-chat-client")
except PackageNotFoundError:  # pragma: no cover - source checkout without dist
    __version__ = "0.0.0+source"

__all__ = ["__version__", "enforce_runtime"]
