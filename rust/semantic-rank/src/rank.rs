use crate::backend::{BackendFactory, BackendSession, DecodeTokens, RawMatrix, SpecialTokens};
use crate::{Failure, PendingProfile, SourceSite, StageError};
use cairn_semantic_protocol::cache::{
    self, CacheState, RestoreOutcome, SnapshotEntries, SuppliedIdentity,
};
use cairn_semantic_protocol::hash::HashAdmittedRank;
use cairn_semantic_protocol::{
    project_unqualified_rank_echo, FailureKind, PyInteger, PyObject, PyString, Value,
};
use sha2::{Digest, Sha256};

const PREFIX: &str = "Represent this sentence for searching relevant passages: ";
const WIDTH: usize = 384;

pub(crate) fn string(text: &str) -> PyString {
    PyString::from_codepoints(text.chars().map(u32::from).collect()).expect("Rust scalar string")
}

pub(crate) fn passage_key(text: &PyString) -> Result<PyString, StageError> {
    let bytes = text.strict_utf8().map_err(Failure::PassageUtf8)?;
    let digest = Sha256::digest(bytes);
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    Ok(string(&hex))
}

fn source(site: SourceSite) -> StageError {
    Failure::Source {
        site,
        kind: FailureKind::ValueError,
    }
    .into()
}

fn object(entries: Vec<(&str, Value)>) -> Result<Value, StageError> {
    PyObject::new(
        entries
            .into_iter()
            .map(|(key, value)| (string(key), value))
            .collect(),
    )
    .map(Value::Object)
    .map_err(|error| Failure::Ir(error).into())
}

struct Constructed<S> {
    backend: S,
    identity: PyString,
}

/// Only construction state, session identity and copied raw passage vectors
/// survive a successful request. There are no retained request/query/body fields.
pub(crate) struct Engine<F: BackendFactory> {
    factory: F,
    session: Option<Constructed<F::Session>>,
    cache: Vec<(PyString, Vec<u32>)>,
    #[cfg(test)]
    pub(crate) snapshot_attempts: usize,
}

impl<F: BackendFactory> Engine<F> {
    pub(crate) fn new(factory: F) -> Self {
        Self {
            factory,
            session: None,
            cache: Vec::new(),
            #[cfg(test)]
            snapshot_attempts: 0,
        }
    }

    pub(crate) fn active(&self) -> bool {
        self.session.is_some()
    }

    /// Called only after published prefix/hash admission. Identity comes from
    /// the returned owning session, never from the request or a ready flag.
    pub(crate) fn construct(&mut self) -> Result<(), StageError> {
        if self.session.is_none() {
            let backend = self.factory.construct()?;
            let identity = backend.identity().clone();
            self.session = Some(Constructed { backend, identity });
        }
        Ok(())
    }

    pub(crate) fn restore(&mut self, payload: &Value) -> Result<(), StageError> {
        let identity =
            SuppliedIdentity::new(self.session.as_ref().expect("constructed").identity.clone());
        let mut restored = CacheState::new();
        match cache::restore_payload(&mut restored, payload, &identity).map_err(Failure::Cache)? {
            RestoreOutcome::NoOp | RestoreOutcome::IgnoredOtherModel => {}
            RestoreOutcome::Replaced => {
                self.cache = restored
                    .entries()
                    .iter()
                    .map(|entry| (entry.key().clone(), entry.bits().to_vec()))
                    .collect();
            }
            RestoreOutcome::NeedsSourceMeasure(marker) => {
                return Err(StageError::Pending(PendingProfile::Render(
                    marker.profile(),
                )))
            }
        }
        Ok(())
    }

    pub(crate) fn snapshot(&mut self) -> Result<Value, StageError> {
        #[cfg(test)]
        {
            self.snapshot_attempts += 1;
        }
        let entries = SnapshotEntries::new(self.cache.clone()).map_err(Failure::Ir)?;
        let identity =
            SuppliedIdentity::new(self.session.as_ref().expect("constructed").identity.clone());
        cache::snapshot_payload(&entries, &identity).map_err(|error| Failure::Cache(error).into())
    }

