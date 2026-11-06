package main

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"

	"github.com/halbritt/cairn/core"
)

func (s *workServer) handleWorkItemCoordination(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.WriteHeader(http.StatusMethodNotAllowed)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"error": "method not allowed; use GET",
			"code":  "INVALID_REQUEST",
		})
		return
	}

	q := r.URL.Query()
	for key, vals := range q {
		switch key {
		case "project", "item", "limit", "wait_cursor", "notice_cursor":
			if len(vals) > 1 {
				writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "duplicate query parameter: " + key})
				return
			}
		default:
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "unsupported query parameter: " + key})
			return
		}
	}

	slug := strings.TrimSpace(q.Get("project"))
	if slug == "" {
		slug = s.defaultSlug
	}
	if slug == "" {
		writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "missing project slug"})
		return
	}
	itemStr := strings.TrimSpace(q.Get("item"))
	if itemStr == "" {
		writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "missing item identifier or uuid"})
		return
	}

	limit := 0
	if rawLimit := strings.TrimSpace(q.Get("limit")); rawLimit != "" {
		parsed, err := strconv.Atoi(rawLimit)
		if err != nil || parsed < 1 || parsed > 100 {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "limit must be an integer between 1 and 100"})
			return
		}
		limit = parsed
	}

	res, err := s.store.GetWorkItemCoordination(r.Context(), core.WorkCoordinationRequest{
		Collection:   s.collection,
		ProjectSlug:  slug,
		Item:         itemStr,
		Limit:        limit,
		WaitCursor:   q.Get("wait_cursor"),
		NoticeCursor: q.Get("notice_cursor"),
	})
	if err != nil {
		writeWorkHTTPError(w, err)
		return
	}
	_ = json.NewEncoder(w).Encode(res)
}
