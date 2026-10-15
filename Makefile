VENV	=	.venv
PYTHON	=	$(VENV)/bin/python

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

.PHONY:	all install lint format typecheck test check clean fclean re
