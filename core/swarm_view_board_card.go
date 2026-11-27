package core

import (
	"encoding/json"
	"io"
	"math"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
)

// Fernside's card contract is pinned to agent-board/server/cards.py at 5ccacf9
// (parse_card, normalize, _number, _stamp, _verbs, _items and _series).
const (
	swarmBoardDefaultTTL = 24 * time.Hour
	swarmBoardMaxTTL     = 72 * time.Hour
)

type swarmBoardCardFacts struct {
	valid           bool
	expires         time.Time
	defaultDeclared bool
	slot            string
}

func swarmBoardCard(block string, published time.Time) swarmBoardCardFacts {
	fallback := swarmBoardCardFacts{expires: published.Add(swarmBoardDefaultTTL)}
	decoder := json.NewDecoder(strings.NewReader(block))
	decoder.UseNumber()
	var card map[string]any
	var extra any
	if decoder.Decode(&card) != nil || card == nil || decoder.Decode(&extra) != io.EOF {
		return fallback
	}
	switch card["severity"] {
	case nil, "info", "notice", "warn", "urgent":
	default:
		return fallback
	}

	capAt := published.Add(swarmBoardMaxTTL)
	ttl := swarmBoardDefaultTTL.Seconds()
	if raw, present := card["ttl"]; present {
		value, valid := swarmBoardNumber(raw)
		if !valid || value < 1 {
			return fallback
		}
		ttl = value
	}
	facts := swarmBoardCardFacts{
		valid:   true,
		expires: published.Add(time.Duration(min(ttl, swarmBoardMaxTTL.Seconds()) * float64(time.Second))),
	}
	for _, field := range []string{"expires_at", "observed_at", "until", "start", "end"} {
		if raw, present := card[field]; present {
			at, valid := swarmBoardStamp(raw, capAt)
			if !valid {
				return fallback
			}
			// normalize treats an expiry at epoch zero as unset.
			if field == "expires_at" && !at.Equal(time.Unix(0, 0)) {
				facts.expires = at
				if facts.expires.After(capAt) {
					facts.expires = capAt
				}
			}
		}
	}

	shape, _ := card["shape"].(string)
	if raw, present := card["verbs"]; present {
		verbs, valid := swarmBoardVerbs(raw)
		if !valid {
			return fallback
		}
		if rawDefault, declared := card["default"]; declared {
			id, ok := rawDefault.(string)
			if !ok || !verbs[id] {
				return fallback
			}
			facts.defaultDeclared = true
		}
	} else if _, declared := card["default"]; declared || strings.ToLower(swarmBoardClean(shape, 24)) == "question" {
		return fallback
	}
	if !swarmBoardTypedFields(card) {
		return fallback
	}
	if raw := card["slot"]; swarmBoardTruthy(raw) {
		slot, ok := swarmBoardStringOrInteger(raw)
		if raw == true {
			slot, ok = "True", true
		}
		// Container and floating-point representations are Python-specific.
		if !ok {
			return fallback
		}
		facts.slot = strings.Map(func(r rune) rune {
			if unicode.IsLetter(r) || unicode.IsNumber(r) || strings.ContainsRune("_.-", r) {
				return r
			}
			return '-'
		}, swarmBoardClean(slot, 40))
	}
	return facts
}

func swarmBoardStringOrInteger(raw any) (string, bool) {
	switch value := raw.(type) {
	case string:
		return value, true
	case json.Number:
		digits := value.String()
		if !strings.ContainsAny(digits, ".eE") {
			if digits == "-0" {
				digits = "0"
			}
			return digits, true
		}
	}
	return "", false
}

func swarmBoardNumber(value any) (float64, bool) {
	number, ok := value.(json.Number)
	if !ok {
		return 0, false
	}
	n, err := number.Float64()
	return n, err == nil && !math.IsInf(n, 0) && !math.IsNaN(n)
}

func swarmBoardTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case json.Number:
		n, _ := v.Float64()
		return n != 0
	case []any:
		return len(v) != 0
	case map[string]any:
		return len(v) != 0
	}
	return false
}

func swarmBoardClean(value string, limit int) string {
	words := strings.FieldsFunc(value, func(r rune) bool {
		return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f
	})
	runes := []rune(strings.Join(words, " "))
	return string(runes[:min(len(runes), limit)])
}

func swarmBoardHasLabel(value any) bool {
	if label, ok := value.(string); ok {
		return swarmBoardClean(label, 24) != ""
	}
	return swarmBoardTruthy(value)
}

