// Private synthetic report data, never a claim of actual native observations.
mod response_tests;
use super::super::RunningImageProvenance;
use super::*;
use std::path::PathBuf;

const RANK_CANONICAL: &str = r###"{"algorithms":{"cache":"first-pass-bits/exact-occurrences/no-renormalization","dtype":"float32","first_epsilon_decimal":"0.000000000001","matvec":["M0-empty","M1-sdot384-plus-positive-zero","M>=2-one-sgemv"],"max_passages":128,"max_tokens":512,"model_feed":"batch-one-int64-ids/mask/zero-type-ids","normalizations":["squares/pairwise128x8/+0/sqrt/maximum-epsilon/divide","full-occurrence-squares/pairwise128x8/+0/sqrt/all-zero-before-divide"],"note_reduce":"ordered-max-from-minus-one/f64-clamp-scale/python-ties-even","pooling":"ORT metadata-ordered run(None) result[0]; rank3 CLS[:,0],rank2 direct","prefix":"Represent this sentence for searching relevant passages: ","rank":"bge-max-chunk/1","rank_tokenizer":"prepared-serialize-deserialize/no-padding/no-truncation","specials":"rank-default-or-disabled/decode-keeps-special/model-default","stride":320,"tokenizer_release":"0.23.2","width":384,"window":384},"assets":[{"bytes":9,"name":"model.onnx","sha256":"1111111111111111111111111111111111111111111111111111111111111111"},{"bytes":11,"name":"tokenizer.json","sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}],"assets_model_external_data_closure":{"kind":"ONNX-v1.22.0-all-TensorProto-paths-self-contained","model_sha256":"1111111111111111111111111111111111111111111111111111111111111111"},"compilation":{"binary_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","effective_flags":{"fixture:target":["-Copt-level=2","-Ctarget-cpu=x86-64"]},"enabled_features":{"fixture:target":["a","b"]},"linked_input_attestation":{"binary_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","source_inputs":[{"path":"rust/fixture.rs","sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}]},"rustc":"fixture-rustc","target":"x86_64-unknown-linux-gnu"},"implementation":{"rust/fixture.rs":{"bytes":7,"sha256":"2222222222222222222222222222222222222222222222222222222222222222"}},"native":{"cpu_kernel_class":{"observed_inputs":{"features":["a","b"]},"sha256":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"},"math_modules":[{"role":"fixture-math","sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}],"ort":{"api":29,"mapped_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","selected_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","version":"fixture-ORT"},"ort_dependency_inventory":[{"sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}],"parallel":0,"threads":2},"prepared_model_tokenizer_sha256":"3333333333333333333333333333333333333333333333333333333333333333","session":{"batch_size":1,"cpu_arena":true,"effective_provider":"fixture-provider","inputs":[{"dimensions":[1,384],"name":"z"},{"dimensions":[1,384],"name":"a"}],"inter_op_cap":2,"intra_op_cap":2,"remaining_defaults":{"fixture":"observed"}},"tag":"cairn.rust-rank-worker/1"}"###;
const EMBED_CANONICAL: &str = r###"{"algorithms":{"dimensions":384,"document_projection":{"adaptive_stride":"max(96,ceil(offsets.len/256))","adaptive_window":"max(128,stride+32)","final_short_tail":true,"instruction_prefix":false,"max_passages":256,"max_utf8_bytes":65536,"offsets":"original-Python-characters/first-and-last-before-clipping","passage_max_tokens":512,"spans":"original-UTF8-prefix-offset/selected-original-UTF8-length","stride":96,"validation":"all-spans/count-before-ordered-default-special-reencode","window":128},"dtype":"float32","embedding":"bge-original-passages/1","first_epsilon_decimal":"0.000000000001","max_tokens":512,"model_feed":"batch-one-int64-ids/mask/zero-type-ids","normalizations":["squares/pairwise128x8/+0/sqrt/maximum-epsilon/divide"],"pooling":"ORT metadata-ordered run(None) result[0]; rank3 CLS[:,0],rank2 direct","prefix":"Represent this sentence for searching relevant passages: ","query_projection":{"acceptance":"Python-nonblank-and-at-most-512-tokens","initial_end":511,"max_retreats":8,"max_tokens":512,"max_utf8_bytes":4096,"metadata":"full-original-and-embedded-counts/strict-UTF8-prefix-bytes/SHA256-excludes-instruction","method":"original-prefix/1","offsets":"original-Python-characters/nonempty-max-stop-minus-instruction-length","prefix":"strict-positive-proper-original-character-prefix","reencode":"exact-instruction-plus-original-prefix/default-specials","reserve_final_special":true,"retreat":"end-=max(1,embedded_tokens-512);Python-signed-slice-end"},"rank_tokenizer":"prepared-serialize-deserialize/no-padding/no-truncation","specials":"query-default/document-body-disabled/passage-default/model-default","tokenizer_release":"0.23.2","vector_validation":{"dimensions":384,"finite":true,"nonzero":true},"width":384},"assets":[{"bytes":9,"name":"model.onnx","sha256":"1111111111111111111111111111111111111111111111111111111111111111"},{"bytes":11,"name":"tokenizer.json","sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}],"assets_model_external_data_closure":{"kind":"ONNX-v1.22.0-all-TensorProto-paths-self-contained","model_sha256":"1111111111111111111111111111111111111111111111111111111111111111"},"compilation":{"binary_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","effective_flags":{"fixture:target":["-Copt-level=2","-Ctarget-cpu=x86-64"]},"enabled_features":{"fixture:target":["a","b"]},"linked_input_attestation":{"binary_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","source_inputs":[{"path":"rust/fixture.rs","sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}]},"rustc":"fixture-rustc","target":"x86_64-unknown-linux-gnu"},"implementation":{"rust/fixture.rs":{"bytes":7,"sha256":"2222222222222222222222222222222222222222222222222222222222222222"}},"native":{"cpu_kernel_class":{"observed_inputs":{"features":["a","b"]},"sha256":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"},"math_modules":[{"role":"fixture-math","sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}],"ort":{"api":29,"mapped_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","selected_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","version":"fixture-ORT"},"ort_dependency_inventory":[{"sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}],"parallel":0,"threads":2},"prepared_model_tokenizer_sha256":"3333333333333333333333333333333333333333333333333333333333333333","session":{"batch_size":1,"cpu_arena":true,"effective_provider":"fixture-provider","inputs":[{"dimensions":[1,384],"name":"z"},{"dimensions":[1,384],"name":"a"}],"inter_op_cap":2,"intra_op_cap":2,"remaining_defaults":{"fixture":"observed"}},"tag":"cairn.rust-embedding-worker/1"}"###;
const RANK_DIGEST: &str = "c7caa5cabe5cbcf929070150af1e5d593412a392f6a37370e45cb676068a0b30";
const EMBED_DIGEST: &str = "2faee8408e4b9954848eea5e706bc8fa4a2c71fbebd52b8228921777bbeb80c6";

