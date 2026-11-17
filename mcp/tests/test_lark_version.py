"""First installation follows latest; existing deployments never drift implicitly."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1]/'lark-cli-mcp/scripts/prepare-cli.sh'
UPDATE = SCRIPT.with_name('update-cli.sh')


class VersionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name)
        self.root=self.base/'service';self.root.mkdir()
        (self.base/'scripts').mkdir()
        shutil.copy2(SCRIPT.parents[2]/'scripts/check-python.sh',self.base/'scripts/check-python.sh')
        (self.base/'scripts/prepare-python.py').write_text('print("reusing installed Python dependencies")\n')
        (self.root/'scripts').mkdir()
        shutil.copy2(SCRIPT,self.root/'scripts/prepare-cli.sh')
        shutil.copy2(UPDATE,self.root/'scripts/update-cli.sh')
        (self.root/'Dockerfile').write_text('test Dockerfile unchanged')
        (self.root/'compose.yaml').write_text('test Compose unchanged')
        self.bin=self.root/'bin';self.bin.mkdir()
        (self.bin/'curl').write_text('''#!/bin/sh
printf 'request\\n' >> "$REQUEST_LOG"
[ "${MOCK_NETWORK_FAIL:-0}" = 0 ] || exit 1
printf '%s\\n' "${MOCK_RELEASE}"
''')
        (self.bin/'docker').write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$DOCKER_LOG"
case "$*" in
  info) exit 0 ;;
  'compose ps --all -q mcp') printf '%s' "${MOCK_CONTAINER:-}" ;;
  'compose exec -T mcp lark-cli --version'|'compose exec -T mcp bx --version'|'compose exec -T mcp firecrawl --version')
    [ "${MOCK_EXEC_FAIL:-0}" = 0 ] || exit 1
    if [ -f "$RUNNING_RECORD" ]; then version=$(cat "$RUNNING_RECORD"); else version=${MOCK_INSTALLED:-1.2.3}; fi
    printf 'lark-cli version %s\\n' "$version" ;;
  'inspect -f {{.Image}} '*) printf 'test-old-image\\n' ;;
  'inspect -f {{.Config.Image}} '*) printf 'test-service:latest\\n' ;;
  'image tag '*) exit 0 ;;
  'compose build mcp') [ "${MOCK_BUILD_FAIL:-0}" = 0 ] ;;
  'compose up '*)
    if [ "${MOCK_UP_FAIL_ONCE:-0}" = 1 ] && [ ! -f "$UP_FAILURE_MARKER" ]; then touch "$UP_FAILURE_MARKER"; exit 1; fi
    cat deploy/lark-cli.version > "$RUNNING_RECORD" ;;
  *) exit 1 ;;
esac
''')
        for p in self.bin.iterdir():p.chmod(0o755)
        self.env={**os.environ,'PATH':str(self.bin)+os.pathsep+os.environ['PATH'],
                  'DOCKER_LOG':str(self.root/'docker.log'),'RUNNING_RECORD':str(self.root/'running-version'),'UP_FAILURE_MARKER':str(self.root/'up-failed'),
                  'REQUEST_LOG':str(self.root/'requests'),'MOCK_RELEASE':json.dumps({'tag_name':'v9.9.9','draft':False,'prerelease':False}),
                  'MOCK_CONTAINER':'','MOCK_NETWORK_FAIL':'0','MOCK_EXEC_FAIL':'0'}
        self.record=self.root/'deploy/lark-cli.version'

    def tearDown(self):self.tmp.cleanup()

    def run_script(self,*args,**env):
        return subprocess.run(['bash',str(self.root/'scripts/prepare-cli.sh'),*args],env={**self.env,**env},capture_output=True,text=True)

    def update(self,*args,**env):
        return subprocess.run(['bash',str(self.root/'scripts/update-cli.sh'),*args],env={**self.env,'MOCK_CONTAINER':'existing',**env},capture_output=True,text=True)

    def test_explicit_update_changes_local_record_not_source(self):
        result=self.update()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.record.read_text().strip(),'9.9.9')
        self.assertEqual((self.root/'Dockerfile').read_text(),'test Dockerfile unchanged')
        self.assertEqual((self.root/'compose.yaml').read_text(),'test Compose unchanged')

    def test_failed_upgrade_restores_installed_record_and_image(self):
        for flag in ['MOCK_BUILD_FAIL','MOCK_UP_FAIL_ONCE']:
            with self.subTest(flag=flag):
                result=self.update(**{flag:'1'})
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(self.record.read_text().strip(),'1.2.3')
                log=(self.root/'docker.log').read_text()
                self.assertIn('image tag lark-cli-mcp-rollback:',log)
                self.assertIn('compose up -d --no-build',log)

    def test_dry_run_does_not_create_a_version_record(self):
        result=self.update('--dry-run')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(self.record.exists())
        self.assertNotIn('compose build',(self.root/'docker.log').read_text())

    def test_all_cli_services_resolve_latest_then_preserve_the_record(self):
        for service,record in [('brave-search-mcp','brave-cli'),('firecrawl-mcp','firecrawl-cli'),('lark-cli-mcp','lark-cli')]:
            with self.subTest(service=service):
                script=SCRIPT.parents[2]/service/'scripts/prepare-cli.sh'
                shutil.copy2(script,self.root/'scripts/prepare-cli.sh')
                self.assertEqual(self.run_script().returncode,0)
                saved=self.root/'deploy'/f'{record}.version'
                self.assertEqual(saved.read_text().strip(),'9.9.9')
                self.assertEqual(self.run_script(MOCK_NETWORK_FAIL='1').returncode,0)

    def test_future_fresh_install_uses_release_returned_at_execution(self):
        for service, record in [('brave-search-mcp','brave-cli'),('firecrawl-mcp','firecrawl-cli'),('lark-cli-mcp','lark-cli')]:
            with self.subTest(service=service):
                shutil.copy2(SCRIPT.parents[2]/service/'scripts/prepare-cli.sh',self.root/'scripts/prepare-cli.sh')
                release=json.dumps({'tag_name':'v42.7.19','draft':False,'prerelease':False})
                result=self.run_script(MOCK_RELEASE=release)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual((self.root/'deploy'/f'{record}.version').read_text().strip(),'42.7.19')

    def test_fresh_install_resolves_latest_once_then_reuses(self):
        self.assertEqual(self.run_script().returncode,0)
        self.assertEqual(self.record.read_text().strip(),'9.9.9')
        result=self.run_script(MOCK_NETWORK_FAIL='1')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'9.9.9')
        self.assertEqual((self.root/'requests').read_text().count('request'),1)

    def test_legacy_install_adopts_installed_version_without_latest_lookup(self):
        result=self.run_script(MOCK_CONTAINER='existing',MOCK_INSTALLED='1.2.3',MOCK_NETWORK_FAIL='1')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.record.read_text().strip(),'1.2.3')
        self.assertFalse((self.root/'requests').exists())

    def test_unreadable_existing_install_fails_without_upgrading(self):
        result=self.run_script(MOCK_CONTAINER='stopped',MOCK_EXEC_FAIL='1')
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.record.exists())
        self.assertFalse((self.root/'requests').exists())

    def test_explicit_lookup_does_not_modify_installed_record(self):
        self.assertEqual(self.run_script('--use','1.2.3').returncode,0)
        self.assertEqual(self.run_script('--latest').stdout.strip(),'9.9.9')
        self.assertEqual(self.record.read_text().strip(),'1.2.3')

    def test_prerelease_and_network_errors_never_create_record(self):
        for env in [{'MOCK_NETWORK_FAIL':'1'}, {'MOCK_RELEASE':json.dumps({'tag_name':'v9.9.9','prerelease':True})}]:
            with self.subTest(env=env):
                self.assertNotEqual(self.run_script(**env).returncode,0)
                self.assertFalse(self.record.exists())


if __name__=='__main__':unittest.main()
