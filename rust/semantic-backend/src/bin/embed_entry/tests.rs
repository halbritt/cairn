//! Pure entry fixtures; production main/run_native and native loading excluded.
use super::*;
use cleanup::{with_cleanup, CleanupReport};
use std::{
    cell::RefCell,
    io::{Cursor, Read},
    path::PathBuf,
    rc::Rc,
};

fn args() -> Vec<OsString> {
    [
        "--model-dir",
        "/selected/model",
        "--ort-runtime",
        "/selected/ort.so",
        "--openblas",
        "/selected/blas.so",
        "--gfortran",
        "/selected/fortran.so",
        "--quadmath",
        "/selected/quad.so",
        "--source-root",
        "/selected/source",
    ]
    .into_iter()
    .map(OsString::from)
    .collect()
}

#[test]
fn embedding_entry6_plan_binds_all_six_explicit_paths() {
    let paths = embedding_paths(args()).unwrap();
    assert_eq!(
        [
            paths.model,
            paths.config,
            paths.tokenizer_config,
            paths.special_tokens_map,
            paths.tokenizer,
            paths.ort_runtime,
            paths.math.openblas,
            paths.math.gfortran,
            paths.math.quadmath,
            paths.source_root
        ],
        [
            "/selected/model/model_optimized.onnx",
            "/selected/model/config.json",
            "/selected/model/tokenizer_config.json",
            "/selected/model/special_tokens_map.json",
            "/selected/model/tokenizer.json",
            "/selected/ort.so",
            "/selected/blas.so",
            "/selected/fortran.so",
            "/selected/quad.so",
            "/selected/source"
        ]
        .map(PathBuf::from)
    );
}

#[test]
fn embedding_entry6_plan_accepts_reordered_paths_with_spaces() {
    let selected = [
        "--source-root",
        "/source root",
        "--quadmath",
        "/q",
        "--gfortran",
        "/g",
        "--openblas",
        "/b",
        "--ort-runtime",
        "/o",
        "--model-dir",
        "/model dir",
    ];
    let paths = embedding_paths(selected.into_iter().map(OsString::from)).unwrap();
    assert_eq!(
        paths.model,
        PathBuf::from("/model dir/model_optimized.onnx")
    );
    assert_eq!(paths.source_root, PathBuf::from("/source root"));
}

#[test]
fn embedding_entry6_invalid_plans_cannot_dispatch_or_initialize() {
    let mut cases = vec![
        (vec![], PlanError::MissingFlag("--model-dir")),
        (
            vec!["--model-dir".into()],
            PlanError::MissingValue("--model-dir"),
        ),
    ];
    for (index, name) in [
        "--model-dir",
        "--ort-runtime",
        "--openblas",
        "--gfortran",
        "--quadmath",
        "--source-root",
    ]
    .into_iter()
    .enumerate()
    {
        let mut missing = args();
        missing.drain(index * 2..index * 2 + 2);
        cases.push((missing, PlanError::MissingFlag(name)));
        let mut relative = args();
        relative[index * 2 + 1] = "relative".into();
        cases.push((
            relative,
            PlanError::RelativePath(name, PathBuf::from("relative")),
        ));
        let mut duplicate = args();
        duplicate.extend([OsString::from(name), OsString::from("/other")]);
        cases.push((duplicate, PlanError::Duplicate(name)));
    }
    for unknown in [
        "--unknown",
        "/extra",
        "--model-dir=/selected/model",
        "--stream=1",
    ] {
        let mut selected = args();
        selected.push(unknown.into());
        cases.push((selected, PlanError::UnknownArgument(unknown.into())));
    }
    for (selected, expected) in cases {
        let error = dispatch::<()>(selected, |_| {
            panic!("invalid plan reached native invocation")
        })
        .unwrap_err();
        let EntryFailure::Plan(actual) = error else {
            panic!("expected plan refusal")
        };
        assert_eq!(actual, expected);
    }
}

#[test]
fn embedding_entry6_rank_stream_switch_is_unavailable() {
    let mut selected = args();
    selected.push("--stream".into());
    let error = embedding_paths(selected).unwrap_err();
    assert_eq!(error, PlanError::UnknownArgument("--stream".into()));
}

