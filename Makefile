.PHONY: build test test-sdk-spawn test-sdk-ipc test-sdk-native-ipc test-sdk-native-process-owner test-sdk-node-owner test-sdk-peer test-integration test-lifecycle test-hybrid-runtime-operations test-isolated check contract semantic-rust-build semantic-stage-rust test-semantic-package test-semantic-package-real
build:
	go build -o bin/cairn ./cmd/cairn
test:
	go test ./...
	python3 -B -m unittest discover -s scripts -p 'test_*.py'
	$(MAKE) test-sdk-spawn
	$(MAKE) test-sdk-ipc
test-sdk-ipc:
	python3 -B -m unittest discover -s scripts -p 'test_trial_sdk_ipc*.py'
	python3 -B -m unittest discover -s scripts -p 'test_trial_sdk_input_profile.py'
	@set -e; for suite in scripts/test_trial_sdk_ipc.cjs scripts/test_trial_sdk_ipc_profile2.cjs scripts/test_trial_sdk_input_profile.cjs; do \
		node --permission --allow-fs-read=./scripts --no-global-search-paths --disable-sigusr1 --require ./scripts/guard_trial_sdk_ipc.cjs "$$suite"; \
	done
test-sdk-spawn:
	@set -e; for suite in scripts/test_trial_sdk_spawn_bridge.cjs scripts/test_trial_sdk_spawn_utf8.cjs scripts/test_trial_sdk_spawn_invalid_utf8.cjs scripts/test_trial_sdk_spawn_lifetime.cjs; do \
		node --permission --allow-fs-read=./scripts --no-global-search-paths --disable-sigusr1 --require ./scripts/guard_trial_sdk_spawn_bridge.cjs "$$suite"; \
	done
test-sdk-native-ipc:
	@test -n "$(SDK_IPC_TEST_OUTPUT)" || { echo 'Set SDK_IPC_TEST_OUTPUT to a fresh absolute output directory'; exit 2; }
	python3 -B scripts/test_trial_sdk_native_ipc.py --output "$(SDK_IPC_TEST_OUTPUT)"
test-sdk-native-process-owner:
	@test -n "$(SDK_OWNER_TEST_OUTPUT)" || { echo 'Set SDK_OWNER_TEST_OUTPUT to a fresh absolute output directory'; exit 2; }
	python3 -B scripts/check_trial_sdk_native_process_owner.py --output "$(SDK_OWNER_TEST_OUTPUT)"
test-sdk-node-owner:
	@test -n "$(SDK_NODE_OWNER_TEST_OUTPUT)" || { echo 'Set SDK_NODE_OWNER_TEST_OUTPUT to a fresh absolute output directory'; exit 2; }
	python3 -B scripts/check_trial_sdk_node_owner.py --output "$(SDK_NODE_OWNER_TEST_OUTPUT)"
test-sdk-peer:
	@test -n "$(SDK_PEER_TEST_OUTPUT)" || { echo 'Set SDK_PEER_TEST_OUTPUT to a fresh absolute output directory'; exit 2; }
	python3 -B scripts/check_trial_sdk_peer.py --output "$(SDK_PEER_TEST_OUTPUT)"
test-integration:
	bash scripts/test-postgres.sh
test-lifecycle: build
	bash scripts/test-local-lifecycle.sh
test-hybrid-runtime-operations:
	python3 -I -B scripts/test_hybrid_runtime_operations.py $(ISOLATED_ARGS)
test-isolated:
	python3 -I -B scripts/test_hybrid_runtime_operations.py $(ISOLATED_ARGS) $(if $(RO_BIND),--ro-bind "$(RO_BIND)",) $(if $(RECEIPT),--receipt "$(RECEIPT)",) $(if $(CMD),-- $(CMD),)
semantic-rust-build:
	python3 -I -B scripts/semantic_package.py build --source-root "$(CURDIR)" --output "$(SEMANTIC_BUILD_DIR)"
semantic-stage-rust:
	bash scripts/install-semantic.sh --stage-rust $(SEMANTIC_PACKAGE_ARGS)
test-semantic-package:
	python3 -I -B scripts/test_semantic_package.py
test-semantic-package-real:
	python3 -I -B scripts/check_semantic_offline_package.py $(SEMANTIC_PACKAGE_ARGS)
contract:
	go run ./internal/apicontract/cmd/cairn-contract
check:
	go vet ./...
	go run ./internal/apicontract/cmd/cairn-contract -check
	test -z "$$(gofmt -l core cmd runner localapi artifacts mcpapi semantic integrations internal)"
	python3 -B scripts/check_design_sources.py
