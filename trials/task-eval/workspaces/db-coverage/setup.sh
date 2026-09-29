git init -q . && git add -A && git commit -qm "initial" && sed -i "s/ORDER BY written_at DESC/ORDER BY written_at DESC, id/" core/store.go
