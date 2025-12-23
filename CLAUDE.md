# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an AI Assistant Development Environment that integrates multiple AI/ML services into a cohesive development ecosystem. The system focuses on AI workflow automation, trading data processing, and observability using a microservices architecture.

### Service Architecture

```
Open WebUI (8080) -> Dify (8010) -> n8n (5678, 5679) -> SigNoz
                                -> LiteLLM (4000) -> External LLMs
                                -> Temporal (7233, 8088)
                                -> Prefect (4200)
                                -> Superset (8091)
```

## Key Service URLs

| Service | Port | Purpose |
|---------|------|---------|
| Open WebUI | 8080 | ChatGPT-like user interface |
| Dify Web UI | 8010 | LLM application platform |
| Dify Plugin | 5003 | Dify plugin daemon |
| LiteLLM | 4000 | LLM proxy for multiple providers |
| n8n Server | 5678 | Workflow automation |
| n8n Webhook | 5679 | n8n webhook receiver |
| SigNoz UI | 3301 | Observability platform |
| SigNoz OTLP gRPC | 4317 | OpenTelemetry traces |
| SigNoz OTLP HTTP | 4318 | OpenTelemetry traces |
| Superset | 8091 | Data visualization |
| Prefect UI | 4200 | Prefect workflow orchestrator |
| Temporal UI | 8088 | Temporal workflow UI |
| Temporal API | 7233 | Temporal gRPC API |

## Service Management

### Using the Manager Script

The `manager.sh` script provides unified service management with port conflict detection.

```bash
# List all available services
./manager.sh list

# Start specific services
./manager.sh start dify openwebui litellm

# Start by profile
./manager.sh start ai           # dify, openwebui, litellm
./manager.sh start workflow     # n8n, prefect, temporal
./manager.sh start observability # signoz
./manager.sh start bi           # superset
./manager.sh start all          # everything

# Stop services
./manager.sh stop dify
./manager.sh stop ai

# Check status
./manager.sh status
./manager.sh status dify

# View logs
./manager.sh logs dify

# Show service URLs
./manager.sh urls
```

### Manual Service Start

Each service workspace can be started individually:

```bash
cd src/<service>-workspace
docker-compose up -d
```

## Project Structure

```
AI-assistant/
├── manager.sh                  # Unified service manager
├── .devcontainer/
│   ├── configs/               # Service configuration files
│   │   ├── signoz/           # SigNoz OTLP collector configs
│   │   └── nginx/            # Nginx proxy configs
│   └── .env                  # Environment variables
├── agents/                    # Trading agents
│   └── hype-trading-agent/   # Hyperliquid trading agent
├── src/
│   ├── dify-workspace/        # Dify LLM platform
│   │   ├── docker-compose.yaml
│   │   ├── configs/
│   │   └── README.md
│   ├── openwebui-workspace/   # Open WebUI chat interface
│   │   └── docker-compose.yaml
│   ├── litellm-workspace/     # LiteLLM proxy
│   │   ├── docker-compose.yaml
│   │   └── configs/
│   ├── n8n-workspace/         # n8n workflow automation
│   │   └── docker-compose.yaml
│   ├── signoz-workspace/      # SigNoz observability
│   │   ├── docker-compose.yaml
│   │   └── configs/
│   ├── superset-workspace/    # Apache Superset BI
│   │   ├── docker-compose.yaml
│   │   └── configs/
│   ├── prefect-workspace/     # Prefect workflows
│   │   ├── docker-compose.yaml
│   │   ├── flows/            # Workflow definitions
│   │   ├── examples/         # Usage examples
│   │   └── prefect-init.sh
│   └── temporal-workspace/    # Temporal workflows
│       ├── docker-compose.yaml
│       ├── workflows/        # Workflow definitions
│       ├── workers/          # Activity workers
│       └── examples/         # Usage examples
└── pyproject.toml            # Python dependencies
```

## Running Workflows

### Prefect Flows
Prefect uses a process-based work pool `hyperliquid-pool` created automatically on server start.

```bash
# Start Prefect services
./manager.sh start prefect

# Run a flow
cd src/prefect-workspace
python flows/hyperliquid_trades_flow.py
```

### Temporal Workflows
Temporal uses long-running worker processes for persistent WebSocket subscriptions.

