.PHONY: help up down build migrate test test-docker logs shell clean

help: ## Show this help
	@awk 'BEGIN{FS=":.*##"} /^[a-zA-Z_-]+:.*##/{printf "  \033[36m%-18s\033[0m %s\n",$$1,$$2}' $(MAKEFILE_LIST)

up: ## Start all services
	docker compose up -d

down: ## Stop all services
	docker compose down

build: ## Build images
	docker compose build
	docker build -f Dockerfile.agent -t cpa_agent:latest .

migrate: ## Run Alembic migrations
	docker compose exec main-app alembic upgrade head

test: ## Run tests locally (requires venv + deps)
	pytest --cov=app --cov-report=term-missing --cov-fail-under=70 -v

test-docker: ## Run tests inside Docker
	docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm main-app

logs: ## Follow main-app logs
	docker compose logs -f main-app

shell: ## Open a shell in main-app
	docker compose exec main-app bash

clean: ## Remove all containers and volumes
	docker compose down -v --remove-orphans
	docker image prune -f

create-user: ## Create a user interactively
	@echo "Visit http://localhost:8000/register to create your account"
	@echo "Or use: curl -X POST http://localhost:8000/auth/register -H 'Content-Type: application/json' -d '{\"email\":\"you@example.com\",\"password\":\"Pass123!\",\"display_name\":\"Admin\"}'"

tunnel: ## Start a quick cloudflare tunnel (no account)
	cloudflared tunnel --url http://localhost:8000