var swarmBoardVerbID = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,23}$`)

// The map records whether each unique verb is eligible to be the default.
func swarmBoardVerbs(raw any) (map[string]bool, bool) {
	entries, ok := raw.([]any)
	if !ok || len(entries) < 1 || len(entries) > 3 {
		return nil, false
	}
	verbs := make(map[string]bool, len(entries))
	for _, rawVerb := range entries {
		entry, ok := rawVerb.(map[string]any)
		if !ok || !swarmBoardHasLabel(entry["label"]) {
			return nil, false
		}
		id, _ := swarmBoardStringOrInteger(entry["id"])
		if _, exists := verbs[id]; exists || !swarmBoardVerbID.MatchString(id) {
			return nil, false
		}
		rawSnooze, snoozes := entry["snooze"]
		if snoozes {
			seconds, valid := swarmBoardNumber(rawSnooze)
			if !valid || seconds < 60 || seconds > 24*3600 {
				return nil, false
			}
		}
		verbs[id] = !snoozes
	}
	return verbs, true
}

func swarmBoardTypedFields(card map[string]any) bool {
	if raw, present := card["progress"]; present {
		progress, valid := swarmBoardNumber(raw)
		if !valid || progress < 0 || progress > 1 {
			return false
		}
	}
	if raw, present := card["items"]; present {
		items, ok := raw.([]any)
		if !ok || len(items) > 12 {
			return false
		}
		for _, rawItem := range items {
			switch item := rawItem.(type) {
			case string:
				if !swarmBoardHasLabel(item) {
					return false
				}
			case map[string]any:
				if !swarmBoardHasLabel(item["label"]) {
					return false
				}
			default:
				return false
			}
		}
	}
	if raw, present := card["series"]; present {
		series, ok := raw.([]any)
		if !ok || len(series) < 2 || len(series) > 200 {
			return false
		}
		for _, point := range series {
			if _, valid := swarmBoardNumber(point); !valid {
				return false
			}
		}
	}
	return true
}

const swarmBoardISODate = `[0-9]{4}(?:-[0-9]{2}-[0-9]{2}|[0-9]{4}|-W[0-9]{2}(?:-[1-7])?|W[0-9]{2}[1-7]?)`
const swarmBoardISOClock = `[0-9]{2}(?::[0-9]{2}(?::[0-9]{2}(?:[.,][0-9]+)?)?|[0-9]{2}(?:[0-9]{2}(?:[.,][0-9]+)?)?)?`

var swarmBoardISOStamp = regexp.MustCompile(`^(` + swarmBoardISODate + `)(?s:.)(` + swarmBoardISOClock + `)(Z|([+-])(` + swarmBoardISOClock + `))$`)

func swarmBoardStamp(raw any, capAt time.Time) (time.Time, bool) {
	if seconds, valid := swarmBoardNumber(raw); valid {
		if seconds < 0 {
			return time.Time{}, false
		}
		// Bound large numeric timestamps before converting to Unix seconds.
		if seconds >= float64(capAt.Unix())+1 {
			return capAt, true
		}
		whole, fraction := math.Modf(seconds)
		return time.Unix(int64(whole), int64(math.Round(fraction*float64(time.Second)))).UTC(), true
	}
	stamp, _ := raw.(string)
	parts := swarmBoardISOStamp.FindStringSubmatch(stamp)
	if parts == nil {
		return time.Time{}, false
	}
	date, valid := swarmBoardDate(parts[1])
	if !valid {
		return time.Time{}, false
	}
	hour, minute, second, fraction := swarmBoardClock(parts[2])
	if hour >= 24 || minute >= 60 || second >= 60 {
		return time.Time{}, false
	}
	at := date.Add(time.Duration(hour)*time.Hour + time.Duration(minute)*time.Minute + time.Duration(second)*time.Second + fraction)
	if parts[3] == "Z" {
		return at, true
	}
	hour, minute, second, fraction = swarmBoardClock(parts[5])
	offset := time.Duration(hour)*time.Hour + time.Duration(minute)*time.Minute + time.Duration(second)*time.Second
	if offset != 0 {
		offset += fraction
	}
	if offset >= 24*time.Hour {
		return time.Time{}, false
	}
	if parts[4] == "-" {
		offset = -offset
	}
	return at.Add(-offset), true
}

func swarmBoardDate(raw string) (time.Time, bool) {
	date := strings.ReplaceAll(raw, "-", "")
	if date[4] != 'W' {
		at, err := time.Parse("20060102", date)
		return at, err == nil && at.Year() >= 1
	}
	year, _ := strconv.Atoi(date[:4])
	week, _ := strconv.Atoi(date[5:7])
	weekday := 1
	if len(date) == 8 {
		weekday = int(date[7] - '0')
	}
	jan4 := time.Date(year, 1, 4, 0, 0, 0, 0, time.UTC)
	at := jan4.AddDate(0, 0, (week-1)*7+weekday-1-(int(jan4.Weekday())+6)%7)
	isoYear, isoWeek := at.ISOWeek()
	return at, year >= 1 && at.Year() >= 1 && at.Year() <= 9999 && isoYear == year && isoWeek == week
}

// The caller has already matched the clock spelling. Python datetime discards
// fractional digits beyond microseconds.
func swarmBoardClock(raw string) (hour, minute, second int, fraction time.Duration) {
	whole := strings.ReplaceAll(raw, ":", "")
	if i := strings.IndexAny(whole, ".,"); i != -1 {
		digits := (whole[i+1:] + "000000")[:6]
		microseconds, _ := strconv.Atoi(digits)
		fraction = time.Duration(microseconds) * time.Microsecond
		whole = whole[:i]
	}
	whole += strings.Repeat("0", 6-len(whole))
	hour, _ = strconv.Atoi(whole[:2])
	minute, _ = strconv.Atoi(whole[2:4])
	second, _ = strconv.Atoi(whole[4:6])
	return
}
