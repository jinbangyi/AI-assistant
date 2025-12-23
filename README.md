# README

- langfuse: observe, prompt manage, evaluate
- dify: integrate external services
- n8n: workflow automation
- open webui: user interface

open webui -> dify -> n8n -> langfuse
                   -> gitlab
                   -> grafana
                   -> prometheus
                   -> loki
                   -> signoz
           -> litellm -> deepseek
                      -> openrouter

- localhost:8010 -> dify-nginx
- localhost:8080 -> open webui
- localhost:4000 -> litellm
- localhost:5678 -> n8n
- localhost:3301 -> signoz
- localhost:4318/4317 -> signoz otel collector
