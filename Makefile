VENV	=	.venv
PYTHON	=	$(VENV)/bin/python

# NAB (Numenta Anomaly Benchmark) figé sur un commit : les mêmes fichiers à chaque téléchargement
NAB	=	https://raw.githubusercontent.com/numenta/NAB/ea702d75cc2258d9d7dd35ca8e5e2539d71f3140
NAB_FILES	=	data/nab/nyc_taxi.csv \
		data/nab/machine_temperature_system_failure.csv \
		data/nab/combined_windows.json

all:	install

install:	$(VENV)

$(VENV):	pyproject.toml
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install -e '.[dev]'
	touch $(VENV)

lint:	$(VENV)
	$(PYTHON) -m ruff check
	$(PYTHON) -m ruff format --check

format:	$(VENV)
	$(PYTHON) -m ruff check --fix
	$(PYTHON) -m ruff format

typecheck:	$(VENV)
	$(PYTHON) -m mypy

test:	$(VENV)
	$(PYTHON) -m pytest

check:	lint typecheck test

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

fclean:	clean
	rm -rf $(VENV) src/*.egg-info

re:	fclean install

nab:	$(NAB_FILES)

data/nab/%.csv:
	mkdir -p data/nab
	curl -sSfL -o $@ $(NAB)/data/realKnownCause/$*.csv

data/nab/combined_windows.json:
	mkdir -p data/nab
	curl -sSfL -o $@ $(NAB)/labels/combined_windows.json

.PHONY:	all install lint format typecheck test check clean fclean re nab
