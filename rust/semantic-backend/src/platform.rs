//! Actual read-only CPU observations when construction is separately executed.
//! A bounded feature subset is explicitly partial, never a complete shared CPU class.
pub(crate) fn observe_cpu() -> serde_json::Value {
    let mut features = Vec::<&str>::new();
    #[cfg(target_arch = "x86_64")]
    {
        macro_rules! observe { ($($name:tt),* $(,)?) => { $(if std::arch::is_x86_feature_detected!($name) { features.push($name); })* }; }
        observe!(
            "aes",
            "avx",
            "avx2",
            "avx512f",
            "avx512bw",
            "avx512vl",
            "avx512dq",
            "bmi1",
            "bmi2",
            "f16c",
            "fma",
            "lzcnt",
            "pclmulqdq",
            "popcnt",
            "rdrand",
            "rdseed",
            "sha",
            "sse",
            "sse2",
            "sse3",
            "sse4.1",
            "sse4.2",
            "ssse3"
        );
    }
    let features = normalized_features(features);
    serde_json::json!({ "architecture": std::env::consts::ARCH, "endianness": if cfg!(target_endian="little") { "little" } else { "big" }, "pointer_width": usize::BITS, "os_enabled_vector_features": features, "coverage": "partial Rust CPU/OS feature-detection subset; complete class unresolved" })
}

pub(crate) fn normalized_features(mut features: Vec<&str>) -> Vec<&str> {
    features.sort_unstable();
    features.dedup();
    features
}
