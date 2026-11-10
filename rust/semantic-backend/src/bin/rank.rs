//! Source entry wiring only. Parsing a plan does not admit native/model execution.
#[path = "entry/cleanup.rs"]
mod cleanup;
#[path = "rank_entry/plan.rs"]
mod plan;

use cleanup::{invoke_native, NativeFailure};
#[cfg(test)]
use cleanup::{with_cleanup, CleanupReport};

use cairn_semantic_backend::assets::AssetPaths;
use cairn_semantic_protocol::CachePolicy;
use cairn_semantic_rank::{run_rank_once, run_rank_stream, InvocationError};
use plan::{LaunchPlan, Mode};
use std::{
    io::{self, BufRead, Write},
    process::ExitCode,
};

trait Driver {
    type Error;
    fn rank_once(&mut self, paths: AssetPaths) -> Result<(), Self::Error>;
    fn stream(&mut self, paths: AssetPaths, cache: CachePolicy) -> Result<(), Self::Error>;
}

fn dispatch<D: Driver>(plan: LaunchPlan, driver: &mut D) -> Result<(), D::Error> {
    match plan.mode {
        Mode::RankOnce => driver.rank_once(plan.paths),
        Mode::Stream { cache_enabled } => driver.stream(
            plan.paths,
            if cache_enabled {
                CachePolicy::AdvertisedCache
            } else {
                CachePolicy::NoCache
            },
        ),
    }
}

struct NativeDriver<'a, R, W> {
    input: &'a mut R,
    output: &'a mut W,
}
impl<R: BufRead, W: Write> Driver for NativeDriver<'_, R, W> {
    type Error = NativeFailure<InvocationError>;
    fn rank_once(&mut self, paths: AssetPaths) -> Result<(), Self::Error> {
        invoke_native(paths, |factory| {
            run_rank_once(self.input, self.output, factory)
        })
    }
    fn stream(&mut self, paths: AssetPaths, cache: CachePolicy) -> Result<(), Self::Error> {
        invoke_native(paths, |factory| {
            run_rank_stream(self.input, self.output, factory, cache)
        })
    }
}

