.PHONY: check test eval

check:
	ruff check . && ruff format --check . && pytest -q

test:
	pytest -q

eval:
	python corpus/run.py
