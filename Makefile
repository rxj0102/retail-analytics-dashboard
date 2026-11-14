# make reproduce   regenerate data, clean it, rebuild the SQLite database and write results/analysis_summary.txt
# make test        run the unit tests
.PHONY: setup reproduce test

setup:
	uv sync

reproduce:
	uv run python -m src.generate_data
	uv run python -m src.clean_data
	uv run python -m src.build_database
	uv run python -m src.analysis > results/analysis_summary.txt

test:
	uv run pytest -q
