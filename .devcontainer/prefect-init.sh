#!/bin/sh
# Prefect initialization script
# Creates the work pool after the server is ready

export PREFECT_API_DATABASE_CONNECTION_URL="${PREFECT_API_DATABASE_CONNECTION_URL}"
export PREFECT_API_URL="http://localhost:4200/api"

# Start server in background
prefect server database upgrade -y
prefect server start --host 0.0.0.0 --port 4200 &
SERVER_PID=$!

# Wait for server to be ready
echo "Waiting for Prefect server to start..."
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if python -c "import urllib.request; urllib.request.urlopen('http://localhost:4200/api/health').read()" 2>/dev/null; then
    echo "Server is ready!"
    break
  fi
  sleep 2
done

# Create work pool (using 'agent-pool' since 'prefect-agent' is reserved)
echo "Creating work pool 'agent-pool'..."
prefect work-pool create agent-pool --type process 2>/dev/null || \
  (prefect work-pool inspect agent-pool >/dev/null 2>&1 && echo "Work pool already exists")

# Keep server running
wait $SERVER_PID
