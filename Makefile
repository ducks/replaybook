.PHONY: help version-bump release build test clean clippy fmt fmt-check lint deploy-check harbor-check host-check skills-check pages-check install-hooks

define get_next_version
$(shell \
	TODAY=$$(date +%Y%m%d); \
	LATEST=$$(git tag -l "v$$TODAY.*" 2>/dev/null | sort -V | tail -1); \
	if [ -z "$$LATEST" ]; then \
		echo "$$TODAY.0.0"; \
	else \
		PATCH=$$(echo "$$LATEST" | sed 's/.*\.0\.\([0-9]*\)/\1/'); \
		echo "$$TODAY.0.$$((PATCH + 1))"; \
	fi \
)
endef

VERSION := $(get_next_version)

help:
	@echo "on-call Makefile"
	@echo ""
	@echo "Usage:"
	@echo "  make release                       - Auto-version and release (recommended)"
	@echo "  make release VERSION=20260125.0.0  - Release with specific version"
	@echo "  make build                         - Build release binary"
	@echo "  make test                          - Run tests"
	@echo "  make clippy                        - Run clippy"
	@echo "  make deploy-check                  - Validate deployment shell scripts"
	@echo "  make harbor-check                  - Validate Harbor integration scripts"
	@echo "  make host-check                    - Validate host-native evaluation scripts"
	@echo "  make skills-check                  - Validate bundled agent skills"
	@echo "  make clean                         - Clean build artifacts"
	@echo ""
	@echo "Next version will be: $(VERSION)"

version-bump:
	@echo "Next version: $(VERSION)"
	@echo "Creating release branch for version $(VERSION)..."
	@git checkout -b release/v$(VERSION)
	@echo "Bumping version to $(VERSION)..."
	@sed -i 's/^version = .*/version = "$(VERSION)"/' Cargo.toml
	@echo "Updating Cargo.lock..."
	@cargo check --quiet 2>/dev/null || true
	@git add Cargo.toml Cargo.lock
	@git commit -m "chore: bump version to $(VERSION)"
	@echo ""
	@echo "Created branch release/v$(VERSION)"
	@echo "Version bumped to $(VERSION)"
	@echo "Commit created"

release: version-bump
	@echo "Merging into main..."
	@git checkout main
	@git merge --no-ff release/v$(VERSION) -m "Merge branch 'release/v$(VERSION)'"
	@echo "Creating tag v$(VERSION) on main..."
	@git tag -a v$(VERSION) -m "Release v$(VERSION)"
	@echo "Pushing to origin..."
	@git push origin main
	@git push origin v$(VERSION)
	@echo "Publishing to crates.io..."
	@cargo publish
	@echo ""
	@echo "Released v$(VERSION)"
	@echo "  - Merged release/v$(VERSION) into main"
	@echo "  - Tagged v$(VERSION)"
	@echo "  - Pushed to GitHub"
	@echo "  - Published to crates.io"

build:
	cargo build --release

test:
	cargo test

clippy:
	cargo clippy -- -D warnings

clean:
	cargo clean

fmt:
	cargo fmt

fmt-check:
	cargo fmt -- --check

deploy-check:
	@find deploy -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n

harbor-check:
	@find integrations/harbor -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n
	@bash integrations/harbor/test-matrix-summary.sh
	@bash integrations/harbor/test-verifier-categories.sh
	@python -m unittest integrations.harbor.test_analyze_trajectory
	@python -m unittest integrations.harbor.test_report_matrix_results
	@python -m unittest integrations.harbor.test_scenario_sets

host-check:
	@bash integrations/host/test-host-native.sh
	@python -m unittest integrations.host.test_run_host_matrix
	@python -m unittest integrations.host.test_openrouter_proxy
	@python -m unittest integrations.host.test_guest_leak_audit
	@python -m unittest integrations.host.test_scenario_phase
	@python -m unittest integrations.host.test_publish_benchmarks
	@python -m unittest integrations.host.test_benchmark_submission
	@python -m unittest integrations.host.test_result_catalog
	@python integrations/host/publish_benchmarks.py check

skills-check:
	@bash -n skills/replaybook-add-harness/assets/adapter.sh
	@python skills/replaybook-add-harness/scripts/validate_result.py --help >/dev/null
	@grep -q '^name: replaybook-add-harness$$' skills/replaybook-add-harness/SKILL.md
	@grep -q '\$$replaybook-add-harness' skills/replaybook-add-harness/agents/openai.yaml
	@bash -n skills/replaybook-build-scenario/assets/oracle.sh
	@python skills/replaybook-build-scenario/scripts/scaffold_scenario.py --help >/dev/null
	@python skills/replaybook-build-scenario/scripts/validate_scenario.py --help >/dev/null
	@python skills/replaybook-build-scenario/scripts/test_tools.py >/dev/null
	@python skills/replaybook-build-scenario/scripts/validate_scenario.py integrations/host/scenarios/016-rails-pool-exhaustion >/dev/null
	@grep -q '^name: replaybook-build-scenario$$' skills/replaybook-build-scenario/SKILL.md
	@grep -q '\$$replaybook-build-scenario' skills/replaybook-build-scenario/agents/openai.yaml

pages-check:
	@python -m unittest tests.test_pages

lint: fmt-check deploy-check harbor-check host-check skills-check pages-check
	cargo clippy -- -D warnings
	cargo test

install-hooks:
	@mkdir -p .git/hooks
	@printf '#!/usr/bin/env bash\nset -e\nexec make lint\n' > .git/hooks/pre-push
	@chmod +x .git/hooks/pre-push
	@echo "Installed pre-push hook -> make lint"
