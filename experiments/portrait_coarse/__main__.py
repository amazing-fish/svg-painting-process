"""Run an explicit coarse experiment, without automatic semantic acceptance."""
import argparse
import json
from pathlib import Path
from .runner import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--guides', type=Path, required=True)
    parser.add_argument('--provenance', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--through', type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    try:
        painter = generate(**vars(args))
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f'Input or experiment error: {error}\n')
    blocked = getattr(painter, 'construction_blocked', None)
    print(json.dumps(dict(status='incomplete' if blocked else 'requested_layers_complete',
                          strokes=len(painter.strokes), blocked=blocked, semanticAcceptance=False), ensure_ascii=False))
    return 1 if blocked else 0


if __name__ == '__main__':
    raise SystemExit(main())
