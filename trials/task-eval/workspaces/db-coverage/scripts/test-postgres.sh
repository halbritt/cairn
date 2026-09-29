#!/usr/bin/env bash
# Fixture stand-in: records that the disposable-database suite was requested.
set -euo pipefail
echo "provisioning disposable PostgreSQL (fixture)"
touch .integration-ran
CAIRN_TEST_DATABASE_URL="fixture" go test ./...
