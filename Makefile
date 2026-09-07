.PHONY: build test test-integration test-lifecycle check
build:
	go build -o bin/cairn ./cmd/cairn
test:
	go test ./...
test-integration:
	bash scripts/test-postgres.sh
test-lifecycle: build
	bash scripts/test-local-lifecycle.sh
check:
	go vet ./...
	test -z "$$(gofmt -l core cmd runner localapi artifacts)"
