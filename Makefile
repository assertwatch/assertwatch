.PHONY: check test eval history

check:
	ruff check . && ruff format --check . && pytest -q

test:
	pytest -q

eval:
	python corpus/run.py

history:
	python scripts/real_history.py .cache/real-history.json
