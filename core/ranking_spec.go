package core

import (
	"slices"
	"strconv"
	"strings"
)

// RankingChannel is the retrieval channel a sealed ranking identity names:
// the ordinary lexical compiler, the whole-body semantic ranker, the two
// ready-index contracts, or the binary IDF search.
type RankingChannel int

const (
	RankingLexical RankingChannel = iota
	RankingSemantic
	RankingHybrid
	RankingInterleaved
	RankingBinaryIDF
)

// RankingTier is the outer preference tier. Tiers are cumulative: a failure
// search also prefers quoted literals, and an entity search prefers both.
type RankingTier int

const (
	RankingTierPlain RankingTier = iota
	RankingTierLiteral
	RankingTierFailure
	RankingTierEntity
)

// RankingSpec is the parsed form of a sealed ranking identity such as
// "binary-idf-scope-recency/6". The sealed string stays the retained contract;
// this module parses it once so compiler stages read fields instead of
// re-deriving one bit each from the version number.
//
// Tokenizer is the lexical token-set generation (1–4). Only the plain lexical
// versions 1–4 retain older generations; every other identity uses generation 4.
// CompoundTerms marks adjacent-compound query terms (binary-idf 5–8 and the
// indexed IDF channel). IndexedIDF marks the ready indexed lexical channel
// carrying IDF evidence (interleaved 5–8).
type RankingSpec struct {
	Channel       RankingChannel
	Tier          RankingTier
	Tokenizer     int
	CompoundTerms bool
	IndexedIDF    bool
}

const rankingSuffix = "-scope-recency/"

var rankingChannelNames = [...]string{
	RankingLexical:     "lexical",
	RankingSemantic:    "semantic",
	RankingHybrid:      "hybrid",
	RankingInterleaved: "interleaved",
	RankingBinaryIDF:   "binary-idf",
}

// rankingSpecTable enumerates every sealed ranking identity the compiler has
// ever written. Adding a value means adding a row; nothing else enumerates
// versions. The table order groups by channel, then version number.
var rankingSpecTable = []RankingSpec{
	{Channel: RankingLexical, Tier: RankingTierPlain, Tokenizer: 1},
	{Channel: RankingLexical, Tier: RankingTierPlain, Tokenizer: 2},
	{Channel: RankingLexical, Tier: RankingTierPlain, Tokenizer: 3},
	{Channel: RankingLexical, Tier: RankingTierPlain, Tokenizer: 4},
	{Channel: RankingLexical, Tier: RankingTierLiteral, Tokenizer: 4},
	{Channel: RankingLexical, Tier: RankingTierFailure, Tokenizer: 4},
	{Channel: RankingLexical, Tier: RankingTierEntity, Tokenizer: 4},
	{Channel: RankingSemantic, Tier: RankingTierPlain, Tokenizer: 4},
	{Channel: RankingSemantic, Tier: RankingTierLiteral, Tokenizer: 4},
	{Channel: RankingSemantic, Tier: RankingTierFailure, Tokenizer: 4},
	{Channel: RankingSemantic, Tier: RankingTierEntity, Tokenizer: 4},
	{Channel: RankingHybrid, Tier: RankingTierPlain, Tokenizer: 4},
	{Channel: RankingHybrid, Tier: RankingTierLiteral, Tokenizer: 4},
	{Channel: RankingHybrid, Tier: RankingTierFailure, Tokenizer: 4},
	{Channel: RankingHybrid, Tier: RankingTierEntity, Tokenizer: 4},
	{Channel: RankingInterleaved, Tier: RankingTierPlain, Tokenizer: 4},
	{Channel: RankingInterleaved, Tier: RankingTierLiteral, Tokenizer: 4},
	{Channel: RankingInterleaved, Tier: RankingTierFailure, Tokenizer: 4},
	{Channel: RankingInterleaved, Tier: RankingTierEntity, Tokenizer: 4},
	{Channel: RankingInterleaved, Tier: RankingTierPlain, Tokenizer: 4, CompoundTerms: true, IndexedIDF: true},
	{Channel: RankingInterleaved, Tier: RankingTierLiteral, Tokenizer: 4, CompoundTerms: true, IndexedIDF: true},
	{Channel: RankingInterleaved, Tier: RankingTierFailure, Tokenizer: 4, CompoundTerms: true, IndexedIDF: true},
	{Channel: RankingInterleaved, Tier: RankingTierEntity, Tokenizer: 4, CompoundTerms: true, IndexedIDF: true},
	{Channel: RankingBinaryIDF, Tier: RankingTierPlain, Tokenizer: 4},
	{Channel: RankingBinaryIDF, Tier: RankingTierLiteral, Tokenizer: 4},
	{Channel: RankingBinaryIDF, Tier: RankingTierFailure, Tokenizer: 4},
	{Channel: RankingBinaryIDF, Tier: RankingTierEntity, Tokenizer: 4},
	{Channel: RankingBinaryIDF, Tier: RankingTierPlain, Tokenizer: 4, CompoundTerms: true},
	{Channel: RankingBinaryIDF, Tier: RankingTierLiteral, Tokenizer: 4, CompoundTerms: true},
	{Channel: RankingBinaryIDF, Tier: RankingTierFailure, Tokenizer: 4, CompoundTerms: true},
	{Channel: RankingBinaryIDF, Tier: RankingTierEntity, Tokenizer: 4, CompoundTerms: true},
}

