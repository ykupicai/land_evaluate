"""全專案文字盤點：不 import 被查程式、不執行案件腳本；只寫 outputs/_maintenance。"""
import ast
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
import warnings

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / 'outputs/_maintenance/20260916'


def main():
    raw = subprocess.check_output(['rg', '--files', '--hidden', '--no-ignore',
        '-g', '*.py', '-g', '*.md', '-g', '*.ps1', '-g', '!.git/**',
        '-g', '!outputs/_maintenance/**', '-g', '!outputs/_maintenance_*/**',
        '-g', '!**/__pycache__/**', '-g', '!**/.pytest_cache/**', '-g', '!.venv/**'], cwd=REPO)
    paths = sorted(raw.decode('utf-8').splitlines())
    inventory, duplicates, problems, links = [], defaultdict(list), [], []
    for relative in paths:
        path = REPO / relative
        content = path.read_text(encoding='utf-8-sig')
        item = {'path': path.relative_to(REPO).as_posix(), 'lines': len(content.splitlines()),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        if path.suffix == '.py':
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter('always')
                    tree = ast.parse(content, filename=relative)
                for warning in caught:
                    problems.append({'path': relative, 'line': warning.lineno, 'message': str(warning.message)})
                functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                item['functions'] = [{'name': n.name, 'line': n.lineno, 'lines': n.end_lineno-n.lineno+1}
                                     for n in functions]
                item['purpose'] = (ast.get_docstring(tree) or '').split('\n')[0]
                for n in functions:
                    if n.end_lineno - n.lineno < 5:
                        continue
                    body = n.body[1:] if ast.get_docstring(n) else n.body
                    key = ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False)
                    duplicates[key].append(f'{relative}:{n.lineno} {n.name}')
                item['imports'] = sorted({n.module for n in ast.walk(tree)
                                          if isinstance(n, ast.ImportFrom) and n.module})
            except SyntaxError as exc:
                problems.append({'path': relative, 'line': exc.lineno, 'message': str(exc)})
        elif path.suffix == '.md':
            item['headings'] = re.findall(r'^#{1,3} .+', content, re.M)
            for target in re.findall(r'\]\(([^)]+)\)', content):
                target = target.strip('<>').split('#')[0]
                if not target or re.match(r'^[a-zA-Z]+:', target) or '<' in target:
                    continue
                if not (path.parent / target).exists():
                    links.append({'path': relative, 'target': target})
        inventory.append(item)
    result = {'files': inventory, 'syntax_warnings': problems, 'missing_local_links': links,
              'identical_function_bodies': [v for v in duplicates.values() if len(v) > 1]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'source_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'files': len(inventory), 'python': sum(p.endswith('.py') for p in paths),
        'markdown': sum(p.endswith('.md') for p in paths), 'syntax_warnings': problems,
        'missing_local_links': links, 'duplicate_groups': len(result['identical_function_bodies'])},
        ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