```bash
# Start Temporal services
./manager.sh start temporal

# Run workflow and worker
cd src/temporal-workspace
python scripts/start_hyperliquid_subscription.py
python workers/hyperliquid_trade_worker.py
```

## Environment Configuration

Environment variables are configured in `.devcontainer/.env`:

### Dify
- `DIFY_SECRET_KEY` - Dify encryption key
- `DIFY_HTTP_PORT` - Web UI port (default: 8010)
- `DIFY_DB_PASSWORD` - Database password

### Open WebUI
- `WEBUI_SECRET_KEY` - Session key
- `OPENWEBUI_PORT` - UI port (default: 8080)

### LiteLLM
- `LITELLM_MASTER_KEY` - Admin key (default: `sk-nftgo-1234-abcd`)
- `LITELLM_SALT_KEY` - Hashing salt
- `LITELLM_PORT` - Proxy port (default: 4000)
- `LITELLM_DB_PASSWORD` - Database password

### n8n
- `N8N_POSTGRES_PASSWORD` - Database password
- `N8N_PORT` - Server port (default: 5678)
- `N8N_WEBHOOK_PORT` - Webhook port (default: 5679)

### SigNoz
- `SIGNOZ_UI_PORT` - UI port (default: 3301)
- `SIGNOZ_OTLP_GRPC_PORT` - OTLP gRPC (default: 4317)
- `SIGNOZ_OTLP_HTTP_PORT` - OTLP HTTP (default: 4318)

### Superset
- `SUPERSET_SECRET_KEY` - Encryption key
- `SUPERSET_HTTP_PORT` - UI port (default: 8091)
- `BIZ_DB_PASSWORD` - Database password

### Prefect
- `PREFECT_DB_PORT` - Database port (default: 5435)

### Temporal
- `TEMPORAL_UI_PORT` - API port (default: 7233)
- `TEMPORAL_WEB_PORT` - Web UI port (default: 8088)
- `TEMPORAL_DB_PORT` - Database port (default: 5436)

## Configuration Files

Located in each workspace's `configs/` directory:

- **dify-workspace/configs/nginx/** - Reverse proxy for Dify
- **litellm-workspace/configs/** - Model routing and API keys
- **signoz-workspace/configs/** - OTLP collector, ClickHouse, dashboards
- **superset-workspace/configs/** - Superset customization

## Python Dependencies

Core dependencies from `pyproject.toml`:
- `websocket-client` - WebSocket connections for Hyperliquid
- `temporalio` - Temporal workflow SDK
- `asyncpg` - Async PostgreSQL driver
- `websockets` - Async WebSocket support

## Architecture Notes

### Service Isolation
Each service runs in its own workspace under `src/<service>-workspace/` with:
- Independent `docker-compose.yaml`
- Dedicated configuration directory
- Isolated database (when applicable)
- Separate network

### Port Allocation
To prevent conflicts, each service uses unique ports:
- Dify: 8010, 5003
- Open WebUI: 8080
- LiteLLM: 4000, 6380
- n8n: 5678, 5679
- SigNoz: 3301, 4317, 4318
- Superset: 8091
- Prefect: 4200, 5435 (db)
- Temporal: 7233, 8088, 5436 (db)

### Multi-Orchestrator Design
The codebase supports both **Prefect** and **Temporal** for different use cases:
- **Prefect**: ETL-style flows with batch processing (database migration, data aggregation)
- **Temporal**: Long-running persistent workflows with real-time WebSocket subscriptions

### Database Isolation
Each service has its own PostgreSQL instance to allow independent management:
- `dify` / `dify_plugin` - Dify application data
- `n8n` - n8n workflow state
- `litellm` - LiteLLM proxy metrics and user management
- `prefect` - Prefect workflow state
- `temporal` - Temporal workflow state
- `biz` / `superset` - Business data and BI

### Observability Integration
LiteLLM is pre-configured to export traces, metrics, and logs to SigNoz via OTLP on port 4317. Configure your applications to use the OTLP endpoint for centralized observability.

## Initial Setup Tasks

See `TODO.md` for first-time setup:
1. Create LiteLLM admin user and configure API keys
2. Create SigNoz admin user
3. Create Open WebUI admin user
4. Create Dify admin user
5. Configure integration between services
