"""Package a generated painting-data.json as a versioned offline player."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pack_v7 import complete_source, envelope, GENERATOR_VERSION


def render_player(data_path, output):
    data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    if data.get("version") != GENERATOR_VERSION:
        raise ValueError(f"Painting data v{data.get('version')} does not match generator v{GENERATOR_VERSION}; use its original player or regenerate")
    template = (ROOT / "player_v7.html").read_text(encoding="utf-8")
    html = template.replace("__PAINTING_DATA__", envelope(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()))
    html = html.replace("__GENERATOR_SOURCE__", envelope(complete_source().encode(), f"portrait-painting-v{GENERATOR_VERSION}.py"))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(render_player(args.data, args.output).resolve())
