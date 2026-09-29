.PHONY: test test-integration check

test:
	go test ./...

# Provisions a disposable PostgreSQL cluster and runs the database-backed tests.
test-integration:
	bash scripts/test-postgres.sh

check:
	go vet ./...
	test -z "$$(gofmt -l .)"
