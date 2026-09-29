"""Event-age freshness score for digest candidates (0-100, higher is fresher)."""


def score(candidate):
    age = candidate.get("event_age_days")
    if age is None:
        return None
    return max(0, 100 - 5 * age)
