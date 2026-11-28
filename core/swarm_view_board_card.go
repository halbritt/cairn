package core

import (
	"encoding/json"
	"math"
	"regexp"
	"strings"
	"time"
)

// These bounds and the strict subset below follow agent-board/server/cards.py
// at 5ccacf9. For other forms we bound the lifetime without inferring a slot
// or default. The board's cap starts at poll time, which Cairn cannot observe.
const (
	swarmBoardDefaultTTL = 24 * time.Hour
	swarmBoardMaxTTL     = 72 * time.Hour
)

type swarmBoardCertainty uint8

const (
	swarmBoardCertainValid swarmBoardCertainty = iota
	swarmBoardCertainCardless
	swarmBoardUncertain
)

type swarmBoardCardFacts struct {
	certainty       swarmBoardCertainty
	keepUntil       time.Time
	defaultDeclared bool
	slot            string
}

func swarmBoardCard(block *string, published time.Time) swarmBoardCardFacts {
	cardlessUntil := published.Add(swarmBoardDefaultTTL)
	cardless := swarmBoardCardFacts{certainty: swarmBoardCertainCardless, keepUntil: cardlessUntil}
	if block == nil {
		return cardless
	}
	capAt := published.Add(swarmBoardMaxTTL)
	var value any
	decoder := json.NewDecoder(strings.NewReader(*block))
	decoder.UseNumber()
	if !json.Valid([]byte(*block)) || decoder.Decode(&value) != nil {
		// Python accepts some JSON spellings that Go rejects. Without an object
		// we cannot rule out a valid long lifetime elsewhere in the block.
		return swarmBoardCardFacts{certainty: swarmBoardUncertain, keepUntil: capAt}
	}
	card, ok := value.(map[string]any)
	if !ok {
		return cardless // Other JSON types are certainly not objects.
	}
	expiry, lifetime := swarmBoardLifetime(card, published)
	status, slot, defaultDeclared := swarmBoardFields(card)
	switch max(status, lifetime) {
	case swarmBoardCertainCardless:
		return cardless
	case swarmBoardUncertain:
		if lifetime == swarmBoardUncertain {
			expiry = capAt
		} else if expiry.Before(cardlessUntil) {
			expiry = cardlessUntil
		}
		return swarmBoardCardFacts{certainty: swarmBoardUncertain, keepUntil: expiry}
	default:
		return swarmBoardCardFacts{certainty: swarmBoardCertainValid, keepUntil: expiry, defaultDeclared: defaultDeclared, slot: slot}
	}
}

func swarmBoardLifetime(card map[string]any, published time.Time) (time.Time, swarmBoardCertainty) {
	status := swarmBoardCertainValid
	capAt := published.Add(swarmBoardMaxTTL)
	ttl := swarmBoardDefaultTTL.Seconds()
	if raw, present := card["ttl"]; present {
		seconds, ok := swarmBoardNumber(raw)
		switch {
		case !ok:
			status = swarmBoardUncertain
		case seconds < 1:
			status = swarmBoardCertainCardless
		default:
			ttl = min(seconds, swarmBoardMaxTTL.Seconds())
		}
	}
	// Round outward so a sub-nanosecond TTL fraction cannot remove a row early.
	expiry := published.Add(time.Duration(math.Ceil(ttl * float64(time.Second))))
	if raw, present := card["expires_at"]; present {
		at, ok := swarmBoardStamp(raw)
		if !ok {
			return capAt, swarmBoardUncertain
		}
		if !at.Equal(time.Unix(0, 0)) {
			expiry = at
		}
	}
	if expiry.After(capAt) {
		expiry = capAt
	}
	return expiry, status
}

func swarmBoardFields(card map[string]any) (swarmBoardCertainty, string, bool) {
	status := swarmBoardCertainValid
	if severity, present := card["severity"]; present {
		switch severity {
		case "info", "notice", "warn", "urgent":
		default:
			if _, ok := swarmBoardASCII(severity); ok {
				status = swarmBoardCertainCardless
			} else {
				status = swarmBoardUncertain
			}
		}
	}
	var shape string
	if raw, present := card["shape"]; present {
		var ok bool
		shape, ok = swarmBoardASCII(raw)
		if !ok {
			status = swarmBoardUncertain
		}
	}

	var slot string
	if raw, present := card["slot"]; present {
		if value, ok := swarmBoardASCII(raw); ok && value != "" {
			slot = strings.Map(func(r rune) rune {
				switch {
				case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9', strings.ContainsRune("_.-", r):
					return r
				default:
					return '-'
				}
			}, swarmBoardCleanASCII(value, 40))
		} else {
			status = swarmBoardUncertain
		}
	}

	var verbs map[string]bool
	if raw, present := card["verbs"]; present {
		var validity swarmBoardCertainty
		verbs, validity = swarmBoardVerbs(raw)
		status = max(status, validity)
	} else if strings.EqualFold(swarmBoardCleanASCII(shape, 24), "question") {
		status = max(status, swarmBoardCertainCardless)
	}
	var defaultDeclared bool
	if raw, present := card["default"]; present {
		id, ok := raw.(string)
		switch {
		case !ok:
			status = swarmBoardUncertain
		case !verbs[id]:
			status = max(status, swarmBoardCertainCardless)
		default:
			defaultDeclared = true
		}
	}
	return max(status, swarmBoardTypedFields(card)), slot, defaultDeclared
}

