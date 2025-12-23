#!/usr/bin/env python3
"""
pgAdmin server auto-configuration script.
Adds PostgreSQL server to pgAdmin SQLite database.
"""

import sqlite3
import os
import time
import json

# Path to pgAdmin storage
storage_dir = "/var/lib/pgadmin/storage/admin@prefect.io"
db_path = "/var/lib/pgadmin/pgadmin4.db"

# Server configuration
SERVER_NAME = "Prefect PostgreSQL"
HOST = "postgres"
PORT = 5432
MAINTENANCE_DB = "prefect"
USERNAME = "prefect"
PASSWORD = "prefect"

# Connection parameters JSON (includes SSL settings)
CONNECTION_PARAMS = json.dumps({
    "sslmode": "prefer",
    "connect_timeout": 30,
    "sslcompression": 0
})

print("Waiting for pgAdmin database to be created...")
for i in range(60):
    if os.path.exists(db_path):
        print(f"Database found at {db_path}")
        break
    time.sleep(1)
else:
    print(f"ERROR: Database not found at {db_path}")
    exit(1)

# Additional wait for database initialization
time.sleep(2)

print("Connecting to database...")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check if server already exists
cursor.execute("SELECT id FROM server WHERE name = ?", (SERVER_NAME,))
existing = cursor.fetchone()

if existing:
    print(f"Server '{SERVER_NAME}' already exists (ID: {existing[0]})")
    conn.close()
    exit(0)

# First, check if we have a server group
cursor.execute("SELECT id FROM servergroup WHERE user_id = 1 LIMIT 1")
group_result = cursor.fetchone()
if group_result:
    servergroup_id = group_result[0]
else:
    # Create a server group
    cursor.execute("""
        INSERT INTO servergroup (user_id, name)
        VALUES (1, 'Servers')
    """)
    conn.commit()
    servergroup_id = cursor.lastrowid
    print(f"Created server group with ID: {servergroup_id}")

# Insert the server configuration with correct schema
cursor.execute("""
    INSERT INTO server (user_id, servergroup_id, name, host, port, maintenance_db,
                        username, password, save_password, connection_params)
    VALUES (1, ?, ?, ?, ?, ?, ?, ?, 1, ?)
""", (
    servergroup_id,
    SERVER_NAME,
    HOST,
    PORT,
    MAINTENANCE_DB,
    USERNAME,
    PASSWORD,
    CONNECTION_PARAMS
))

conn.commit()
server_id = cursor.lastrowid
print(f"Server '{SERVER_NAME}' added successfully! (ID: {server_id})")
conn.close()
