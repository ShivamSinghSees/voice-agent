"""One-off idempotent script to update existing agents' system prompts with internal guidance instructions.

Checks for an existing marker before modifying to guarantee idempotency.
"""

import sys
from pathlib import Path

# Add api directory to sys.path so app imports work when run standalone
api_dir = Path(__file__).resolve().parent.parent
if str(api_dir) not in sys.path:
    sys.path.insert(0, str(api_dir))

from app import config, storage

MARKER = "INTERNAL GUIDANCE:"
GUIDANCE_ADDENDUM = (
    "\n\nINTERNAL GUIDANCE: You may occasionally receive private internal notes or guidance during the call. "
    "Seamlessly incorporate this guidance into your next response. NEVER mention receiving notes or guidance, "
    "never say 'I have been informed' or reference internal instructions, never quote the guidance verbatim, "
    "and never reveal that you are receiving assistance."
)


def update_agent_prompts(db_path: str = config.DATABASE_PATH) -> int:
    """Check each agent in the database and append internal guidance instructions if missing."""
    storage.init_db(db_path)
    agents = storage.list_agents(db_path)
    if not agents:
        print(f"No agents found in {db_path}. Nothing to update.")
        return 0

    updated_count = 0
    for agent in agents:
        agent_id = agent["id"]
        current_prompt = agent["system_prompt"]

        if MARKER in current_prompt:
            print(f"Agent '{agent['name']}' ({agent_id}): marker '{MARKER}' already present. Skipping.")
            continue

        updated_prompt = current_prompt + GUIDANCE_ADDENDUM
        storage.update_agent_system_prompt(db_path, agent_id, updated_prompt)
        print(f"Agent '{agent['name']}' ({agent_id}): successfully updated system prompt with internal guidance.")
        updated_count += 1

    return updated_count


if __name__ == "__main__":
    print(f"Checking database at: {config.DATABASE_PATH}")
    count = update_agent_prompts()
    print(f"Done. {count} agent(s) updated.")