fn fixture() -> ConstructionReport {
    ConstructionReport {
        inputs: serde_json::from_str(RANK_CANONICAL).unwrap(),
        missing: vec![],
        provenance: vec![PathBuf::from("/fixture/2026-01-01/model")],
        running_image: Some(RunningImageProvenance {
            device: 1,
            inode: 2,
            bytes: 3,
            sha256: "a".repeat(64),
        }),
    }
}

fn remove(inputs: &mut Value, pointer: &str) {
    let (parent, key) = pointer.rsplit_once('/').unwrap();
    inputs
        .pointer_mut(parent)
        .unwrap()
        .as_object_mut()
        .unwrap()
        .remove(key);
}

fn identity(manifest: &Value) -> String {
    digest(&canonical(manifest).unwrap())
}

#[test]
fn embedding_identity4_exact_manifest_bytes_and_literal_digest() {
    let report = fixture();
    assert_eq!(
        canonical(&report.embedding_manifest().unwrap()).unwrap(),
        EMBED_CANONICAL.as_bytes()
    );
    assert_eq!(
        report.embedding_identity().unwrap(),
        PyString::from(EMBED_DIGEST)
    );
}

#[test]
fn embedding_identity4_rank_canonical_bytes_and_digest_are_unchanged() {
    let report = fixture();
    assert_eq!(
        canonical(report.inputs()).unwrap(),
        RANK_CANONICAL.as_bytes()
    );
    assert_eq!(report.rank_identity().unwrap(), PyString::from(RANK_DIGEST));
    assert_ne!(
        report.rank_identity().unwrap(),
        report.embedding_identity().unwrap()
    );
    assert_eq!(
        canonical(report.inputs()).unwrap(),
        RANK_CANONICAL.as_bytes()
    );
}

