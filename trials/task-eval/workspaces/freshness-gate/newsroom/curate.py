"""Select digest candidates."""
from newsroom import freshness


def annotate(candidates):
    for c in candidates:
        s = freshness.score(c)
        if s is not None:
            c["title"] = f"{c['title']} {{freshness={s}}}"
    return candidates


def select(candidates):
    return annotate([c for c in candidates if c.get("in_window", True)])
