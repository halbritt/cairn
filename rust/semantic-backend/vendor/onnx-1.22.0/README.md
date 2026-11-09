# Selected ONNX schema provenance

`src/model_schema.rs` is a source-derived field/child map, not generated bindings
or an executable protobuf parser dependency. ORT v1.29.0 `cmake/deps.txt:40`
selects ONNX v1.22.0. Its archive SHA-1 entry is a declaration, not a verified
commit or acquired archive. Only these nine exact official text sources were
acquired once; no archive, package or toolchain was fetched.

| Official selected source | Bytes | SHA-256 |
| --- | ---: | --- |
| [microsoft/onnxruntime@v1.29.0/cmake/deps.txt](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.29.0/cmake/deps.txt) | 7901 | `a088d88e42772db22c353006a8053ad7bb5372912500ae76f7d52e22390a8fd1` |
| [microsoft/onnxruntime@v1.29.0/onnxruntime/core/framework/tensorprotoutils.cc](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.29.0/onnxruntime/core/framework/tensorprotoutils.cc) | 143079 | `2d0af568b9086b5a7549e9e4186ae8576a4ba5a30f867e91d577798dd6c08033` |
| [microsoft/onnxruntime@v1.29.0/onnxruntime/core/framework/tensorprotoutils.h](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.29.0/onnxruntime/core/framework/tensorprotoutils.h) | 36350 | `cab5b81c71ff055e5176a1c0838f8dbaaed49506d410d0899a5cf1dedc6766e9` |
| [microsoft/onnxruntime@v1.29.0/onnxruntime/core/graph/model.cc](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.29.0/onnxruntime/core/graph/model.cc) | 44883 | `302268d30493e999196398b6d03ac998da7e42029dada5063d30d2ef830ae679` |
| [microsoft/onnxruntime@v1.29.0/LICENSE](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.29.0/LICENSE) | 1073 | `2f07c72751aed99790b8a4869cf2311df85a860b22ded05fa22803587a48922c` |
| [onnx/onnx@v1.22.0/onnx/onnx.proto](https://raw.githubusercontent.com/onnx/onnx/v1.22.0/onnx/onnx.proto) | 42918 | `a05cfbcd1370608b809c5b84c44e3198d3369036458e0b5f297e76ceaf9c4e1b` |
| [onnx/onnx@v1.22.0/onnx/onnx-ml.proto](https://raw.githubusercontent.com/onnx/onnx/v1.22.0/onnx/onnx-ml.proto) | 43381 | `f5996bda8d5c46d29e9ea5dd5f56465dfa7e98c22cb31529ebcbce7cbe9bbaba` |
| [onnx/onnx@v1.22.0/LICENSE](https://raw.githubusercontent.com/onnx/onnx/v1.22.0/LICENSE) | 11358 | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |
| [microsoft/onnxruntime@v1.29.0/onnxruntime/core/session/inference_session.cc](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.29.0/onnxruntime/core/session/inference_session.cc) | 211213 | `031d1083292eb2bb671d67f86dfacee131e26c8a855ac6f5e56c66715546c4b7` |

The complete reachable set is ModelProto, GraphProto, NodeProto, AttributeProto,
TensorProto, SparseTensorProto, TrainingInfoProto and FunctionProto. Both exact
`onnx.proto` and `onnx-ml.proto` agree on these fields and child relations. The
retained schema-selection and schema-ml-comparison receipts record every field,
not just tensor field names. ONNX's Apache-2.0 LICENSE accompanies this map.

ORT `model.cc:733-745` parses the supplied model buffer. The selected
`tensorprotoutils.h:553-557` external-data predicate requires EXTERNAL location;
its implementation validates external paths. The observer conservatively holds
every EXTERNAL tensor, including malformed/undefined-data cases. Metadata under
DEFAULT does not alone create consumed external tensor files.

The private observer checks wire offsets/lengths and ten-byte varint overflow,
uses depth128, one million fields and i32::MAX bytes as explicit observation
bounds, merges singular messages and preserves repeated ones. Every unknown
field in a traversed message is pending. Known leaf messages cannot contain a
TensorProto under the exact schema and remain opaque. Missing graph/IR, IR>13,
unknown locations, unsupported wire groups and ORT-format input stay pending.
The conventional ORTM identifier guard is conservative; no complete ORT-format
schema proof is claimed. No tensor filename heuristic establishes closure.

A successful closure establishes absence of external tensor files across all
known schema paths and binds the exact consumed model bytes. It does not prove
generic protobuf/model validity, custom-operator I/O safety, model acceptance,
or absence of other runtime effects. Actual selected model bytes have not been
observed here. If they contain external data, the dependent next source action
is an owned-byte/file mapping into the exact ORT session consumption path. The
full campaign domain and runtime gates remain open; unsupported legitimate
input is not silently relabeled as complete.