#[test]
fn embedding_identity4_common_observations_are_retained_exactly() {
    let report = fixture();
    let embedded = report.embedding_manifest().unwrap();
    for (key, value) in report.inputs.as_object().unwrap() {
        if key != "tag" && key != "algorithms" {
            assert_eq!(&embedded[key], value, "{key}");
        }
    }
    for key in [
        "prefix",
        "width",
        "max_tokens",
        "pooling",
        "first_epsilon_decimal",
        "dtype",
        "tokenizer_release",
        "rank_tokenizer",
        "model_feed",
    ] {
        assert_eq!(
            embedded["algorithms"][key], report.inputs["algorithms"][key],
            "{key}"
        );
    }
    assert_eq!(
        embedded["algorithms"]["normalizations"],
        json!(["squares/pairwise128x8/+0/sqrt/maximum-epsilon/divide"])
    );
    for key in [
        "window",
        "stride",
        "max_passages",
        "matvec",
        "rank",
        "note_reduce",
        "cache",
    ] {
        assert!(embedded["algorithms"].get(key).is_none(), "{key}");
    }
}

#[test]
fn embedding_identity4_each_required_observation_missing_null_and_invalid_refuses() {
    let rows = [
        ("/compilation/rustc", "compilation.rustc", json!(true)),
        ("/compilation/target", "compilation.target", json!(true)),
        (
            "/compilation/enabled_features",
            "compilation.enabled_features",
            json!({"unit":["b","a"]}),
        ),
        (
            "/compilation/effective_flags",
            "compilation.effective_flags",
            json!({"unit":true}),
        ),
        (
            "/compilation/linked_input_attestation",
            "compilation.linked_input_attestation",
            json!({"binary_sha256":"0".repeat(64),"source_inputs":[]}),
        ),
        (
            "/session/cpu_arena",
            "session.cpu_arena",
            json!("not-a-boolean"),
        ),
        (
            "/session/remaining_defaults",
            "session.remaining_defaults",
            json!({}),
        ),
        (
            "/session/effective_provider",
            "session.effective_provider",
            json!(true),
        ),
        (
            "/native/ort_dependency_inventory",
            "native.ort_dependency_inventory",
            json!([]),
        ),
        (
            "/native/cpu_kernel_class",
            "native.cpu_os_complete_class",
            json!({"sha256":"bad","observed_inputs":true}),
        ),
        (
            "/assets_model_external_data_closure",
            "assets.model_external_data_closure",
            json!({"kind":"digest-only","model_sha256":"1".repeat(64)}),
        ),
    ];
    // Eleven declared rules exercised on private fixtures, not measured gaps.
    for (pointer, field, invalid) in rows {
        for kind in 0..3 {
            let mut report = fixture();
            match kind {
                0 => remove(&mut report.inputs, pointer),
                1 => *report.inputs.pointer_mut(pointer).unwrap() = Value::Null,
                _ => *report.inputs.pointer_mut(pointer).unwrap() = invalid.clone(),
            }
            let rank = report.rank_identity().unwrap_err();
            let embed = report.embedding_identity().unwrap_err();
            assert!(
                embed.iter().any(|gap| gap.field == field),
                "{pointer} variant{kind}"
            );
            assert_eq!(embed, rank);
            assert_eq!(report.embedding_manifest().unwrap_err(), rank);
        }
    }
}

