.PHONY: scan-local test

scan-local:
	PYTHONPATH=. .venv/bin/python -m scripts.scan --dry-run --skip-claude

test:
	PYTHONPATH=. .venv/bin/python -m pytest
