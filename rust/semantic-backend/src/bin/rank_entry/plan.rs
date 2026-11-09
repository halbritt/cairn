use cairn_semantic_backend::assets::AssetPaths;
use cairn_semantic_math::LibraryPaths;
use std::{
    ffi::{OsStr, OsString},
    path::PathBuf,
};

const PATH_FLAGS: [&str; 6] = [
    "--model-dir",
    "--ort-runtime",
    "--openblas",
    "--gfortran",
    "--quadmath",
    "--source-root",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Mode {
    RankOnce,
    Stream { cache_enabled: bool },
}
#[derive(Debug)]
pub(super) struct LaunchPlan {
    pub paths: AssetPaths,
    pub mode: Mode,
}
#[derive(Debug, PartialEq, Eq)]
pub(super) enum PlanError {
    UnknownArgument(OsString),
    Duplicate(&'static str),
    MissingValue(&'static str),
    MissingFlag(&'static str),
    RelativePath(&'static str, PathBuf),
}

fn required(paths: &mut [Option<PathBuf>; 6], index: usize) -> Result<PathBuf, PlanError> {
    paths[index]
        .take()
        .ok_or(PlanError::MissingFlag(PATH_FLAGS[index]))
}

impl LaunchPlan {
    /// Arguments exclude argv[0]. Values stay OS paths; parsing performs no I/O.
    pub fn parse(
        args: impl IntoIterator<Item = OsString>,
        cache_env: Option<OsString>,
    ) -> Result<Self, PlanError> {
        let mut args = args.into_iter();
        let mut paths: [Option<PathBuf>; 6] = std::array::from_fn(|_| None);
        let mut stream = false;
        while let Some(flag) = args.next() {
            if flag.as_os_str() == OsStr::new("--stream") {
                if stream {
                    return Err(PlanError::Duplicate("--stream"));
                }
                stream = true;
                continue;
            }
            let index = PATH_FLAGS
                .iter()
                .position(|name| flag.as_os_str() == OsStr::new(*name))
                .ok_or(PlanError::UnknownArgument(flag))?;
            if paths[index].is_some() {
                return Err(PlanError::Duplicate(PATH_FLAGS[index]));
            }
            let path = PathBuf::from(
                args.next()
                    .ok_or(PlanError::MissingValue(PATH_FLAGS[index]))?,
            );
            if !path.is_absolute() {
                return Err(PlanError::RelativePath(PATH_FLAGS[index], path));
            }
            paths[index] = Some(path);
        }
        let model_dir = required(&mut paths, 0)?;
        let paths = AssetPaths {
            model: model_dir.join("model_optimized.onnx"),
            config: model_dir.join("config.json"),
            tokenizer_config: model_dir.join("tokenizer_config.json"),
            special_tokens_map: model_dir.join("special_tokens_map.json"),
            tokenizer: model_dir.join("tokenizer.json"),
            ort_runtime: required(&mut paths, 1)?,
            math: LibraryPaths {
                openblas: required(&mut paths, 2)?,
                gfortran: required(&mut paths, 3)?,
                quadmath: required(&mut paths, 4)?,
            },
            source_root: required(&mut paths, 5)?,
        };
        let mode = if stream {
            Mode::Stream {
                cache_enabled: cache_env.as_deref() == Some(OsStr::new("1")),
            }
        } else {
            Mode::RankOnce
        };
        Ok(Self { paths, mode })
    }
}
