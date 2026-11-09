//! Linux executing-inode observation; never hash a replaceable installation path.
use crate::arithmetic::operation;
use cairn_semantic_rank::backend::BackendError;
use std::{fs::File, io::Read, os::unix::fs::MetadataExt};
pub(crate) struct RunningImage {
    pub bytes: Vec<u8>,
    pub device: u64,
    pub inode: u64,
}
pub(crate) fn observe() -> Result<RunningImage, BackendError> {
    let fd =
        File::open("/proc/self/exe").map_err(|e| operation("open running executable inode", e))?;
    read_descriptor(fd)
}
pub(super) fn read_descriptor(mut fd: File) -> Result<RunningImage, BackendError> {
    let before = fd
        .metadata()
        .map_err(|e| operation("stat running executable inode", e))?;
    if !before.is_file() || before.len() > isize::MAX as u64 {
        return Err(operation(
            "running image storage",
            "invalid regular executable size",
        ));
    }
    let mut bytes = Vec::new();
    fd.by_ref()
        .take(before.len().saturating_add(1))
        .read_to_end(&mut bytes)
        .map_err(|e| operation("read running executable descriptor", e))?;
    let after = fd
        .metadata()
        .map_err(|e| operation("restat running executable inode", e))?;
    if before.dev() != after.dev()
        || before.ino() != after.ino()
        || before.len() != after.len()
        || bytes.len() as u64 != after.len()
        || before.mtime() != after.mtime()
        || before.mtime_nsec() != after.mtime_nsec()
        || before.ctime() != after.ctime()
        || before.ctime_nsec() != after.ctime_nsec()
    {
        return Err(operation(
            "running executable changed",
            "descriptor metadata/content length changed",
        ));
    }
    Ok(RunningImage {
        bytes,
        device: after.dev(),
        inode: after.ino(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn p2_observe1_descriptor_hash_follows_open_inode_after_install_path_replacement() {
        use std::sync::atomic::{AtomicU64, Ordering};
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let dir = std::env::temp_dir().join(format!(
            "cairn-observe-image-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&dir).unwrap();
        let installed = dir.join("image");
        let replacement = dir.join("replacement");
        std::fs::write(&installed, b"running-original-inode").unwrap();
        let fd = File::open(&installed).unwrap();
        let old = fd.metadata().unwrap();
        std::fs::write(&replacement, b"new-install-path-inode").unwrap();
        std::fs::rename(&replacement, &installed).unwrap();
        let observed = read_descriptor(fd).unwrap();
        assert_eq!(observed.bytes, b"running-original-inode");
        assert_eq!(observed.device, old.dev());
        assert_eq!(observed.inode, old.ino());
        assert_ne!(observed.inode, std::fs::metadata(&installed).unwrap().ino());
        assert_ne!(observed.bytes, std::fs::read(&installed).unwrap());
        std::fs::remove_file(installed).unwrap();
        std::fs::remove_dir(dir).unwrap();
    }
}
