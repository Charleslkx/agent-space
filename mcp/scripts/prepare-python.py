#!/usr/bin/env python3
"""Resolve latest stable application dependencies once; preserve installed state."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ('brave-search-mcp','firecrawl-mcp','exa-mcp','lark-cli-mcp','lark-doc')
PROBE = '''import importlib.metadata as m,json,sys
print(json.dumps({"python":str(sys.version_info.major)+"."+str(sys.version_info.minor),"packages":{d.metadata["Name"]:d.version for d in m.distributions() if d.metadata["Name"]}}))'''


def run(argv, **kwargs):
    kwargs.setdefault("umask", 0o022)
    return subprocess.run([str(x) for x in argv],check=True,text=True,**kwargs)


def prepare(service: Path, native=False, upgrade=False):
    record=service/'deploy/python'
    if record.exists() and not upgrade:
        for name in ['pyproject.toml','uv.lock','uv-version.txt','installation.json']:
            if not (record/name).is_file():raise RuntimeError('incomplete dependency record; restore its backup before rebuilding')
        if native and not (record/'.venv/bin/python').exists():
            run(['bash',ROOT/'scripts/check-python.sh','--setup'],env={**os.environ,'PYTHON_BIN':sys.executable})
            metadata=json.loads((record/'installation.json').read_text())
            version=(record/'uv-version.txt').read_text().strip()
            with tempfile.TemporaryDirectory(prefix='mcp-uv-tools-') as temporary:
                tools=Path(temporary)/'tools'
                run([sys.executable,'-m','venv',tools])
                run([tools/'bin/python','-m','pip','--isolated','install','--no-cache-dir','--index-url','https://pypi.org/simple','uv=='+version])
                run([tools/'bin/uv','--version'])
                run([tools/'bin/uv','sync','--frozen','--no-dev','--python',metadata['python'],'--project',record],env={**os.environ,'UV_PYTHON_INSTALL_DIR':str(ROOT/'python')})
        print('Reusing recorded application dependencies; no package versions changed.')
        return
    run(['bash',ROOT/'scripts/check-python.sh','--setup'],env={**os.environ,'PYTHON_BIN':sys.executable})
    installed=None
    installed_uv=None
    if not upgrade:
        if native:
            for executable in [record/'.venv/bin/python',service/'.venv/bin/python']:
                if executable.exists():
                    installed=json.loads(run([executable,'-c',PROBE],cwd=service,capture_output=True).stdout)
                    break
        else:
            run(['docker','info'],stdout=subprocess.DEVNULL)
            container=run(['docker','compose','ps','--all','-q','mcp'],cwd=service,capture_output=True).stdout.strip()
            if container:
                # Unreadable installed environments stop here, never silently resolve latest.
                installed=json.loads(run(['docker','compose','exec','-T','mcp','/app/.venv/bin/python','-c',PROBE],cwd=service,capture_output=True).stdout)
                installed_uv=run(['docker','compose','exec','-T','mcp','python','-c','import importlib.metadata as m; print(m.version("uv"))'],cwd=service,capture_output=True).stdout.strip()
    service.joinpath('deploy').mkdir(exist_ok=True)
    python_store=ROOT/'python'
    python_store.mkdir(exist_ok=True,mode=0o755)
    python_store.chmod(0o755)
    env={**os.environ,'UV_DEFAULT_INDEX':'https://pypi.org/simple','UV_INDEX_URL':'https://pypi.org/simple','UV_NO_PROGRESS':'1','UV_PYTHON_INSTALL_DIR':str(ROOT/'python')}
    for name in ['UV_EXTRA_INDEX_URL','UV_INDEX','PIP_EXTRA_INDEX_URL']:
        env.pop(name,None)
    backup=None
    with tempfile.TemporaryDirectory(prefix='.python-candidate-',dir=service/'deploy') as staging:
        stage=Path(staging)
        tools=stage/'tools'
        run([sys.executable,'-m','venv',tools])
        uv_package='uv=='+installed_uv if installed_uv else 'uv'
        run([tools/'bin/python','-m','pip','--isolated','install','--no-cache-dir','--index-url','https://pypi.org/simple',uv_package],env=env)
        uv=tools/'bin/uv'
        run([uv,'--version'],env=env)
        uv_version=run([tools/'bin/python','-c','import importlib.metadata as m; print(m.version("uv"))'],capture_output=True).stdout.strip()
        candidate=stage/'dependencies';candidate.mkdir(mode=0o755);candidate.chmod(0o755)
        project=(service/'pyproject.toml').read_text()
        if installed:
            constraints=[f'{name}=={version}' for name,version in sorted(installed['packages'].items())]
            project=project.replace('[tool.uv]\n','[tool.uv]\nconstraint-dependencies = '+json.dumps(constraints)+'\n',1)
        (candidate/'pyproject.toml').write_text(project)
        python=installed['python'] if installed else '3.12'
        run([uv,'lock','--upgrade','--prerelease','disallow','--default-index','https://pypi.org/simple','--python',python,'--project',candidate],env=env)
        run([uv,'sync','--frozen','--no-dev','--python',python,'--project',candidate],env=env)
        suite='scripts' if service.name=='lark-doc' else 'tests'
        run([candidate/'.venv/bin/python','-B','-m','unittest','discover','-s',suite,'-p','test_*.py'],cwd=service,env=env)
        (candidate/'uv-version.txt').write_text(uv_version+'\n')
        (candidate/'installation.json').write_text(json.dumps({'python':python,'adopted_installed_versions':bool(installed),'resolved_at':datetime.now(timezone.utc).isoformat()},indent=2)+'\n')
        # Virtualenv entry points contain absolute paths; create native env at its final path.
        shutil.rmtree(candidate/'.venv')
        if record.exists():
            backup=service/'deploy'/('python.backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
            record.rename(backup)
        try:
            candidate.rename(record)
            if native:run([uv,'sync','--frozen','--no-dev','--python',python,'--project',record],env=env)
        except BaseException:
            if record.exists():shutil.rmtree(record)
            if backup:backup.rename(record)
            raise
    print('Recorded verified application dependencies; existing code-only rebuilds will reuse them.')
    if backup:print('Previous dependency record retained in deploy/python.backup-* for rollback.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('service',choices=SERVICES)
    parser.add_argument('--native',action='store_true',help='prepare a native/systemd Python environment')
    parser.add_argument('--upgrade',action='store_true',help='explicitly re-resolve latest stable application dependencies')
    args=parser.parse_args()
    run(['bash',ROOT/'scripts/check-python.sh','--runtime'],env={**os.environ,'PYTHON_BIN':sys.executable})
    prepare(ROOT/args.service,args.native,args.upgrade)


if __name__=='__main__':main()
