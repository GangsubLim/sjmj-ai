# sjmj-ai 로컬 개발용 Makefile (운영 배포는 scripts/install-launchagent.sh / launchd)
BACKEND_DIR  := apps/invoice-ocr/backend
FRONTEND_DIR := apps/invoice-ocr/frontend
BACKEND_PORT ?= 8400

.DEFAULT_GOAL := help
.PHONY: help install backend frontend dev build test

help: ## 사용 가능한 명령 목록
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36mmake %-10s\033[0m %s\n", $$1, $$2}'

install: ## 의존성 설치 (backend uv sync + frontend npm install)
	cd $(BACKEND_DIR) && uv sync
	cd $(FRONTEND_DIR) && npm install

backend: ## 백엔드 dev 서버 (:$(BACKEND_PORT), --reload)
	cd $(BACKEND_DIR) && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port $(BACKEND_PORT)

frontend: ## 프론트 dev 서버 (:5173)
	cd $(FRONTEND_DIR) && npm run dev

dev: ## 백엔드 + 프론트 동시 실행 (Ctrl-C로 함께 종료)
	@echo "backend http://127.0.0.1:$(BACKEND_PORT)  |  frontend http://localhost:5173"
	@trap 'kill 0' INT TERM; \
		$(MAKE) backend & \
		$(MAKE) frontend & \
		wait

build: ## 프론트 빌드 → frontend/dist (FastAPI가 서빙)
	cd $(FRONTEND_DIR) && npm run build

test: ## 백엔드 테스트 (pytest)
	cd $(BACKEND_DIR) && uv run pytest -q