func swarmBoardASCII(raw any) (string, bool) {
	value, ok := raw.(string)
	for i := range len(value) {
		if value[i] >= 0x80 {
			return "", false
		}
	}
	return value, ok
}

func swarmBoardCleanASCII(value string, limit int) string {
	fields := strings.FieldsFunc(value, func(r rune) bool {
		return r == ' ' || r >= '\t' && r <= '\r' || r >= 0x1c && r <= 0x1f
	})
	value = strings.Join(fields, " ")
	return value[:min(len(value), limit)]
}

var swarmBoardVerbID = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,23}$`)

func swarmBoardVerbs(raw any) (map[string]bool, swarmBoardCertainty) {
	entries, ok := raw.([]any)
	if !ok || len(entries) < 1 || len(entries) > 3 {
		return nil, swarmBoardCertainCardless
	}
	status := swarmBoardCertainValid
	verbs := make(map[string]bool, len(entries))
	for _, rawVerb := range entries {
		entry, ok := rawVerb.(map[string]any)
		if !ok {
			status = max(status, swarmBoardCertainCardless)
			continue
		}
		id, idOK := swarmBoardASCII(entry["id"])
		label, labelOK := swarmBoardASCII(entry["label"])
		if !idOK || !labelOK {
			status = swarmBoardUncertain
		} else if _, exists := verbs[id]; exists || !swarmBoardVerbID.MatchString(id) || swarmBoardCleanASCII(label, 24) == "" {
			status = max(status, swarmBoardCertainCardless)
		}
		rawSnooze, snoozes := entry["snooze"]
		if snoozes {
			seconds, ok := swarmBoardNumber(rawSnooze)
			if !ok {
				status = swarmBoardUncertain
			} else if seconds < 60 || seconds > 86400 {
				status = max(status, swarmBoardCertainCardless)
			}
		}
		verbs[id] = !snoozes
	}
	return verbs, status
}

func swarmBoardTypedFields(card map[string]any) swarmBoardCertainty {
	status := swarmBoardCertainValid
	for _, field := range []string{"observed_at", "until", "start", "end"} {
		if raw, present := card[field]; present {
			if _, ok := swarmBoardStamp(raw); !ok {
				return swarmBoardUncertain
			}
		}
	}
	if raw, present := card["progress"]; present {
		progress, ok := swarmBoardNumber(raw)
		if !ok {
			return swarmBoardUncertain
		}
		if progress < 0 || progress > 1 {
			status = swarmBoardCertainCardless
		}
	}
	if raw, present := card["items"]; present {
		items, ok := raw.([]any)
		if !ok || len(items) > 12 {
			status = swarmBoardCertainCardless
		} else {
			for _, rawItem := range items {
				if item, ok := rawItem.(map[string]any); ok {
					rawItem = item["label"]
				}
				label, ok := swarmBoardASCII(rawItem)
				if !ok {
					return swarmBoardUncertain
				}
				if swarmBoardCleanASCII(label, 60) == "" {
					status = swarmBoardCertainCardless
				}
			}
		}
	}
	if raw, present := card["series"]; present {
		series, ok := raw.([]any)
		if !ok || len(series) < 2 || len(series) > 200 {
			return swarmBoardCertainCardless
		}
		for _, point := range series {
			if _, ok := swarmBoardNumber(point); !ok {
				return swarmBoardUncertain
			}
		}
	}
	return status
}

func swarmBoardNumber(raw any) (float64, bool) {
	number, ok := raw.(json.Number)
	if !ok {
		return 0, false
	}
	n, err := number.Float64()
	// Keep integers in the exactly representable range, well beyond any
	// supported lifetime or civil timestamp.
	return n, err == nil && math.Abs(n) < 1<<53
}

var swarmBoardISOStamp = regexp.MustCompile(`^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(\.[0-9]+)?(Z|[+-](?:[01][0-9]|2[0-3]):?[0-5][0-9])$`)

func swarmBoardStamp(raw any) (time.Time, bool) {
	if seconds, ok := swarmBoardNumber(raw); ok {
		if seconds < 0 {
			return time.Time{}, false
		}
		whole, fraction := math.Modf(seconds)
		return time.Unix(int64(whole), int64(math.Round(fraction*float64(time.Second)))).UTC(), true
	}
	stamp, _ := raw.(string)
	parts := swarmBoardISOStamp.FindStringSubmatch(stamp)
	if parts == nil {
		return time.Time{}, false
	}
	// Python's datetime uses microsecond precision and truncates extra digits.
	fraction := parts[2][:min(len(parts[2]), 7)]
	offset := parts[3]
	if len(offset) == 5 {
		offset = offset[:3] + ":" + offset[3:]
	}
	at, err := time.Parse(time.RFC3339Nano, parts[1]+fraction+offset)
	return at, err == nil && at.Year() >= 1
}
