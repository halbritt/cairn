.PHONY: build test test-integration test-lifecycle test-hybrid-runtime-operations test-isolated check contract semantic-rust-build semantic-stage-rust test-semantic-package test-semantic-package-real
build:
	go build -o bin/cairn ./cmd/cairn
test:
	go test ./...
	python3 -B -m unittest discover -s scripts -p 'test_*.py'
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
