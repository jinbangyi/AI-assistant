# Configuration Files

This directory contains all configuration files for the development container services.

## Files

### `.env.example`
Example environment variables file. Copy this to `.env` in the parent `.devcontainer/` directory (not here) for docker-compose to automatically load it.

```bash
cp .env.example ../.env
# Then edit ../.env with your actual values
```

### `litellm_config.yaml`
LiteLLM proxy configuration file. This defines:
- Available models (OpenAI, Anthropic, etc.)
- Model configurations and settings
- API key references (keys are set via environment variables)

To customize, edit this file and restart the LiteLLM service.

### `n8n-init-db.sql.example`
Example PostgreSQL initialization script for n8n. If you need custom database initialization:

1. Copy this file to `n8n-init-db.sql`
2. Customize it with your SQL commands
3. Uncomment the volume mount in `n8n-docker-compose.yml`

Note: n8n creates its own tables automatically, so this is only needed for custom initialization.

### `signoz/` Directory
SigNoz observability platform configuration files:

- **`otel-collector-config.yaml`**: OpenTelemetry Collector configuration for receiving traces, metrics, and logs
- **`signoz/prometheus.yml`**: Prometheus configuration for SigNoz
- **`signoz/otel-collector-opamp-config.yaml`**: OPAMP (Open Agent Management Protocol) configuration
- **`clickhouse/`**: ClickHouse database configuration files (config.xml, users.xml, cluster.xml, etc.)
- **`dashboards/`**: Custom dashboard definitions (add your dashboards here)
- **`histogram-quantile.tar.gz`**: Required binary for ClickHouse histogram quantile function

To customize SigNoz:
- Edit `otel-collector-config.yaml` to add new receivers, processors, or exporters
- Add custom dashboards to the `dashboards/` directory
- Modify ClickHouse settings in `clickhouse/config.xml` if needed

## Adding New Configuration Files

When adding new configuration files:
1. Place them in this `configs/` directory
2. Update the corresponding docker-compose.yml file to mount from `./configs/your-config-file`
3. Document the file in this README
