package value

func Text(s string) *Value              { return &Value{Kind: String, String: []rune(s)} }
func Boolean(b bool) *Value             { return &Value{Kind: Bool, Boolean: b} }
func IntegerText(s string) *Value       { return &Value{Kind: Int, Integer: s} }
func Mapping(members ...Member) *Value  { return &Value{Kind: Object, Members: members} }
func Field(key string, v *Value) Member { return Member{Key: []rune(key), Value: v} }

func (v *Value) Set(key string, item *Value) {
	for i, m := range v.Members {
		if samePoints(m.Key, []rune(key)) {
			v.Members[i].Value = item
			return
		}
	}
	v.Members = append(v.Members, Field(key, item))
}

func (v *Value) Without(key string) *Value {
	result := &Value{Kind: v.Kind}
	for _, m := range v.Members {
		if !samePoints(m.Key, []rune(key)) {
			result.Members = append(result.Members, m)
		}
	}
	return result
}

func SameText(v *Value, s string) bool {
	return v != nil && v.Kind == String && samePoints(v.String, []rune(s))
}
func samePoints(a, b []rune) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func Truth(v *Value) bool {
	if v == nil {
		return false
	}
	switch v.Kind {
	case Null:
		return false
	case Bool:
		return v.Boolean
	case Int:
		return v.Integer != "0"
	case Float:
		return v.Number != 0
	case String:
		return len(v.String) != 0
	case Array:
		return len(v.Items) != 0
	case Object:
		return len(v.Members) != 0
	}
	return false
}
