# Dify Workspace

Dify is an LLM application development platform.

## Services

| Service | Container | Port | Description |
|---------|-----------|------|-------------|
| Dify Web UI | dify-nginx | 8010 | Web interface |
| Dify API | dify-api | - | Backend API (via nginx) |
| Plugin Daemon | dify-plugin-daemon | 5003 | Plugin API |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| DIFY_HTTP_PORT | 8010 | Web UI port |
| DIFY_PLUGIN_PORT | 5003 | Plugin daemon port |
| DIFY_SECRET_KEY | sk-xxxxx | Encryption key |
| DIFY_DB_PASSWORD | postgres | Database password |
| SANDBOX_API_KEY | dify-sandbox | Sandbox API key |
| PLUGIN_DAEMON_KEY | dify-plugin-daemon-key | Plugin daemon key |

## Usage

```bash
# Start via manager
./manager.sh start dify

# Start manually
cd src/dify-workspace
docker-compose up -d

# View logs
./manager.sh logs dify

# Stop
./manager.sh stop dify
```

## URLs

- Web UI: http://localhost:8010
- API: http://localhost:8010/api (via nginx)
- Plugin Daemon: http://localhost:5003