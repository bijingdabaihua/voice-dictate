PY := .venv/bin/python

.PHONY: run test lint format app dmg clean

run:
	$(PY) dictate.py

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .

format:
	$(PY) -m ruff format .

app:
	SKIP_DMG=1 ./packaging/build-app.sh

dmg:
	./packaging/build-app.sh

clean:
	rm -rf build dist