#[test]
fn embedding_identity4_cached_missing_and_recomputed_gaps_are_preserved() {
    let mut report = fixture();
    report
        .missing
        .push(missing("owner-pending", "retain original owner action"));
    report.inputs["session"]["cpu_arena"] = Value::Null;
    let expected = report.rank_identity().unwrap_err();
    assert_eq!(
        expected[0],
        missing("owner-pending", "retain original owner action")
    );
    assert_eq!(report.embedding_identity().unwrap_err(), expected);
    assert_eq!(report.missing.len(), 1);
}

#[test]
fn embedding_identity4_partial_io_records_cannot_fill_global_nulls() {
    let mut report = fixture();
    for key in ["cpu_arena", "remaining_defaults", "effective_provider"] {
        report.inputs["session"][key] = Value::Null;
    }
    report.inputs["session"]["partial_observations"] = json!({"cpu_arena":true,"remaining_defaults":{"ready":true},"effective_provider":"CPUExecutionProvider","outputs":[{"ep_device":{"name_hex":"435055"}}]});
    let errors = report.embedding_identity().unwrap_err();
    for field in [
        "session.cpu_arena",
        "session.remaining_defaults",
        "session.effective_provider",
    ] {
        assert!(errors.iter().any(|e| e.field == field));
    }
}

#[test]
fn embedding_identity4_model_closure_is_not_replaced_by_asset_digest() {
    let mut report = fixture();
    report.inputs["assets_model_external_data_closure"] = Value::Null;
    assert!(report
        .embedding_manifest()
        .unwrap_err()
        .iter()
        .any(|g| g.field == "assets.model_external_data_closure"));
    report.inputs["assets_model_external_data_closure"] =
        json!({"kind":"external-or-unknown","model_sha256":"1".repeat(64)});
    assert!(report.embedding_identity().is_err());
}

#[test]
fn embedding_identity4_common_material_changes_both_identities() {
    let original = fixture();
    let rows = [
        ("/implementation/rust~1fixture.rs/bytes", json!(8)),
        ("/assets/0/sha256", json!("9".repeat(64))),
        ("/prepared_model_tokenizer_sha256", json!("8".repeat(64))),
        ("/compilation/rustc", json!("another-fixture-rustc")),
        (
            "/compilation/effective_flags/fixture:target/0",
            json!("-Copt-level=3"),
        ),
        ("/session/intra_op_cap", json!(3)),
        ("/native/ort/mapped_sha256", json!("7".repeat(64))),
        (
            "/native/cpu_kernel_class/observed_inputs/features",
            json!(["a", "c"]),
        ),
        ("/algorithms/prefix", json!("different instruction ")),
        ("/algorithms/first_epsilon_decimal", json!("0.000000000002")),
        (
            "/algorithms/normalizations/0",
            json!("different-first-order"),
        ),
        ("/algorithms/pooling", json!("different-pooling")),
        ("/algorithms/model_feed", json!("different-layout")),
        ("/algorithms/tokenizer_release", json!("different-release")),
    ];
    for (pointer, value) in rows {
        let mut report = fixture();
        *report.inputs.pointer_mut(pointer).unwrap() = value;
        assert_ne!(
            report.embedding_identity().unwrap(),
            original.embedding_identity().unwrap(),
            "{pointer}"
        );
        assert_ne!(
            report.rank_identity().unwrap(),
            original.rank_identity().unwrap(),
            "{pointer}"
        );
    }
}

