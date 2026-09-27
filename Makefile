.PHONY: build test test-integration test-lifecycle check contract
build:
	go build -o bin/cairn ./cmd/cairn
test:
	go test ./...
	python3 -B -m unittest discover -s scripts -p 'test_*.py'
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
