.PHONY: build test test-integration check
build:
	go build -o bin/cairn ./cmd/cairn
test:
	go test ./...
test-integration:
	bash scripts/test-postgres.sh
check:
	go vet ./...
	test -z "$$(gofmt -l core cmd)"
