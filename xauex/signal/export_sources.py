"""Export the curated source registry to JSON, CSV and Markdown."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from xauex.signal.assets import all_symbols
from xauex.signal.source_registry import get_sources, get_source_map


FIELDNAMES = [
    'id', 'asset', 'tier', 'name', 'provider', 'category', 'kind', 'url', 'homepage',
    'official', 'auto_fetch', 'cadence', 'priority', 'why', 'notes'
]


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def write_md(path: Path, asset: str, rows: list[dict]) -> None:
    lines = [f'# {asset} source list', '', '| Tier | Name | Kind | Auto | Why | URL |', '|---|---|---|---|---|---|']
    for row in rows:
        auto = 'yes' if row['auto_fetch'] else 'manual'
        lines.append(
            f"| {row['tier']} | {row['name']} | {row['kind']} | {auto} | {row['why']} | {row['url']} |"
        )
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(description='Export source registry files')
    parser.add_argument('--output-dir', default='xauex/signal/sourcepacks', help='Directory to write files into')
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    combined = [row.to_dict() for row in get_sources()]
    (out_dir / 'source_registry.json').write_text(json.dumps(combined, indent=2), encoding='utf-8')
    write_csv(out_dir / 'source_registry.csv', combined)

    for asset in all_symbols():
        rows = [row.to_dict() for row in get_sources(asset)]
        base = out_dir / asset.lower()
        base.mkdir(parents=True, exist_ok=True)
        (base / f'{asset.lower()}_sources.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
        write_csv(base / f'{asset.lower()}_sources.csv', rows)
        write_md(base / f'{asset.lower()}_sources.md', asset, rows)


if __name__ == '__main__':
    main()