#[test]
fn embedding_identity4_rank_only_policy_changes_do_not_enter_embedding_mode() {
    let original = fixture();
    for (key, value) in [
        ("window", json!(999)),
        ("stride", json!(999)),
        ("max_passages", json!(999)),
        ("matvec", json!(["other"])),
        ("rank", json!("other-rank")),
        ("note_reduce", json!("other-reduce")),
        ("cache", json!("other-cache")),
        ("specials", json!("other-rank-specials")),
    ] {
        let mut report = fixture();
        report.inputs["algorithms"][key] = value;
        assert_ne!(
            report.rank_identity().unwrap(),
            original.rank_identity().unwrap()
        );
        assert_eq!(
            report.embedding_identity().unwrap(),
            original.embedding_identity().unwrap(),
            "{key}"
        );
    }
    let mut report = fixture();
    report.inputs["algorithms"]["normalizations"][1] = json!("other-second-normalization");
    assert_ne!(
        report.rank_identity().unwrap(),
        original.rank_identity().unwrap()
    );
    assert_eq!(
        report.embedding_identity().unwrap(),
        original.embedding_identity().unwrap()
    );
}

#[test]
fn embedding_identity4_mode_constants_affect_the_canonical_digest() {
    let report = fixture();
    let manifest = report.embedding_manifest().unwrap();
    for (pointer, value) in [
        ("/tag", json!("other-tag")),
        ("/algorithms/embedding", json!("other-algorithm")),
        ("/algorithms/dimensions", json!(385)),
        ("/algorithms/query_projection/max_utf8_bytes", json!(4097)),
        ("/algorithms/query_projection/max_tokens", json!(513)),
        ("/algorithms/query_projection/initial_end", json!(510)),
        ("/algorithms/query_projection/max_retreats", json!(9)),
        ("/algorithms/query_projection/method", json!("other-method")),
        (
            "/algorithms/query_projection/reserve_final_special",
            json!(false),
        ),
        (
            "/algorithms/query_projection/metadata",
            json!("different-prefix-hash"),
        ),
        (
            "/algorithms/document_projection/max_utf8_bytes",
            json!(65537),
        ),
        ("/algorithms/document_projection/window", json!(129)),
        ("/algorithms/document_projection/stride", json!(97)),
        (
            "/algorithms/document_projection/adaptive_stride",
            json!("other-ceil"),
        ),
        (
            "/algorithms/document_projection/adaptive_window",
            json!("other-window"),
        ),
        ("/algorithms/document_projection/max_passages", json!(257)),
        (
            "/algorithms/document_projection/final_short_tail",
            json!(false),
        ),
        (
            "/algorithms/document_projection/spans",
            json!("other-spans"),
        ),
        (
            "/algorithms/document_projection/passage_max_tokens",
            json!(513),
        ),
        (
            "/algorithms/document_projection/instruction_prefix",
            json!(true),
        ),
        ("/algorithms/vector_validation/finite", json!(false)),
        ("/algorithms/vector_validation/nonzero", json!(false)),
    ] {
        let mut changed = manifest.clone();
        *changed.pointer_mut(pointer).unwrap() = value;
        assert_ne!(identity(&changed), EMBED_DIGEST, "{pointer}");
    }
}

#[test]
fn embedding_identity4_paths_timestamp_components_and_inode_are_provenance_only() {
    let mut report = fixture();
    let original = report.embedding_identity().unwrap();
    report.provenance = vec![
        PathBuf::from("/moved/2030-04-05/library"),
        PathBuf::from("/different/model"),
    ];
    let image = report.running_image.as_mut().unwrap();
    image.device = 88;
    image.inode = 99;
    assert_eq!(report.embedding_identity().unwrap(), original);
    assert_eq!(report.rank_identity().unwrap(), PyString::from(RANK_DIGEST));
    let bytes =
        String::from_utf8(canonical(&report.embedding_manifest().unwrap()).unwrap()).unwrap();
    assert!(!bytes.contains("/moved"));
    assert!(!bytes.contains("2030-04-05"));
}

