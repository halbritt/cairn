// Build-time fixture only. Observe the existing public NativeFactory report.
// The probe's binary hash differs from each worker; the Python fixture projects
// only that field, and must match the actual worker's protocol identity.
use cairn_semantic_backend::{assets::AssetPaths, CleanupErrors, NativeFactory};
use std::path::PathBuf;

fn main() {
    let root = PathBuf::from(std::env::args_os().nth(1).expect("absolute payload"));
    let cleanup = CleanupErrors::default();
    let factory = NativeFactory::new(
        AssetPaths {
            model: root.join("model/model_optimized.onnx"),
            config: root.join("model/config.json"),
            tokenizer_config: root.join("model/tokenizer_config.json"),
            special_tokens_map: root.join("model/special_tokens_map.json"),
            tokenizer: root.join("model/tokenizer.json"),
            ort_runtime: root.join("native/ort"),
            math: cairn_semantic_math::LibraryPaths {
                openblas: root.join("native/openblas"),
                gfortran: root.join("native/gfortran"),
                quadmath: root.join("native/quadmath"),
            },
            source_root: root.join("source"),
        },
        cleanup.clone(),
    );
    let loaded = factory.load().expect("observe real local model");
    println!(
        "{}",
        serde_json::json!({
            "rank_template": loaded.observations().rank_manifest().expect("rank manifest"),
            "embed_template": loaded.observations().embedding_manifest().expect("embed manifest"),
            "method": "public NativeFactory report from separately built probe; binary field requires verified worker projection"
        })
    );
    drop(loaded);
    drop(factory);
    assert!(cleanup.ort.drain().is_empty(), "ORT cleanup");
    assert!(cleanup.math.drain().is_empty(), "math cleanup");
}
