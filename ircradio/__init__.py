import os
from typing import List, Optional

import settings

with open(os.path.join(settings.cwd, 'data', 'agents.txt'), 'r') as f:
    user_agents: Optional[List[str]] = [
        l.strip() for l in f.readlines() if l.strip()]