#[test]
fn embedding_identity4_object_keys_sort_but_sequences_keep_order_and_duplicates() {
    let original = fixture();
    let mut reversed = fixture();
    let entries: Vec<_> = reversed
        .inputs
        .as_object()
        .unwrap()
        .iter()
        .rev()
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    reversed.inputs = Value::Object(entries.into_iter().collect());
    assert_eq!(
        reversed.embedding_identity().unwrap(),
        original.embedding_identity().unwrap()
    );
    let mut flags = fixture();
    flags.inputs["compilation"]["effective_flags"]["fixture:target"] =
        json!(["-Ctarget-cpu=x86-64", "-Copt-level=2"]);
    assert_ne!(
        flags.embedding_identity().unwrap(),
        original.embedding_identity().unwrap()
    );
    let mut ordinals = fixture();
    ordinals.inputs["session"]["inputs"]
        .as_array_mut()
        .unwrap()
        .reverse();
    assert_ne!(
        ordinals.embedding_identity().unwrap(),
        original.embedding_identity().unwrap()
    );
    let mut duplicate = fixture();
    duplicate.inputs["compilation"]["effective_flags"]["fixture:target"] =
        json!(["-Copt-level=2", "-Copt-level=2", "-Ctarget-cpu=x86-64"]);
    assert_ne!(
        duplicate.embedding_identity().unwrap(),
        original.embedding_identity().unwrap()
    );
}

#[test]
fn embedding_identity4_feature_sets_refuse_unsorted_or_duplicate_observations() {
    for values in [json!(["b", "a"]), json!(["a", "a"])] {
        let mut report = fixture();
        report.inputs["compilation"]["enabled_features"]["fixture:target"] = values;
        assert!(report
            .embedding_identity()
            .unwrap_err()
            .iter()
            .any(|g| g.field == "compilation.enabled_features"));
    }
}

#[test]
fn embedding_identity4_floating_constants_require_decimal_strings() {
    let mut report = fixture();
    report.inputs["algorithms"]["first_epsilon_decimal"] = json!(1e-12);
    assert_eq!(
        report.embedding_identity().unwrap_err(),
        report.rank_identity().unwrap_err()
    );
    assert_eq!(
        report.embedding_identity().unwrap_err()[0].field,
        "canonical_manifest"
    );
    assert!(EMBED_CANONICAL.contains("\"first_epsilon_decimal\":\"0.000000000001\""));
}

#[test]
fn embedding_identity4_repeated_owned_projections_never_mutate_report_or_rank() {
    let report = fixture();
    let before = canonical(report.inputs()).unwrap();
    let rank = report.rank_identity().unwrap();
    let mut first = report.embedding_manifest().unwrap();
    first["assets"][0]["bytes"] = json!(999);
    assert_eq!(
        report.embedding_manifest().unwrap(),
        serde_json::from_str::<Value>(EMBED_CANONICAL).unwrap()
    );
    assert_eq!(
        report.embedding_identity().unwrap(),
        PyString::from(EMBED_DIGEST)
    );
    assert_eq!(canonical(report.inputs()).unwrap(), before);
    assert_eq!(report.rank_identity().unwrap(), rank);
    assert_eq!(
        report.provenance_paths(),
        [PathBuf::from("/fixture/2026-01-01/model")]
    );
}

#[test]
fn embedding_identity4_unknown_future_common_material_is_not_dropped() {
    let original = fixture();
    let mut report = fixture();
    report.inputs["future_common"] = json!({"value":17});
    report.inputs["algorithms"]["future_common_algorithm"] = json!(["first", "second"]);
    let manifest = report.embedding_manifest().unwrap();
    assert_eq!(manifest["future_common"], json!({"value":17}));
    assert_eq!(
        manifest["algorithms"]["future_common_algorithm"],
        json!(["first", "second"])
    );
    assert_ne!(
        report.embedding_identity().unwrap(),
        original.embedding_identity().unwrap()
    );
}

#[test]
fn embedding_identity4_missing_common_algorithm_structure_refuses_projection() {
    for replacement in [
        Value::Null,
        json!({}),
        json!({"normalizations":[]}),
        json!({"normalizations":[null]}),
        json!({"normalizations":[false]}),
    ] {
        let mut report = fixture();
        report.inputs["algorithms"] = replacement;
        assert_eq!(
            report.embedding_identity().unwrap_err()[0].field,
            "algorithms.embedding_common"
        );
    }
}
