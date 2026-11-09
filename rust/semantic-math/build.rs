fn main() {
    assert_eq!(
        std::env::var("TARGET").expect("Cargo TARGET"),
        "x86_64-unknown-linux-gnu"
    );
    cc::Build::new()
        .file("native/bridge.c")
        .file("native/compute.c")
        .include("vendor/scipy-openblas64-0.3.31.188.0")
        .flag("-std=c11")
        .flag("-fno-fast-math")
        .flag("-ffp-contract=off")
        .warnings(true)
        .compile("cairn_semantic_math_bridge");
    println!("cargo:rustc-link-lib=dl");
    for path in [
        "native/bridge.c",
        "native/compute.c",
        "native/bridge.h",
        "native/internal.h",
        "vendor/scipy-openblas64-0.3.31.188.0/cblas.h",
        "vendor/scipy-openblas64-0.3.31.188.0/openblas_config.h",
    ] {
        println!("cargo:rerun-if-changed={path}");
    }
}
