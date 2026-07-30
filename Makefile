# One flat test suite has to import the shared core AND both apps, and each app's own
# package sits under apps/<name>/, so `.` alone is not enough. The Dockerfiles flatten
# packages/coros_core to /app/coros_core, which keeps `import coros_core` identical in
# the container. Scripts get the path from here; pytest gets it from pytest.ini's
# pythonpath, so check/verify run byte-identically to CI.
PYPATH := .:packages:apps/brujula:apps/huella
VENV_PY := ./.venv/bin/python
PY := PYTHONPATH=$(PYPATH) $(VENV_PY)
.PHONY: setup check verify doctor fixtures dev-brujula dev-huella clean

setup:
	python3.12 -m venv .venv
	./.venv/bin/pip install -q --upgrade pip
	./.venv/bin/pip install -q -r requirements.txt
	@echo "setup ok"

check:
	$(VENV_PY) -m pytest -m "not live" -q

verify:
	$(VENV_PY) -m pytest -m live -q

doctor:
	@$(PY) scripts/doctor.py

fixtures:
	$(PY) scripts/dump_fixtures.py

# reflex must run with the app directory as cwd: .web/ and reflex.lock/ resolve against
# it. Ports are pinned per app in each rxconfig.py so both can run at once.
dev-brujula:
	cd apps/brujula && PYTHONPATH=$(CURDIR)/packages:$(CURDIR)/apps/brujula ../../.venv/bin/reflex run

dev-huella:
	cd apps/huella && PYTHONPATH=$(CURDIR)/packages:$(CURDIR)/apps/huella ../../.venv/bin/reflex run

# Run artifacts only. Leaves apps/*/.web alone — dropping it costs a long recompile,
# which is not what you want mid-iteration.
clean:
	rm -rf apps/*/.states .pytest_cache .playwright-mcp .reflex-*.log
	find . -path ./.venv -prune -o -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
