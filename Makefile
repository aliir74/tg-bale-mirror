.PHONY: install dev test lint format typecheck check run clean install-agent uninstall-agent agent-status \
        deploy restart start stop status logs logs-follow ssh push-env pull-state install-systemd \
        session

install:
	uv sync

dev:
	uv sync --extra dev

test:
	uv run pytest -v

lint:
	uv run ruff check src tests

format:
	uv run ruff format src tests

typecheck:
	uv run pyright

check: lint typecheck test

run:
	uv run python -m src.main

clean:
	rm -rf __pycache__ .pytest_cache .ruff_cache dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +

# --- LaunchAgent (macOS) ---

PLIST_TEMPLATE := ops/tg-bale-mirror.plist.template
PLIST_INSTALLED := $(HOME)/Library/LaunchAgents/local.tg-bale-mirror.plist
PLIST_LABEL := local.tg-bale-mirror

install-agent:
	@command -v uv >/dev/null || { echo "uv not found in PATH"; exit 1; }
	@REPO_PATH=$$(pwd); UV_PATH=$$(command -v uv); \
	  sed -e "s|\$${REPO_PATH}|$$REPO_PATH|g" \
	      -e "s|\$${UV_PATH}|$$UV_PATH|g" \
	      -e "s|\$${HOME}|$$HOME|g" \
	      $(PLIST_TEMPLATE) > $(PLIST_INSTALLED)
	@launchctl unload $(PLIST_INSTALLED) 2>/dev/null || true
	launchctl load $(PLIST_INSTALLED)
	@echo "Installed and loaded $(PLIST_LABEL). Logs: ~/Library/Logs/tg-bale-mirror.log"

uninstall-agent:
	@launchctl unload $(PLIST_INSTALLED) 2>/dev/null || true
	@rm -f $(PLIST_INSTALLED)
	@echo "Removed $(PLIST_LABEL)"

agent-status:
	@launchctl list | grep $(PLIST_LABEL) || echo "$(PLIST_LABEL) not loaded"

# --- one-time helpers ---

session:
	uv run python scripts/generate_session.py

# --- VPS deploy (systemd on your-vps) ---

SSH_HOST ?= your-vps
REMOTE_DIR ?= /opt/tg-bale-mirror
SERVICE ?= tg-bale-mirror
SSH := ssh $(SSH_HOST)

deploy:
	@if ! git -C . diff --quiet HEAD origin/main -- 2>/dev/null; then \
		echo ">>> WARNING: local HEAD differs from origin/main. Push first."; \
		git log --oneline origin/main..HEAD; \
		exit 1; \
	fi
	$(SSH) 'set -e; cd $(REMOTE_DIR) && git fetch && git reset --hard origin/main && /root/.local/bin/uv sync --all-extras && systemctl restart $(SERVICE) && sleep 2 && systemctl is-active $(SERVICE)'
	@echo ">>> deployed. tail logs with: make logs"

restart:
	$(SSH) 'systemctl restart $(SERVICE) && systemctl is-active $(SERVICE)'

start:
	$(SSH) 'systemctl start $(SERVICE) && systemctl is-active $(SERVICE)'

stop:
	$(SSH) 'systemctl stop $(SERVICE)'

status:
	$(SSH) 'systemctl status $(SERVICE) --no-pager'

logs:
	$(SSH) 'journalctl -u $(SERVICE) -n 100 --no-pager'

logs-follow:
	$(SSH) 'journalctl -u $(SERVICE) -f'

ssh:
	$(SSH) -t 'cd $(REMOTE_DIR); exec $$SHELL -l'

push-env:
	@echo ">>> pushing local .env to $(SSH_HOST):$(REMOTE_DIR)/.env (Ctrl-C to abort)"
	@sleep 3
	scp .env $(SSH_HOST):$(REMOTE_DIR)/.env
	$(SSH) 'chmod 600 $(REMOTE_DIR)/.env && chown root:root $(REMOTE_DIR)/.env'
	@echo ">>> done. Restart service to pick up env changes: make restart"

pull-state:
	mkdir -p state-backup
	-scp $(SSH_HOST):$(REMOTE_DIR)/.bale_retry_queue state-backup/ 2>/dev/null
	@echo ">>> state files saved to ./state-backup/ (missing files are fine)"

install-systemd:
	scp ops/tg-bale-mirror.service $(SSH_HOST):/etc/systemd/system/$(SERVICE).service
	$(SSH) 'systemctl daemon-reload && systemctl enable $(SERVICE)'
	@echo ">>> systemd unit installed and enabled. Start with: make start"