    pub(crate) fn score(&mut self, request: &HashAdmittedRank<'_>) -> Result<Value, StageError> {
        let prefix = request.prefix();
        let session = self
            .session
            .as_mut()
            .expect("constructed after hash admission");
        let backend = &mut session.backend;
        let mut question_points: Vec<u32> = PREFIX.chars().map(u32::from).collect();
        question_points.extend_from_slice(prefix.query().codepoints());
        let question = PyString::from_codepoints(question_points).map_err(Failure::Ir)?;
        if backend.encode(&question, SpecialTokens::Default)?.len() > 512 {
            return Err(source(SourceSite::QueryModelTokens));
        }

        let mut passages = Vec::new();
        let mut missing: Vec<(PyString, usize)> = Vec::new();
        let mut owners = Vec::new();
        let mut keys = Vec::new();
        // Preserve the Source generator's laziness: no later body encode or
        // later chunk decode can precede this chunk's checks and hash.
        for (owner, note) in prefix.notes().iter().enumerate() {
            let Value::Object(note) = note else {
                unreachable!("prefix admitted note")
            };
            let Some(Value::String(body)) = note.get(&string("body")) else {
                unreachable!("prefix admitted body")
            };
            let tokens = backend.encode(body, SpecialTokens::Disabled)?;
            if tokens.is_empty() {
                return Err(source(SourceSite::BodyModelTokens(owner)));
            }
            let mut start = 0;
            loop {
                let end = (start + WIDTH).min(tokens.len());
                let chunk = backend.decode(&tokens[start..end], DecodeTokens::KeepSpecialTokens)?;
                // 129th occurrence has been decoded, but not reencoded/inferred.
                if owners.len() == 128 {
                    return Err(source(SourceSite::OccurrenceBudget));
                }
                if backend.encode(&chunk, SpecialTokens::Default)?.len() > 512 {
                    return Err(source(SourceSite::PassageModelTokens));
                }
                let key = passage_key(&chunk)?;
                if !self.cache.iter().any(|(cached, _)| cached == &key)
                    && !missing.iter().any(|(existing, _)| existing == &key)
                {
                    missing.push((key.clone(), passages.len() + 1));
                    passages.push(chunk);
                }
                owners.push(owner);
                keys.push(key);
                if start + WIDTH >= tokens.len() {
                    break;
                }
                start += 320;
            }
        }

        let mut model_texts = Vec::with_capacity(passages.len() + 1);
        model_texts.push(question);
        model_texts.extend(passages);
        let embedded = backend.embed_batch_one(&model_texts)?;
        if embedded.rows != model_texts.len()
            || embedded.columns != WIDTH
            || embedded.bits.len() != model_texts.len() * WIDTH
            || embedded
                .bits
                .iter()
                .any(|bits| bits & 0x7f80_0000 == 0x7f80_0000)
        {
            return Err(source(SourceSite::RawModelShapeOrFinite));
        }
        let parts: Vec<Vec<u32>> = keys
            .iter()
            .map(|key| {
                if let Some((_, bits)) = self.cache.iter().find(|(cached, _)| cached == key) {
                    bits.clone()
                } else {
                    let row = missing
                        .iter()
                        .find(|(missing, _)| missing == key)
                        .expect("miss row exists")
                        .1;
                    embedded.bits[row * WIDTH..(row + 1) * WIDTH].to_vec()
                }
            })
            .collect();
        let mut bits = Vec::with_capacity((parts.len() + 1) * WIDTH);
        bits.extend_from_slice(&embedded.bits[..WIDTH]);
        for part in &parts {
            bits.extend_from_slice(part);
        }
        let vectors = RawMatrix {
            rows: parts.len() + 1,
            columns: WIDTH,
            bits,
        };
        let norms = backend.norms_keepdims(&vectors)?;
        if backend.any_zero_norm(&norms)? {
            return Err(source(SourceSite::ZeroNorm));
        }
        let normalized = backend.divide_in_place(vectors, norms)?;
        let similarities = backend.matrix_vector(normalized)?;
        // Do not introduce new post-arithmetic finiteness guards. The published
        // reduction preserves ordered max, NaN/Inf handling and ties-even.
        let mut per_note = vec![Vec::new(); prefix.notes().len()];
        for (&owner, value) in owners.iter().zip(similarities) {
            per_note[owner].push(value);
        }

        // Source metadata lookups occur after arithmetic. Failure must leave
        // the previous raw cache intact, including missing-field KeyError.
        let echoes = project_unqualified_rank_echo(prefix.notes()).map_err(Failure::Projection)?;
        let mut scores = Vec::with_capacity(echoes.notes().len());
        for (index, echo) in echoes.notes().iter().enumerate() {
            let score = cairn_semantic_score::rank_note_score(&per_note[index]);
            let integer = PyInteger::new(score < 0, vec![score.unsigned_abs()]);
            scores.push(object(vec![
                ("record_id", echo.record_id().clone()),
                ("version", echo.version().clone()),
                ("body_sha256", echo.body_sha256().clone()),
                ("score", Value::Integer(integer)),
            ])?);
        }
        let result = object(vec![
            ("model_sha256", Value::String(session.identity.clone())),
            ("algorithm", Value::String(string("bge-max-chunk/1"))),
            ("scores", Value::Array(scores)),
        ])?;
        let mut replacement: Vec<(PyString, Vec<u32>)> = Vec::new();
        for (key, part) in keys.into_iter().zip(parts) {
            if let Some((_, value)) = replacement
                .iter_mut()
                .find(|(existing, _)| existing == &key)
            {
                *value = part;
            } else {
                replacement.push((key, part));
            }
        }
        self.cache = replacement;
        Ok(result)
    }

    #[cfg(test)]
    pub(crate) fn cached(&self) -> &[(PyString, Vec<u32>)] {
        &self.cache
    }
}
