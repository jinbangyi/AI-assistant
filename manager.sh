#!/bin/bash
# AI Assistant Service Manager
# Manages all AI services in individual workspaces

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.devcontainer/.env"

# Service definitions with their workspace paths and ports
declare -A SERVICES=(
    ["dify"]="src/dify-workspace|8010,5001,5003|Dify LLM Platform"
    ["openwebui"]="src/openwebui-workspace|8080|Open WebUI Chat Interface"
    ["litellm"]="src/litellm-workspace|4000,6380|LiteLLM Proxy"
    ["n8n"]="src/n8n-workspace|5678,5679|n8n Workflow Automation"
    ["signoz"]="src/signoz-workspace|3301,4317,4318|SigNoz Observability"
    ["superset"]="src/superset-workspace|8091|Apache Superset BI"
    ["prefect"]="src/prefect-workspace|4200|Prefect Workflow Orchestrator"
    ["temporal"]="src/temporal-workspace|7233,8088|Temporal Workflow Engine"
)

# Default services for common profiles
declare -A PROFILES=(
    ["ai"]="dify openwebui litellm"
    ["workflow"]="n8n prefect temporal"
    ["observability"]="signoz"
    ["bi"]="superset"
    ["all"]="dify openwebui litellm n8n signoz superset prefect temporal"
)

# Load environment file if exists
load_env() {
    if [ -f "$ENV_FILE" ]; then
        export $(grep -v '^#' "$ENV_FILE" | xargs)
    fi
}

