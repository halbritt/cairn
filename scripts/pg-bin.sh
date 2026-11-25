# Sourced by Cairn's PostgreSQL scripts; defines cairn_pg_bin.
# The schema needs pgvector (core/schema/056_semantic_passages.sql), and the
# default pg_config may name the newest server-dev install, which can lack it.
#
# cairn_pg_bin [DATA_DIR] prints the bin directory to use. An existing cluster
# keeps its own major version when that server is installed. Otherwise it picks
# the newest installed server whose share directory provides the extension,
# falling back to pg_config. Callers let CAIRN_PG_BIN override the choice.
cairn_pg_bin() {
    local data="${1:-}" candidate selected=""
    if [[ -n "$data" && -f "$data/PG_VERSION" ]]; then
        candidate="/usr/lib/postgresql/$(<"$data/PG_VERSION")/bin"
        if [[ -x "$candidate/pg_ctl" ]]; then
            printf '%s\n' "$candidate"
            return
        fi
    fi
    while IFS= read -r candidate; do
        candidate="${candidate%/pg_config}"
        if [[ -x "$candidate/initdb" && -f "$("$candidate/pg_config" --sharedir)/extension/vector.control" ]]; then
            selected="$candidate"
        fi
    done < <(ls /usr/lib/postgresql/*/bin/pg_config 2>/dev/null | sort --version-sort)
    printf '%s\n' "${selected:-$(pg_config --bindir)}"
}
