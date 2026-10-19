.PHONY: up down logs ps dev-backend dev-frontend test test-backend test-frontend lint build e2e ui-e2e

up:            ## production stack (docker compose, 2 backend replicas + nginx)
	./deploy.sh
down:
	docker compose down
logs:
	docker compose logs -f --tail=100
ps:
	docker compose ps

dev-backend:   ## SQLite + memory broker, auto-migrate, http://127.0.0.1:8000
	cd backend && APP_PROFILE=dev RUN_MIGRATIONS_ON_STARTUP=true uv run uvicorn --factory app.main:app_factory --reload --port 8000
dev-frontend:  ## Vite dev server with /api proxy, http://127.0.0.1:5173
	cd frontend && npm run dev

test: test-backend test-frontend
test-backend:
	cd backend && uv run ruff check . && uv run pytest -q
test-frontend:
	cd frontend && npm run typecheck && npm run lint && npm test && npm run build
e2e:           ## live DeepSeek smoke test against a running server (BASE=http://127.0.0.1:8000)
	cd backend && uv run python scripts/e2e_live.py --base-url $${BASE:-http://127.0.0.1:8000}
ui-e2e:        ## headless Chrome UI check + screenshots (BASE=http://127.0.0.1:5173)
	cd frontend && node scripts/ui-e2e.mjs $${BASE:-http://127.0.0.1:5173} ../docs/screenshots
