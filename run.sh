# start temporal server with multi-user workers and biz db
docker-compose -f .devcontainer/temporal.docker-compose.yaml \
    -f .devcontainer/temporal-multi-user-workers.docker-compose.yaml \
    -f .devcontainer/temporal-hyperliquid-worker.docker-compose.yaml \
    -f .devcontainer/biz.docker-compose.yaml \
    --profile multi-user up -d

docker-compose -f .devcontainer/temporal.docker-compose.yaml \
    -f .devcontainer/temporal-multi-user-workers.docker-compose.yaml \
    -f .devcontainer/temporal-hyperliquid-worker.docker-compose.yaml \
    -f .devcontainer/biz.docker-compose.yaml \
    --profile multi-user logs -f biz-superset
