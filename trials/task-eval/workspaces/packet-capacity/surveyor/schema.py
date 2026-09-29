RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "finding": {"type": "string"},
        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        "navigation": {"type": "object", "properties": {"file": {"type": "string"}, "line": {"type": "integer"}}},
        "recovery": {"type": "object", "properties": {"retry": {"type": "boolean"}, "hint": {"type": "string"}}},
    },
    "required": ["finding", "severity"],
}
