.PHONY: help bootstrap up down logs build init-ddb test-all clean

help:
	@echo "Valgo platform — workspace commands"
	@echo ""
	@echo "  make bootstrap    Clone all sibling repos"
	@echo "  make up           Start the local stack"
	@echo "  make init-ddb     Create DDB tables + seed config (run once after first up)"
	@echo "  make down         Stop the local stack"
	@echo "  make logs         Tail logs from all services"
	@echo "  make build        Rebuild all images"
	@echo "  make test-all     Run pytest in every Python repo"
	@echo "  make clean        docker compose down -v + prune build cache"

bootstrap:
	@./bootstrap.sh

up:
	@docker compose up -d

down:
	@docker compose down

logs:
	@docker compose logs -f --tail=100

build:
	@docker compose build

init-ddb:
	@python3 scripts/init-ddb.py

test-all:
	@for repo in ../valgo-common ../valgo-ingestor ../valgo-decision \
	             ../valgo-execution-router ../valgo-execution-node \
	             ../valgo-webhook ../valgo-admin-api; do \
	    echo "==> $$repo"; \
	    (cd $$repo && pytest -q) || exit 1; \
	done

clean:
	@docker compose down -v
	@docker builder prune -f
