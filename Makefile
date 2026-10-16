.PHONY: build test test-sdk-spawn test-sdk-ipc test-integration test-lifecycle check contract
build:
	go build -o bin/cairn ./cmd/cairn
test:
	go test ./...
	python3 -B -m unittest discover -s scripts -p 'test_*.py'
	$(MAKE) test-sdk-spawn
	$(MAKE) test-sdk-ipc
test-sdk-ipc:
	python3 -B -m unittest discover -s scripts -p 'test_trial_sdk_ipc.py'
	node --permission --allow-fs-read=./scripts --no-global-search-paths --disable-sigusr1 --require ./scripts/guard_trial_sdk_ipc.cjs scripts/test_trial_sdk_ipc.cjs
test-sdk-spawn:
	@set -e; for suite in scripts/test_trial_sdk_spawn_bridge.cjs scripts/test_trial_sdk_spawn_utf8.cjs scripts/test_trial_sdk_spawn_invalid_utf8.cjs scripts/test_trial_sdk_spawn_lifetime.cjs; do \
		node --permission --allow-fs-read=./scripts --no-global-search-paths --disable-sigusr1 --require ./scripts/guard_trial_sdk_spawn_bridge.cjs "$$suite"; \
	done
test-integration:
	bash scripts/test-postgres.sh
test-lifecycle: build
	bash scripts/test-local-lifecycle.sh
contract:
	go run ./internal/apicontract/cmd/cairn-contract
check:
	go vet ./...
	go run ./internal/apicontract/cmd/cairn-contract -check
	test -z "$$(gofmt -l core cmd runner localapi artifacts mcpapi semantic integrations internal)"
	python3 -B scripts/check_design_sources.py
