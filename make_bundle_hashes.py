"""Build helper: generate digests compiled into the customer executable."""
import argparse
from pathlib import Path

from bundle_integrity import COMPONENTS, tree_digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    values = {}
    for name, relative in COMPONENTS.items():
        target = root / relative
        if not target.exists():
            raise SystemExit(f'Missing release component: {target}')
        values[name] = tree_digest(target)
    output = Path(args.output).resolve()
    output.write_text(
        '"""Generated customer-bundle integrity values. Do not edit."""\n\n'
        + 'DIGESTS = ' + repr(values) + '\n', encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
