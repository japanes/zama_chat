# zama_chat — common commands. Everything runs in Docker: `make up` starts the database, the
# search, the API, the jobs worker and the admin (docker-compose.yml; a server —
# docker-compose.prod.yml). Tests and the linters run on this machine (uv / npm) and need
# only the database. Backend lives in backend/, admin (Nuxt) in admin/.
BACKEND = cd backend &&
UV = uv run
# the CLI inside the running API container: models, tenants, users, scenarios
ZAMA = docker compose exec api zama
TENANT ?= demo

.PHONY: up db down logs ps zama migrate revision test test-unit lint fmt seed seed-scenarios jev-probe eval i18n-check admin-build

up:            ## start everything: admin http://localhost:3120, API http://localhost:8090
	docker compose up -d --build --wait

db:            ## only PostgreSQL — enough for the tests
	docker compose up -d --wait postgres

down:
	docker compose down

logs:          ## logs of everything, or of one part: make logs s=api
	docker compose logs -f --tail=100 $(s)

ps:
	docker compose ps -a

zama:          ## a CLI command: make zama c="create-user me@example.com --superadmin"
	$(ZAMA) $(c)

migrate:       ## apply DB migrations (`make up` does it on every start)
	docker compose run --rm migrate

revision:      ## make revision m="message"
	$(BACKEND) $(UV) alembic revision --autogenerate -m "$(m)"

test:          ## all tests (needs `make db` for the db ones)
	$(BACKEND) $(UV) pytest

test-unit:
	$(BACKEND) $(UV) pytest -m "not db and not live"

lint: i18n-check
	$(BACKEND) $(UV) ruff check .
	$(BACKEND) $(UV) ruff format --check .

fmt:
	$(BACKEND) $(UV) ruff format .
	$(BACKEND) $(UV) ruff check --fix .

seed:          ## load the shared prompts and the scenarios of TENANT (default demo)
	$(ZAMA) seed-prompts
	$(ZAMA) load-scenarios ../scenarios --tenant $(TENANT)

seed-scenarios: ## load scenarios/ for TENANT (default demo)
	$(ZAMA) load-scenarios ../scenarios --tenant $(TENANT)

jev-probe:     ## Jev probe on uk/ru/en -> tmp/jev-probe/report.md
	$(BACKEND) $(UV) python -m scripts.jev_probe

eval:          ## replay eval/dialogs on real models -> tmp/eval/ (MODEL_REPLY=… EFFORT=… SHADOW=1 ONLY=…)
	$(BACKEND) $(UV) python -m scripts.eval

i18n-check:    ## same keys in every admin locale + every backend error code translated
	$(BACKEND) $(UV) python -m scripts.i18n_check

admin-build:   ## production build of the admin on this machine -> admin/.output
	cd admin && npm run build
