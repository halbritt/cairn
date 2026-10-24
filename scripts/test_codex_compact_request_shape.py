import unittest

import check_codex_compact_request_shape as probe


class CodexRequestShapeTests(unittest.TestCase):
    def test_extracts_tools_from_exact_additional_tools_input_position(self):
        descriptor = {"type": "namespace", "name": "cairn", "tools": [
            {"type": "function", "name": "cairn_search", "parameters": {"type": "object"}}]}
        request = {"type": "additional_tools", "role": "developer", "tools": [descriptor]}
        found, byte_count = probe.request_declarations({"input": [
            {"type": "message", "content": [{"text": "discarded"}]},
            request]})
        self.assertEqual(found, [descriptor])
        self.assertEqual(byte_count, len(probe.canonical(request)))

    def test_refuses_missing_or_duplicate_additional_tools_items(self):
        for inputs in ([], [{"type": "additional_tools", "tools": []},
                            {"type": "additional_tools", "tools": []}]):
            with self.subTest(inputs=len(inputs)), self.assertRaises(ValueError):
                probe.request_declarations({"input": inputs})

    def test_inspects_nested_model_tool_names_for_cairn_declarations(self):
        namespaces = [{"name": "functions", "tools": [{"name": "exec", "type": "custom"}]},
                      {"name": "clock", "tools": [{"name": "sleep", "type": "function"}]}]
        self.assertEqual(probe.nested_tool_names(namespaces), ["exec", "sleep"])
        namespaces[0]["tools"].append({"name": "mcp__cairn__cairn_search"})
        self.assertIn("cairn", " ".join(probe.nested_tool_names(namespaces)))

    def test_validation_contract_ignores_prose_but_keeps_constraints(self):
        before = {"type": "object", "description": "Long description",
                  "properties": {"query": {"type": "string", "description": "Query"}},
                  "required": ["query"], "additionalProperties": False}
        after = {"type": "object", "description": "Short description",
                 "properties": {"query": {"type": "string", "description": "Q"}},
                 "required": ["query"], "additionalProperties": False}
        self.assertEqual(probe.validation_contract(before), probe.validation_contract(after))
        changed = {**after, "required": []}
        self.assertNotEqual(probe.validation_contract(before), probe.validation_contract(changed))


if __name__ == "__main__":
    unittest.main()