# Check if a port is already in use
check_port_conflict() {
    local service=$1
    local workspace_path
    local ports
    local desc

    IFS='|' read -r workspace_path ports desc <<< "${SERVICES[$service]}"

    for port in ${ports//,/ }; do
        if lsof -i ":$port" >/dev/null 2>&1 || netstat -an 2>/dev/null | grep ":$port " | grep LISTEN >/dev/null; then
            echo -e "${YELLOW}Warning: Port $port ($desc) is already in use${NC}"
            return 0
        fi
    done
    return 1
}

# Check all port conflicts before starting
check_all_conflicts() {
    local services=("$@")
    local has_conflicts=0

    echo -e "${BLUE}Checking port conflicts...${NC}"
    for service in "${services[@]}"; do
        if check_port_conflict "$service"; then
            has_conflicts=1
        fi
    done

    if [ $has_conflicts -eq 1 ]; then
        echo -e "${RED}Some ports are already in use. Conflicting services may fail to start.${NC}"
        return 1
    fi
    echo -e "${GREEN}No port conflicts detected${NC}"
    return 0
}

# Get workspace path for a service
get_workspace_path() {
    local service=$1
    IFS='|' read -r workspace_path ports desc <<< "${SERVICES[$service]}"
    echo "${SCRIPT_DIR}/${workspace_path}"
}

# Check if workspace exists
workspace_exists() {
    local service=$1
    local workspace_path
    workspace_path=$(get_workspace_path "$service")
    [ -d "$workspace_path" ]
}

# Validate all requested services exist
validate_services() {
    local services=("$@")
    local missing=()

    for service in "${services[@]}"; do
        if [ -z "${SERVICES[$service]}" ]; then
            missing+=("$service")
        elif ! workspace_exists "$service"; then
            echo -e "${RED}Error: Workspace for '$service' not found at $(get_workspace_path "$service")${NC}"
            missing+=("$service")
        fi
    done

    if [ ${#missing[@]} -gt 0 ]; then
        echo -e "${RED}Unknown or missing services: ${missing[*]}${NC}"
        return 1
    fi
    return 0
}

# Start a single service
start_service() {
    local service=$1
    local workspace_path
    local ports
    local desc

    IFS='|' read -r workspace_path ports desc <<< "${SERVICES[$service]}"
    workspace_path="${SCRIPT_DIR}/${workspace_path}"

    if [ ! -f "${workspace_path}/docker-compose.yaml" ]; then
        echo -e "${RED}Error: docker-compose.yaml not found for $service${NC}"
        return 1
    fi

    echo -e "${BLUE}Starting $desc ($service)...${NC}"
    cd "$workspace_path"
    docker-compose up -d
}

# Stop a single service
stop_service() {
    local service=$1
    local workspace_path
    local ports
    local desc

    IFS='|' read -r workspace_path ports desc <<< "${SERVICES[$service]}"
    workspace_path="${SCRIPT_DIR}/${workspace_path}"

    if [ ! -f "${workspace_path}/docker-compose.yaml" ]; then
        echo -e "${YELLOW}Warning: docker-compose.yaml not found for $service${NC}"
        return 0
    fi

    echo -e "${BLUE}Stopping $desc ($service)...${NC}"
    cd "$workspace_path"
    docker-compose down
}

# Show status of a single service
status_service() {
    local service=$1
    local workspace_path
    local ports
    local desc

    IFS='|' read -r workspace_path ports desc <<< "${SERVICES[$service]}"
    workspace_path="${SCRIPT_DIR}/${workspace_path}"

    if [ ! -f "${workspace_path}/docker-compose.yaml" ]; then
        echo -e "${RED}$service: NOT CONFIGURED${NC}"
        return 0
    fi

    cd "$workspace_path"
    if docker-compose ps | grep -q "Up"; then
        echo -e "${GREEN}$service: RUNNING${NC} (ports: $ports)"
    else
        echo -e "${YELLOW}$service: STOPPED${NC}"
    fi
}

# Show logs for a single service
logs_service() {
    local service=$1
    local workspace_path
    local ports
    local desc

    IFS='|' read -r workspace_path ports desc <<< "${SERVICES[$service]}"
    workspace_path="${SCRIPT_DIR}/${workspace_path}"

    if [ ! -f "${workspace_path}/docker-compose.yaml" ]; then
        echo -e "${RED}Error: docker-compose.yaml not found for $service${NC}"
        return 1
    fi

    cd "$workspace_path"
    docker-compose logs -f
}

# List all available services
list_services() {
    echo -e "${BLUE}Available services:${NC}"
    echo ""
    printf "%-15s %-25s %s\n" "Service" "Ports" "Description"
    printf "%-15s %-25s %s\n" "-------" "-----" "-----------"
    for service in "${!SERVICES[@]}"; do
        IFS='|' read -r workspace_path ports desc <<< "${SERVICES[$service]}"
        if workspace_exists "$service"; then
            printf "%-15s %-25s %s\n" "$service" "$ports" "$desc"
        else
            printf "%-15s %-25s %s ${RED}(not configured)${NC}\n" "$service" "$ports" "$desc"
        fi
    done
    echo ""
    echo -e "${BLUE}Available profiles:${NC}"
    for profile in "${!PROFILES[@]}"; do
        echo "  $profile: ${PROFILES[$profile]}"
    done
}

# Show service URLs
show_urls() {
    echo -e "${BLUE}Service URLs:${NC}"
    echo ""
    echo "  Dify Web UI:       http://localhost:8010"
    echo "  Dify API:          http://localhost:5001"
    echo "  Open WebUI:        http://localhost:8080"
    echo "  LiteLLM:           http://localhost:4000"
    echo "  n8n:               http://localhost:5678"
    echo "  n8n Webhook:       http://localhost:5679"
    echo "  SigNoz UI:         http://localhost:3301"
    echo "  Superset:          http://localhost:8091"
    echo "  Prefect UI:        http://localhost:4200"
    echo "  Temporal UI:       http://localhost:8088"
}

# Expand profile to services
expand_profile() {
    local input=$1
    if [ -n "${PROFILES[$input]}" ]; then
        echo "${PROFILES[$input]}"
    else
        echo "$input"
    fi
}

# Main functions
start_services() {
    local services=()
    local all_expanded=""

    # Expand profiles and collect services
    for arg in "$@"; do
        expanded=$(expand_profile "$arg")
        all_expanded="$all_expanded $expanded"
    done

    # Remove duplicates and sort
    services=($(echo $all_expanded | xargs -n1 | sort -u | xargs))

    if [ ${#services[@]} -eq 0 ]; then
        echo -e "${RED}No services specified${NC}"
        list_services
        return 1
    fi

    validate_services "${services[@]}" || return 1
    check_all_conflicts "${services[@]}" || true

    load_env
    for service in "${services[@]}"; do
        start_service "$service" || true
    done

    echo ""
    echo -e "${GREEN}Services started!${NC}"
    show_urls
}

stop_services() {
    local services=()
    local all_expanded=""

    for arg in "$@"; do
        expanded=$(expand_profile "$arg")
        all_expanded="$all_expanded $expanded"
    done

    services=($(echo $all_expanded | xargs -n1 | sort -u | xargs))

    if [ ${#services[@]} -eq 0 ]; then
        echo -e "${RED}No services specified${NC}"
        return 1
    fi

    for service in "${services[@]}"; do
        if [ -n "${SERVICES[$service]}" ]; then
            stop_service "$service" || true
        else
            echo -e "${YELLOW}Unknown service: $service${NC}"
        fi
    done

    echo -e "${GREEN}Services stopped${NC}"
}

restart_services() {
    stop_services "$@"
    start_services "$@"
}

show_status() {
    local services=()
    local all_expanded=""

    for arg in "$@"; do
        expanded=$(expand_profile "$arg")
        all_expanded="$all_expanded $expanded"
    done

    services=($(echo $all_expanded | xargs -n1 | sort -u | xargs))

    if [ ${#services[@]} -eq 0 ]; then
        # Show status for all configured services
        for service in "${!SERVICES[@]}"; do
            if workspace_exists "$service"; then
                status_service "$service"
            fi
        done
    else
        for service in "${services[@]}"; do
            if [ -n "${SERVICES[$service]}" ]; then
                status_service "$service"
            else
                echo -e "${RED}Unknown service: $service${NC}"
            fi
        done
    fi
}

show_logs() {
    if [ $# -eq 0 ]; then
        echo -e "${RED}Please specify a service for logs${NC}"
        return 1
    fi

    local service=$1
    if [ -z "${SERVICES[$service]}" ]; then
        echo -e "${RED}Unknown service: $service${NC}"
        return 1
    fi

    if ! workspace_exists "$service"; then
        echo -e "${RED}Workspace for '$service' not found${NC}"
        return 1
    fi

    logs_service "$service"
}

# Print usage
print_usage() {
    cat << EOF
${BLUE}AI Assistant Service Manager${NC}

Usage: $0 <command> [services...]

Commands:
  start <service|profile>...   Start services (e.g., start dify, start ai)
  stop <service|profile>...    Stop services
  restart <service|profile>... Restart services
  status [service|profile]...  Show service status (default: all)
  logs <service>               Show logs for a service
  list                         List all available services
  urls                         Show service URLs
  help                         Show this help message

Services: ${!SERVICES[@]}
Profiles: ${!PROFILES[@]}

Examples:
  $0 start dify openwebui litellm
  $0 start ai                 # Starts dify, openwebui, litellm
  $0 start all
  $0 stop dify
  $0 status
  $0 logs dify

EOF
}

# Main entry point
main() {
    if [ $# -eq 0 ]; then
        print_usage
        exit 0
    fi

    local command=$1
    shift

    case $command in
        start)
            start_services "$@"
            ;;
        stop)
            stop_services "$@"
            ;;
        restart)
            restart_services "$@"
            ;;
        status)
            show_status "$@"
            ;;
        logs)
            show_logs "$@"
            ;;
        list)
            list_services
            ;;
        urls)
            show_urls
            ;;
        help|--help|-h)
            print_usage
            ;;
        *)
            echo -e "${RED}Unknown command: $command${NC}"
            print_usage
            exit 1
            ;;
    esac
}

main "$@"