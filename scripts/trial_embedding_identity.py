"""Typed embedding backend identity declaration (cairn.embedding-model-identity/1).

The declaration file is pinned by its own SHA-256, which is never the backend's
model hash. Its contents are the semantic.EmbeddingIdentity shape
{model_sha256, algorithm, dimensions}. Discovery exposes model_sha256 and
algorithm only, so dimensions stay declared and are never observed here.
"""
SCHEMA = 'cairn.embedding-model-identity/1'
OBSERVED = ('model_sha256', 'algorithm')


def typed(document):
    """Return the typed declared values, or raise ValueError for anything opaque or untyped."""
    if not (isinstance(document, dict) and set(document) == {'schema', 'model_sha256', 'algorithm', 'dimensions'}
            and document['schema'] == SCHEMA and type(document['model_sha256']) is str
            and len(document['model_sha256']) == 64 and all(c in '0123456789abcdef' for c in document['model_sha256'])
            and type(document['algorithm']) is str and 0 < len(document['algorithm']) <= 128
            and type(document['dimensions']) is int and document['dimensions'] > 0):
        raise ValueError('model_identity_declaration')
    return {k: document[k] for k in ('model_sha256', 'algorithm', 'dimensions')}


def matches(observed, declared):
    """Compare discovery's observed values with the declared ones; dimensions are not compared."""
    return (isinstance(observed, dict) and all(observed.get(k) is not None for k in OBSERVED)
            and {k: observed[k] for k in OBSERVED} == {k: declared[k] for k in OBSERVED})
