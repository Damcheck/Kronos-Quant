import sqlite3
import os

db_path = os.path.expanduser('~/.forven/forven.db')
print("Connecting to DB:", db_path, os.path.exists(db_path))
c = sqlite3.connect(db_path)

print("Canceling pending tasks...")
x = c.execute("UPDATE agent_tasks SET status='failed', error='Manual override: Full-stack-engineer is disabled' WHERE agent_id='full-stack-engineer' AND status IN ('pending', 'running')").rowcount
print("agent_tasks canceled:", x)

c.commit()
print("Done")
