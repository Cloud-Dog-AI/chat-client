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

"""Agent strategy adoption boundary for Chat-Client.

Related requirements: W28B-317 rows 3-17.
Related tests: UT_AGENT_* and AT_AGENT_* suites.
"""

from .strategy import SUPPORTED_AGENT_STRATEGIES, agent_strategy_for_session

__all__ = ["SUPPORTED_AGENT_STRATEGIES", "agent_strategy_for_session"]

