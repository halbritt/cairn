"""Disabled source-only entry. This package contains no operational enablement path."""
import json


def main():
    print(json.dumps({'status': 'REFUSED_UNBOUND',
                      'dependencies': ['F1', 'F2', 'F3 operational launcher', 'F4', 'F5', 'F6 collector'],
                      'effects_launched': 0, 'readiness_or_adoption': False}, sort_keys=True))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
