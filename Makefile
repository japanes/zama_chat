# zama_chat — common commands. Backend lives in backend/, admin (Nuxt) in admin/.
BACKEND = cd backend &&
UV = uv run
TENANT ?= demo

.PHONY: up up-search down migrate revision api worker test test-db lint fmt seed seed-scenarios jev-probe eval i18n-check admin admin-build

up:            ## start PostgreSQL
	docker compose up -d postgres

up-search:     ## start Elasticsearch + Qdrant (stage 5)
	docker compose --profile search up -d

down:
	docker compose down

migrate:       ## apply DB migrations
	$(BACKEND) $(UV) alembic upgrade head

revision:      ## make revision m="message"
	$(BACKEND) $(UV) alembic revision --autogenerate -m "$(m)"

api:           ## run the API (http://localhost:8090)
	$(BACKEND) $(UV) uvicorn app.main:app --reload --port 8090

worker:        ## run the jobs worker
	$(BACKEND) $(UV) zama worker

test:          ## all tests (needs `make up` for the db ones)
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
	$(BACKEND) $(UV) zama seed-prompts
	$(BACKEND) $(UV) zama load-scenarios ../scenarios --tenant $(TENANT)

seed-scenarios: ## load scenarios/ for TENANT (default demo)
	$(BACKEND) $(UV) zama load-scenarios ../scenarios --tenant $(TENANT)

jev-probe:     ## Jev probe on uk/ru/en -> tmp/jev-probe/report.md
	$(BACKEND) $(UV) python -m scripts.jev_probe

eval:          ## replay eval/dialogs on real models -> tmp/eval/ (MODEL_REPLY=… EFFORT=… SHADOW=1 ONLY=…)
	$(BACKEND) $(UV) python -m scripts.eval

i18n-check:    ## same keys in every admin locale + every backend error code translated
	$(BACKEND) $(UV) python -m scripts.i18n_check

admin:         ## run the admin (http://localhost:3120)
	cd admin && npm run dev

admin-build:   ## production build of the admin -> admin/.output
	cd admin && npm run build
