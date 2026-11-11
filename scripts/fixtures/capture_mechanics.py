"""Manufactured AST trace targets; never a FastEmbed package or model oracle.

Keep the local/tensor names observed by _trace_reference. These functions run
only with the manufactured tokenizer/session and the test's supplied NumPy.
"""


def normalize(input_array, p=2, dim=1, eps=1e-12):
    norm = np.linalg.norm(input_array, ord=p, axis=dim, keepdims=True)
    norm = np.maximum(norm, eps)
    normalized_array = input_array / norm
    return normalized_array


def onnx_embed(self, documents, **kwargs):
    encoded = self.tokenize(documents, **kwargs)
    input_ids = np.array([item.ids for item in encoded])
    attention_mask = np.array([item.attention_mask for item in encoded])
    input_names = {item.name for item in self.model.get_inputs()}
    onnx_input = {"input_ids": np.array(input_ids, dtype=np.int64)}
    if "attention_mask" in input_names:
        onnx_input["attention_mask"] = np.array(attention_mask, dtype=np.int64)
    if "token_type_ids" in input_names:
        onnx_input["token_type_ids"] = np.array(
            [np.zeros(len(item), dtype=np.int64) for item in input_ids], dtype=np.int64
        )
    onnx_input = self._preprocess_onnx_input(onnx_input, **kwargs)
    model_output = self.model.run(self.ONNX_OUTPUT_NAMES, onnx_input)
    return OnnxOutputContext(
        model_output=model_output[0],
        attention_mask=onnx_input.get("attention_mask", attention_mask),
        input_ids=onnx_input.get("input_ids", input_ids),
    )
