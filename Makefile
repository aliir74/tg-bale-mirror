.PHONY: install dev test lint format typecheck check run clean install-agent uninstall-agent agent-status

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

PLIST_TEMPLATE := ops/local.tg-bale-mirror.plist.template
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