var rankingSpecsByName = func() map[string]RankingSpec {
	m := make(map[string]RankingSpec, len(rankingSpecTable))
	for _, spec := range rankingSpecTable {
		m[spec.String()] = spec
	}
	return m
}()

// ParseRanking resolves a sealed ranking identity. Only the enumerated sealed
// values parse; any other string is unsupported and keeps its zero fields.
func ParseRanking(ranking string) (RankingSpec, bool) {
	spec, ok := rankingSpecsByName[ranking]
	return spec, ok
}

// SupportedRanking reports whether a retained package's ranking identity is
// one the compiler can reproduce.
func SupportedRanking(ranking string) bool {
	_, ok := ParseRanking(ranking)
	return ok
}

// AllRankingSpecs returns a copy of the sealed ranking table.
func AllRankingSpecs() []RankingSpec {
	return slices.Clone(rankingSpecTable)
}

// String renders the sealed identity. The version number is the tier plus one,
// except that plain lexical versions carry their tokenizer generation, lexical
// tiers follow generation 4 as versions 5–7, and the compound/indexed IDF
// aliases add four. The sealed bytes are proven by the round-trip test.
func (s RankingSpec) String() string {
	version := int(s.Tier) + 1
	switch s.Channel {
	case RankingLexical:
		if s.Tier == RankingTierPlain {
			version = s.Tokenizer
		} else {
			version = int(s.Tier) + 4
		}
	case RankingInterleaved:
		if s.IndexedIDF {
			version += 4
		}
	case RankingBinaryIDF:
		if s.CompoundTerms {
			version += 4
		}
	}
	return rankingChannelNames[s.Channel] + rankingSuffix + strconv.Itoa(version)
}

// rankingChannelOf classifies a string by its channel prefix without requiring
// a supported version, so replay can refuse an unsupported indexed identity
// explicitly instead of treating it as a fresh search.
func rankingChannelOf(ranking string) (RankingChannel, bool) {
	for channel, name := range rankingChannelNames {
		if strings.HasPrefix(ranking, name+rankingSuffix) {
			return RankingChannel(channel), true
		}
	}
	return 0, false
}

// rankingSpec is the base identity of a channel at a tier: generation 4 terms,
// no compound or indexed IDF evidence.
func rankingSpec(channel RankingChannel, tier RankingTier) RankingSpec {
	return RankingSpec{Channel: channel, Tier: tier, Tokenizer: 4}
}

// lexicalRanking is a plain lexical identity at a historical tokenizer generation.
func lexicalRanking(generation int) RankingSpec {
	return RankingSpec{Channel: RankingLexical, Tier: RankingTierPlain, Tokenizer: generation}
}

func (s RankingSpec) withCompoundTerms() RankingSpec {
	s.CompoundTerms = true
	return s
}

func (s RankingSpec) withIndexedIDF() RankingSpec {
	s.CompoundTerms = true
	s.IndexedIDF = true
	return s
}

// withoutCompoundTerms is the historical outer identity whose tier and preview
// policy an adjacent-compound IDF identity retains.
func (s RankingSpec) withoutCompoundTerms() RankingSpec {
	s.CompoundTerms = false
	s.IndexedIDF = false
	return s
}

// rankingTierOf is the tier of a sealed identity; unsupported strings are plain.
func rankingTierOf(ranking string) RankingTier {
	spec, _ := ParseRanking(ranking)
	return spec.Tier
}

// rankingTierForIntent selects the outer tier a search intent earns: entity
// associations outrank a failure signature, which outranks quoted literals.
func rankingTierForIntent(entities []EntityRef, signature string, literals []string) RankingTier {
	tier := RankingTierPlain
	if len(literals) > 0 {
		tier = RankingTierLiteral
	}
	if signature != "" {
		tier = RankingTierFailure
	}
	if len(entities) > 0 {
		tier = RankingTierEntity
	}
	return tier
}
