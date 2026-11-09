package coordination

import (
	"fmt"
	"math"
	"math/big"
	"os"
	"strconv"
	"syscall"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func timeFailure(class, message string) error { return &value.Error{Class: class, Message: message} }

// timestampParts follows datetime.fromtimestamp's time_t conversion and
// half-even rounding to microseconds. It selects no JSON/profile domain.
func timestampParts(at *value.Value) (int64, int64, error) {
	if at == nil {
		return 0, 0, timeFailure("TypeError", "timestamp is not a number")
	}
	if at.Kind == value.Int {
		n, ok := new(big.Int).SetString(at.Integer, 10)
		if !ok {
			return 0, 0, timeFailure("ValueError", "invalid timestamp integer")
		}
		if !n.IsInt64() {
			return 0, 0, timeFailure("OverflowError", "timestamp out of range for platform time_t")
		}
		return n.Int64(), 0, nil
	}
	var n float64
	switch at.Kind {
	case value.Bool:
		if at.Boolean {
			n = 1
		}
	case value.Float:
		n = at.Number
	default:
		return 0, 0, timeFailure("TypeError", "timestamp is not a number")
	}
	if math.IsNaN(n) {
		return 0, 0, timeFailure("ValueError", "Invalid value NaN (not a number)")
	}
	if math.IsInf(n, 0) || n < -0x1p63 || n >= 0x1p63 {
		return 0, 0, timeFailure("OverflowError", "timestamp out of range for platform time_t")
	}
	whole, fraction := math.Modf(n)
	seconds := int64(whole)
	microseconds := int64(math.RoundToEven(fraction * 1e6))
	if microseconds >= 1e6 {
		seconds++
		microseconds -= 1e6
	}
	if microseconds < 0 {
		seconds--
		microseconds += 1e6
	}
	return seconds, microseconds, nil
}

func utcTimestamp(at *value.Value) (*value.Value, error) {
	seconds, microseconds, err := timestampParts(at)
	if err != nil {
		return nil, err
	}
	// Linux gmtime_r represents tm_year in a signed C int. A conversion outside
	// that range is an OSError before datetime enforces years 1 through 9999.
	minimum := time.Date(math.MinInt32+1900, 1, 1, 0, 0, 0, 0, time.UTC).Unix()
	maximum := time.Date(math.MaxInt32+1901, 1, 1, 0, 0, 0, 0, time.UTC).Unix()
	if seconds < minimum || seconds >= maximum {
		return nil, &os.SyscallError{Syscall: "gmtime", Err: syscall.EOVERFLOW}
	}
	t := time.Unix(seconds, microseconds*1000).UTC()
	if t.Year() < 1 || t.Year() > 9999 {
		return nil, timeFailure("ValueError", fmt.Sprintf("year %d is out of range", t.Year()))
	}
	text := t.Format("2006-01-02T15:04:05")
	if microseconds != 0 {
		text += fmt.Sprintf(".%06d", microseconds)
	}
	return value.Text(text + "Z"), nil
}

// ObserveTime binds utc_timestamp(at), then max(0, int(now-at)) in Source order.
func ObserveTime(at *value.Value, now float64) (*value.Value, *value.Value, error) {
	observed, err := utcTimestamp(at)
	if err != nil {
		return nil, nil, err
	}
	var seconds float64
	switch at.Kind {
	case value.Int:
		seconds, err = strconv.ParseFloat(at.Integer, 64)
	case value.Float:
		seconds = at.Number
	case value.Bool:
		if at.Boolean {
			seconds = 1
		}
	}
	if err != nil {
		return nil, nil, timeFailure("OverflowError", "int too large to convert to float")
	}
	difference := now - seconds
	if math.IsNaN(difference) {
		return nil, nil, timeFailure("ValueError", "cannot convert float NaN to integer")
	}
	if math.IsInf(difference, 0) {
		return nil, nil, timeFailure("OverflowError", "cannot convert float infinity to integer")
	}
	age, _ := new(big.Float).SetFloat64(difference).Int(nil)
	if age.Sign() < 0 {
		age.SetInt64(0)
	}
	return observed, value.IntegerText(age.String()), nil
}
