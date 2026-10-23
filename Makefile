.PHONY: test lint check

test:
	cd agent && uv run --python 3.12 --with pytest --with pyyaml python -m pytest -q

lint:
	@files="$$(ls agent/*.sh agent/systemd/*.sh agent/hooks/* docs/*.sh 2>/dev/null)"; \
	if [ -n "$$files" ]; then uvx --from shellcheck-py shellcheck $$files; fi
	@if [ -d terraform ]; then \
		terraform -chdir=terraform fmt -check && terraform -chdir=terraform validate; \
	fi

check: lint test