#[test]
fn embedding_entry6_os_path_bytes_survive_without_utf8_conversion() {
    use std::os::unix::ffi::{OsStrExt, OsStringExt};
    let mut selected = args();
    selected[1] = OsString::from_vec(b"/model-\xff".to_vec());
    let paths = embedding_paths(selected).unwrap();
    assert_eq!(
        paths.model.as_os_str().as_bytes(),
        b"/model-\xff/model_optimized.onnx"
    );
}

type Trace = Rc<RefCell<Vec<&'static str>>>;
struct FixtureOwner(Trace);
impl FixtureOwner {
    fn initialize(trace: &Trace) -> Self {
        trace.borrow_mut().push("initialize");
        Self(trace.clone())
    }
}
impl Drop for FixtureOwner {
    fn drop(&mut self) {
        self.0.borrow_mut().push("owner-drop");
    }
}
#[derive(Debug)]
struct FixtureCleanup(Vec<&'static str>);
impl CleanupReport for FixtureCleanup {
    fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

#[test]
fn embedding_entry6_eof_fixture_owner_drops_before_cleanup_collection() {
    let trace: Trace = Rc::new(RefCell::new(vec![]));
    let result = dispatch(args(), |paths| {
        assert_eq!(paths.ort_runtime, PathBuf::from("/selected/ort.so"));
        with_cleanup(
            || {
                let _owner = FixtureOwner::initialize(&trace);
                let mut input = Cursor::new(Vec::<u8>::new());
                let mut byte = [0];
                assert_eq!(input.read(&mut byte).unwrap(), 0);
                trace.borrow_mut().push("read-eof");
                Ok::<(), &str>(())
            },
            || {
                trace.borrow_mut().push("collect");
                FixtureCleanup(vec![])
            },
        )
    });
    result.unwrap();
    assert_eq!(
        *trace.borrow(),
        ["initialize", "read-eof", "owner-drop", "collect"]
    );
}

#[test]
fn embedding_entry6_initialization_and_cleanup_failures_are_both_retained() {
    let trace: Trace = Rc::new(RefCell::new(vec![]));
    let result = dispatch(args(), |_| {
        with_cleanup(
            || {
                let _owner = FixtureOwner::initialize(&trace);
                Err::<(), _>("fixture initialization failure")
            },
            || {
                trace.borrow_mut().push("collect");
                FixtureCleanup(vec!["fixture cleanup failure"])
            },
        )
    });
    let EntryFailure::Invocation(error) = result.unwrap_err() else {
        panic!("expected invocation refusal")
    };
    assert_eq!(error.invocation, Some("fixture initialization failure"));
    assert_eq!(error.cleanup.0, ["fixture cleanup failure"]);
    assert_eq!(*trace.borrow(), ["initialize", "owner-drop", "collect"]);
}

#[test]
fn embedding_entry6_cleanup_failure_after_success_is_not_hidden() {
    let error = with_cleanup(
        || Ok::<(), &str>(()),
        || FixtureCleanup(vec!["cleanup-only"]),
    )
    .unwrap_err();
    assert_eq!(error.invocation, None);
    assert_eq!(error.cleanup.0, ["cleanup-only"]);
}

#[test]
fn embedding_entry6_native_scope_callback_failure_never_loads_assets() {
    // invoke_native creates only configuration + empty cleanup observers here;
    // the callback never calls NativeFactory::load or EmbeddingFactory::construct.
    let result = dispatch(args(), |paths| {
        invoke_native(paths, |factory| {
            assert!(factory.cleanup_errors().ort.drain().is_empty());
            assert!(factory.cleanup_errors().math.drain().is_empty());
            Err::<(), _>("synthetic callback refusal")
        })
    });
    let EntryFailure::Invocation(error) = result.unwrap_err() else {
        panic!("expected invocation refusal")
    };
    assert_eq!(error.invocation, Some("synthetic callback refusal"));
    assert!(error.cleanup.is_empty());
}

#[test]
fn embedding_entry6_invocation_diagnostic_excludes_owned_request_bytes() {
    let error = InvocationError {
        failure: cairn_semantic_backend::embedding_response::Failure::Backend(
            cairn_semantic_rank::backend::BackendError::Unavailable("fixture-init".into()),
        ),
        input: b"PRIVATE_REQUEST".to_vec(),
        written: 7,
    };
    assert_eq!(
        invocation_diagnostic(&error),
        "embedding invocation failed after 7 bytes: Backend(Unavailable(\"fixture-init\"))"
    );
}
