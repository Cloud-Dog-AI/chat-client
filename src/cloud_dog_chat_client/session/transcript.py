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

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def utc_now_iso() -> str:
    """Handle utc now iso for the current runtime context."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TranscriptEvent:
    event_type: str
    timestamp: str = field(default_factory=utc_now_iso)
    data: Dict[str, Any] = field(default_factory=dict)
    sequence: Optional[int] = None

    def to_json_line(self) -> str:
        """Handle to json line for the current runtime context."""
        return json.dumps(asdict(self), ensure_ascii=False)
