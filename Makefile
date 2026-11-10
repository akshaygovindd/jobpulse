.PHONY: check lint format-check typecheck test tf-check secrets fmt

TF_ROOTS := infra/terraform/bootstrap infra/terraform/main

check: lint format-check typecheck test tf-check secrets

lint:
	uv run ruff check .

format-check:
	uv run ruff format --check .

typecheck:
	uv run mypy

test:
	uv run pytest

tf-check:
	@if ! command -v terraform >/dev/null; then echo "terraform not installed: skipping tf-check"; exit 0; fi; \
	terraform fmt -check -recursive infra/terraform || exit 1; \
	for d in $(TF_ROOTS); do \
		echo "validate $$d"; \
		terraform -chdir=$$d init -backend=false -input=false >/dev/null && \
		terraform -chdir=$$d validate -no-color || exit 1; \
	done

secrets:
	@if command -v gitleaks >/dev/null; then gitleaks detect --source . --no-banner --redact; \
	else echo "gitleaks not installed locally: skipping (runs in CI)"; fi

fmt:
	uv run ruff format .
	uv run ruff check --fix .
	terraform fmt -recursive infra/terraform
