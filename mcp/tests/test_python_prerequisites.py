"""Missing optional tools fail before setup and temporary probes clean up."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/check-python.sh'


class PythonPrerequisiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.interpreter = self.root/'selected python'
        self.interpreter.write_text('''#!/bin/sh
if [ "$1" = -c ]; then exit "${MOCK_RUNTIME_FAIL:-0}"; fi
if [ "$1" = -m ] && [ "$2" = venv ]; then exit 1; fi
exit 1
''')
        self.interpreter.chmod(0o755)
        self.env = {**os.environ, 'PYTHON_BIN':str(self.interpreter), 'TMPDIR':str(self.root)}

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, mode, **env):
        return subprocess.run(['bash',str(SCRIPT),mode],env={**self.env,**env},text=True,capture_output=True)

    def test_selected_interpreter_is_checked_instead_of_global_python(self):
        result = self.check('--runtime')
        self.assertEqual(result.returncode,0,result.stderr)
        failed = self.check('--runtime',MOCK_RUNTIME_FAIL='1')
        self.assertNotEqual(failed.returncode,0)
        self.assertIn('Python 3.10+',failed.stderr)

    def test_missing_interpreter_fails_with_remedy(self):
        result = self.check('--runtime',PYTHON_BIN=str(self.root/'missing-python'))
        self.assertNotEqual(result.returncode,0)
        self.assertIn('python3-venv',result.stderr)

    def test_missing_ensurepip_fails_and_removes_temporary_probe(self):
        result = self.check('--setup')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('venv/ensurepip',result.stderr)
        self.assertEqual(list(self.root.iterdir()),[self.interpreter])

    def test_global_uv_is_only_required_for_explicit_uv_mode(self):
        uv = self.root/'uv'
        uv.write_text('#!/bin/sh\nexit 1\n');uv.chmod(0o755)
        env = {'PATH':str(self.root)+os.pathsep+os.environ['PATH']}
        self.assertEqual(self.check('--runtime',**env).returncode,0)
        result = self.check('--uv',**env)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('uv is required',result.stderr)


if __name__ == '__main__':
    unittest.main()
