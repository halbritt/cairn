fn main() {
    let target = std::env::var("TARGET").expect("Cargo supplies TARGET");
    assert_eq!(
        target, "x86_64-unknown-linux-gnu",
        "selected ORT wheel target only"
    );
    cc::Build::new()
        .file("native/bridge.c")
        .include("vendor/onnxruntime-1.29.0")
        .flag_if_supported("-std=c11")
        .warnings(true)
        .compile("cairn_semantic_ort_bridge");
    println!("cargo:rustc-link-lib=dl");
    for path in [
        "native/bridge.c",
        "native/bridge.h",
        "vendor/onnxruntime-1.29.0/onnxruntime_c_api.h",
        "vendor/onnxruntime-1.29.0/onnxruntime_ep_c_api.h",
        "vendor/onnxruntime-1.29.0/onnxruntime_error_code.h",
    ] {
        println!("cargo:rerun-if-changed={path}");
    }
}