fn main() -> ExitCode {
    let plan = match LaunchPlan::parse(
        std::env::args_os().skip(1),
        std::env::var_os("CAIRN_SEMANTIC_STREAM_CACHE"),
    ) {
        Ok(plan) => plan,
        Err(error) => {
            eprintln!("invalid rank launch plan: {error:?}");
            return ExitCode::from(2);
        }
    };
    let input = io::stdin();
    let output = io::stdout();
    let mut input = input.lock();
    let mut output = output.lock();
    match dispatch(
        plan,
        &mut NativeDriver {
            input: &mut input,
            output: &mut output,
        },
    ) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            if let Some(invocation) = error.invocation {
                // Retain the complete error internally; do not print its owned request bytes.
                eprintln!(
                    "rank invocation failed after {} bytes ({:?}): {:?}",
                    invocation.written, invocation.mode, invocation.failure
                );
            }
            for problem in error.cleanup.ort {
                eprintln!("ORT cleanup failed: {problem:?}");
            }
            for problem in error.cleanup.math {
                eprintln!("math cleanup failed: {problem:?}");
            }
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use plan::PlanError;
    use std::{cell::RefCell, ffi::OsString, path::PathBuf, rc::Rc};

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
    fn plan(mode: Mode) -> LaunchPlan {
        let mut plan = LaunchPlan::parse(args(), None).unwrap();
        plan.mode = mode;
        plan
    }

    #[test]
    fn rank_entry1_rank_plan_binds_all_explicit_paths() {
        let plan = LaunchPlan::parse(args(), Some("1".into())).unwrap();
        assert_eq!(plan.mode, Mode::RankOnce);
        assert_eq!(
            plan.paths.model,
            PathBuf::from("/selected/model/model_optimized.onnx")
        );
        assert_eq!(
            plan.paths.config,
            PathBuf::from("/selected/model/config.json")
        );
        assert_eq!(
            plan.paths.tokenizer_config,
            PathBuf::from("/selected/model/tokenizer_config.json")
        );
        assert_eq!(
            plan.paths.special_tokens_map,
            PathBuf::from("/selected/model/special_tokens_map.json")
        );
        assert_eq!(
            plan.paths.tokenizer,
            PathBuf::from("/selected/model/tokenizer.json")
        );
        assert_eq!(plan.paths.ort_runtime, PathBuf::from("/selected/ort.so"));
        assert_eq!(plan.paths.math.openblas, PathBuf::from("/selected/blas.so"));
        assert_eq!(
            plan.paths.math.gfortran,
            PathBuf::from("/selected/fortran.so")
        );
        assert_eq!(plan.paths.math.quadmath, PathBuf::from("/selected/quad.so"));
        assert_eq!(plan.paths.source_root, PathBuf::from("/selected/source"));
    }

    #[test]
    fn rank_entry1_stream_cache_uses_only_exact_one() {
        for (value, enabled) in [
            (None, false),
            (Some(""), false),
            (Some("0"), false),
            (Some("01"), false),
            (Some("true"), false),
            (Some("1"), true),
        ] {
            let mut selected = args();
            selected.push("--stream".into());
            let plan = LaunchPlan::parse(selected, value.map(OsString::from)).unwrap();
            assert_eq!(
                plan.mode,
                Mode::Stream {
                    cache_enabled: enabled
                }
            );
        }
    }

    #[test]
    fn rank_entry1_flags_accept_arbitrary_order_and_path_spaces() {
        let selected = [
            "--stream",
            "--source-root",
            "/source root",
            "--quadmath",
            "/q",
            "--model-dir",
            "/model dir",
            "--gfortran",
            "/g",
            "--ort-runtime",
            "/o",
            "--openblas",
            "/b",
        ];
        let plan = LaunchPlan::parse(selected.into_iter().map(OsString::from), None).unwrap();
        assert_eq!(
            plan.mode,
            Mode::Stream {
                cache_enabled: false
            }
        );
        assert_eq!(
            plan.paths.model,
            PathBuf::from("/model dir/model_optimized.onnx")
        );
        assert_eq!(plan.paths.source_root, PathBuf::from("/source root"));
        assert_eq!(plan.paths.math.quadmath, PathBuf::from("/q"));
    }

    #[test]
    fn rank_entry1_missing_flags_and_values_refuse() {
        assert_eq!(
            LaunchPlan::parse(Vec::new(), None).unwrap_err(),
            PlanError::MissingFlag("--model-dir")
        );
        assert_eq!(
            LaunchPlan::parse(vec![OsString::from("--model-dir")], None).unwrap_err(),
            PlanError::MissingValue("--model-dir")
        );
        for index in (0..12).step_by(2) {
            let mut selected = args();
            let flag = selected[index].clone();
            selected.drain(index..index + 2);
            let missing = match flag.to_str().unwrap() {
                "--model-dir" => "--model-dir",
                "--ort-runtime" => "--ort-runtime",
                "--openblas" => "--openblas",
                "--gfortran" => "--gfortran",
                "--quadmath" => "--quadmath",
                _ => "--source-root",
            };
            assert_eq!(
                LaunchPlan::parse(selected, None).unwrap_err(),
                PlanError::MissingFlag(missing)
            );
        }
    }

    #[test]
    fn rank_entry1_duplicates_and_unknown_arguments_refuse() {
        let mut duplicate = args();
        duplicate.extend([OsString::from("--ort-runtime"), OsString::from("/other")]);
        assert_eq!(
            LaunchPlan::parse(duplicate, None).unwrap_err(),
            PlanError::Duplicate("--ort-runtime")
        );
        let mut duplicate = args();
        duplicate.extend([OsString::from("--stream"), OsString::from("--stream")]);
        assert_eq!(
            LaunchPlan::parse(duplicate, None).unwrap_err(),
            PlanError::Duplicate("--stream")
        );
        for value in [
            "--unknown",
            "/extra",
            "--model-dir=/selected/model",
            "--stream=1",
        ] {
            let mut selected = args();
            selected.push(value.into());
            assert_eq!(
                LaunchPlan::parse(selected, None).unwrap_err(),
                PlanError::UnknownArgument(value.into())
            );
        }
    }

    #[test]
    fn rank_entry1_every_selected_path_requires_absolute_value() {
        let names = [
            "--model-dir",
            "--ort-runtime",
            "--openblas",
            "--gfortran",
            "--quadmath",
            "--source-root",
        ];
        for (index, name) in names.into_iter().enumerate() {
            for value in ["", "relative/path"] {
                let mut selected = args();
                selected[index * 2 + 1] = value.into();
                assert_eq!(
                    LaunchPlan::parse(selected, None).unwrap_err(),
                    PlanError::RelativePath(name, PathBuf::from(value))
                );
            }
        }
    }

    #[cfg(unix)]
    #[test]
    fn rank_entry1_non_utf8_os_paths_and_values_remain_exact() {
        use std::os::unix::ffi::{OsStrExt, OsStringExt};
        let mut selected = args();
        selected[1] = OsString::from_vec(b"/selected/\xff".to_vec());
        selected.push("--stream".into());
        let plan = LaunchPlan::parse(selected, Some(OsString::from_vec(vec![255]))).unwrap();
        assert_eq!(
            plan.paths.model.as_os_str().as_bytes(),
            b"/selected/\xff/model_optimized.onnx"
        );
        assert_eq!(
            plan.mode,
            Mode::Stream {
                cache_enabled: false
            }
        );
        let unknown = OsString::from_vec(vec![255]);
        assert_eq!(
            LaunchPlan::parse(vec![unknown.clone()], None).unwrap_err(),
            PlanError::UnknownArgument(unknown)
        );
    }

    #[derive(Debug, PartialEq, Eq)]
    struct ProbeError(&'static str);
    struct ProbeDriver {
        calls: Vec<(&'static str, AssetPaths)>,
        fail: bool,
    }
    impl Driver for ProbeDriver {
        type Error = ProbeError;
        fn rank_once(&mut self, paths: AssetPaths) -> Result<(), Self::Error> {
            self.calls.push(("rank", paths));
            if self.fail {
                Err(ProbeError("original"))
            } else {
                Ok(())
            }
        }
        fn stream(&mut self, paths: AssetPaths, cache: CachePolicy) -> Result<(), Self::Error> {
            self.calls.push((
                match cache {
                    CachePolicy::NoCache => "stream",
                    CachePolicy::AdvertisedCache => "stream-cache",
                },
                paths,
            ));
            if self.fail {
                Err(ProbeError("original"))
            } else {
                Ok(())
            }
        }
    }

    #[test]
    fn rank_entry1_dispatch_selects_mode_and_preserves_owned_paths() {
        let mut driver = ProbeDriver {
            calls: vec![],
            fail: false,
        };
        for mode in [
            Mode::RankOnce,
            Mode::Stream {
                cache_enabled: false,
            },
            Mode::Stream {
                cache_enabled: true,
            },
        ] {
            dispatch(plan(mode), &mut driver).unwrap();
        }
        assert_eq!(
            driver.calls.iter().map(|call| call.0).collect::<Vec<_>>(),
            vec!["rank", "stream", "stream-cache"]
        );
        for (_, paths) in driver.calls {
            assert_eq!(
                paths.model,
                PathBuf::from("/selected/model/model_optimized.onnx")
            );
            assert_eq!(paths.ort_runtime, PathBuf::from("/selected/ort.so"));
            assert_eq!(paths.source_root, PathBuf::from("/selected/source"));
        }
    }

    #[test]
    fn rank_entry1_dispatch_returns_the_original_failure() {
        let mut driver = ProbeDriver {
            calls: vec![],
            fail: true,
        };
        for mode in [
            Mode::RankOnce,
            Mode::Stream {
                cache_enabled: false,
            },
            Mode::Stream {
                cache_enabled: true,
            },
        ] {
            assert_eq!(
                dispatch(plan(mode), &mut driver),
                Err(ProbeError("original"))
            );
        }
        assert_eq!(driver.calls.len(), 3);
    }

    impl CleanupReport for Vec<&'static str> {
        fn is_empty(&self) -> bool {
            Vec::is_empty(self)
        }
    }
    struct DropProbe(Rc<RefCell<Vec<&'static str>>>);
    impl Drop for DropProbe {
        fn drop(&mut self) {
            self.0.borrow_mut().push("drop");
        }
    }

    #[test]
    fn rank_entry1_cleanup_follows_owner_drop_and_retains_both_failures() {
        let events = Rc::new(RefCell::new(Vec::new()));
        let result = with_cleanup(
            || {
                events.borrow_mut().push("invoke");
                let _owner = DropProbe(events.clone());
                Err(ProbeError("invocation"))
            },
            || {
                events.borrow_mut().push("collect");
                vec!["cleanup"]
            },
        )
        .unwrap_err();
        assert_eq!(*events.borrow(), vec!["invoke", "drop", "collect"]);
        assert_eq!(result.invocation, Some(ProbeError("invocation")));
        assert_eq!(result.cleanup, vec!["cleanup"]);
    }

    #[test]
    fn rank_entry1_cleanup_outcomes_never_report_false_success() {
        for invocation_fails in [false, true] {
            for cleanup_fails in [false, true] {
                let result = with_cleanup(
                    || {
                        if invocation_fails {
                            Err(ProbeError("invocation"))
                        } else {
                            Ok(())
                        }
                    },
                    || {
                        if cleanup_fails {
                            vec!["cleanup"]
                        } else {
                            Vec::new()
                        }
                    },
                );
                if !invocation_fails && !cleanup_fails {
                    assert!(result.is_ok());
                } else {
                    let error = result.unwrap_err();
                    assert_eq!(
                        error.invocation,
                        invocation_fails.then_some(ProbeError("invocation"))
                    );
                    assert_eq!(
                        error.cleanup,
                        if cleanup_fails {
                            vec!["cleanup"]
                        } else {
                            Vec::new()
                        }
                    );
                }
            }
        }
    }
}
