"""Verify the exact bytes of every published artifact, including line endings."""
import argparse
import hashlib
import json
from pathlib import Path


def verify_manifest(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    records = data.get('files')
    if not isinstance(records, dict) or not records:
        raise ValueError('Artifact manifest needs a nonempty file map')
    total = 0
    for name, record in records.items():
        file = (path.parent / name).resolve()
        if not file.is_relative_to(path.parent) or not file.is_file():
            raise ValueError('Artifact is outside the delivery or missing: ' + name)
        raw = file.read_bytes()
        if len(raw) != record.get('bytes') or hashlib.sha256(raw).hexdigest() != record.get('sha256'):
            raise ValueError('Artifact bytes or SHA256 changed: ' + name)
        total += len(raw)
    actual = {file.relative_to(path.parent).as_posix() for file in path.parent.rglob('*')
              if file.is_file() and file.resolve() != path}
    if actual != set(records):
        raise ValueError('Manifest has missing or unlisted delivery files')
    return dict(filesVerified=len(records), bytes=total)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_manifest(args.manifest)))
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, str(error) + '\n')
