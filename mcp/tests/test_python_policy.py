"""Dependency snapshots prevent incidental upgrades and survive candidate failures."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/prepare-python.py'
spec=importlib.util.spec_from_file_location('prepare_python',SCRIPT)
POLICY=importlib.util.module_from_spec(spec);spec.loader.exec_module(POLICY)


class PythonVersionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.service=self.root/'example-mcp';self.service.mkdir()
        (self.service/'pyproject.toml').write_text('[project]\nname="example-mcp"\nversion="0.1.0"\nrequires-python=">=3.11"\ndependencies=[]\n[tool.uv]\npackage=false\n')
        self.record=self.service/'deploy/python'

    def tearDown(self):self.tmp.cleanup()

    def installed_record(self):
        self.record.mkdir(parents=True)
        for name,text in [('pyproject.toml','old project'),('uv.lock','old lock'),('uv-version.txt','0.1.2'),('installation.json',json.dumps({'python':'3.12'}))]:
            (self.record/name).write_text(text)

    def test_existing_record_causes_no_network_or_resolver_calls(self):
        self.installed_record()
        with patch.object(POLICY,'run') as run,redirect_stdout(io.StringIO()):
            POLICY.prepare(self.service)
            run.assert_not_called()
        self.assertEqual((self.record/'uv.lock').read_text(),'old lock')

    def test_unreadable_existing_container_never_falls_back_to_latest(self):
        def run(argv,**kwargs):
            argv=list(map(str,argv))
            if argv[0]=='bash':return subprocess.CompletedProcess(argv,0)
            if 'info' in argv:return subprocess.CompletedProcess(argv,0)
            if 'ps' in argv:return subprocess.CompletedProcess(argv,0,stdout='existing-container')
            raise subprocess.CalledProcessError(1,argv)
        with patch.object(POLICY,'run',side_effect=run) as commands,self.assertRaises(subprocess.CalledProcessError):
            POLICY.prepare(self.service)
        self.assertFalse(self.record.exists())
        self.assertTrue(any('docker' in list(map(str,c.args[0])) for c in commands.call_args_list))
        self.assertFalse(any('pip' in list(map(str,c.args[0])) for c in commands.call_args_list))

    def test_failed_candidate_tests_leave_old_snapshot_unchanged(self):
        self.installed_record()
        def run(argv,**kwargs):
            argv=list(map(str,argv))
            if 'unittest' in argv:raise subprocess.CalledProcessError(1,argv)
            return subprocess.CompletedProcess(argv,0,stdout='0.1.2\n')
        with patch.object(POLICY,'ROOT',self.root),patch.object(POLICY,'run',side_effect=run),self.assertRaises(subprocess.CalledProcessError):
            POLICY.prepare(self.service,upgrade=True)
        self.assertEqual((self.record/'uv.lock').read_text(),'old lock')
        self.assertFalse(list((self.service/'deploy').glob('python.backup-*')))

    def test_installed_versions_are_constraints_during_legacy_adoption(self):
        observed=[]
        def run(argv,**kwargs):
            argv=list(map(str,argv))
            if argv[:2]==['docker','info']:return subprocess.CompletedProcess(argv,0)
            if 'ps' in argv:return subprocess.CompletedProcess(argv,0,stdout='existing')
            if argv[:3]==['docker','compose','exec']:
                output='0.1.2' if 'm.version("uv")' in argv[-1] else json.dumps({'python':'3.12','packages':{'test-package':'1.2.3'}})
                return subprocess.CompletedProcess(argv,0,stdout=output)
            if 'lock' in argv:observed.append((Path(argv[-1])/'pyproject.toml').read_text())
            if 'unittest' in argv:raise subprocess.CalledProcessError(1,argv)
            return subprocess.CompletedProcess(argv,0,stdout='0.1.2')
        with patch.object(POLICY,'ROOT',self.root),patch.object(POLICY,'run',side_effect=run),self.assertRaises(subprocess.CalledProcessError):
            POLICY.prepare(self.service)
        self.assertIn('test-package==1.2.3',observed[0])
        self.assertFalse(self.record.exists())


if __name__=='__main__':unittest.main()
