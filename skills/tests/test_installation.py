"""Offline installation and configuration boundaries; no real apps or notifications."""
import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, create_autospec

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSTALLER = load(ROOT/'scripts/install_skill.py', 'skill_installer')
SETUP = load(ROOT/'codex-notify-hook/scripts/create_feishu_agent_app.py', 'notify_setup')
PRIVACY = load(ROOT/'scripts/check_public_sources.py', 'source_privacy')


class InstallationTests(unittest.TestCase):
    def test_all_skills_copy_to_every_supported_client(self):
        sources = [p for p in ROOT.iterdir() if (p/'SKILL.md').is_file()]
        with tempfile.TemporaryDirectory() as tmp:
            for source in sources:
                for agent in INSTALLER.PARENTS:
                    with self.subTest(skill=source.name, agent=agent):
                        target = INSTALLER.install(source, Path(tmp)/agent)
                        self.assertEqual((target/'SKILL.md').read_bytes(), (source/'SKILL.md').read_bytes())
                        for resource in ['scripts', 'references', 'agents']:
                            if (source/resource).exists(): self.assertTrue((target/resource).is_dir())

    def test_cli_project_installation_handles_spaces_and_preserves_settings(self):
        with tempfile.TemporaryDirectory(prefix='skill project ') as tmp:
            project=Path(tmp);settings=project/'existing-settings.json';settings.write_text('{"preserved":true}')
            for agent,(_,relative) in INSTALLER.PARENTS.items():
                result=subprocess.run([sys.executable,str(ROOT/'scripts/install_skill.py'),'--skill','obsidian-vault-governance','--agent',agent,'--project',str(project)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertTrue((project/relative/'obsidian-vault-governance/SKILL.md').is_file())
            self.assertEqual(settings.read_text(),'{"preserved":true}')

    def test_secrets_caches_and_backup_files_are_not_distributed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'source'/'example-skill'
            source.mkdir(parents=True)
            for name in ['SKILL.md', '.env', '.env.production', 'feishu-agent.env', 'private.key', 'auth.token',
                         'feishu-agent.env.bak', 'auth.json', 'state.sqlite-wal', 'requirements-setup.txt', '.env.example']:
                (source/name).write_text('test fixture')
            (source/'.venv').mkdir()
            (source/'.venv/python').symlink_to(sys.executable)
            target = INSTALLER.install(source, Path(tmp)/'installed')
            self.assertEqual({p.name for p in target.iterdir()}, {'SKILL.md','requirements-setup.txt','.env.example'})

    def test_upgrade_preserves_old_install_and_rollback_after_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'source'/'example-skill'; source.mkdir(parents=True)
            (source/'SKILL.md').write_text('old')
            parent = Path(tmp)/'client'/'skills'
            target = INSTALLER.install(source, parent)
            with self.assertRaises(FileExistsError): INSTALLER.install(source, parent)
            (source/'SKILL.md').write_text('new')
            INSTALLER.install(source, parent, True)
            backup_root = parent.parent/'skill-backups'
            self.assertEqual(stat.S_IMODE(backup_root.stat().st_mode), 0o700)
            self.assertEqual(next(backup_root.glob('*/SKILL.md')).read_text(), 'old')
            self.assertEqual((target/'SKILL.md').read_text(), 'new')
            original_rename = Path.rename
            def fail_switch(path, dest):
                if '.skill-install-' in str(path): raise OSError('simulated switch failure')
                return original_rename(path, dest)
            with patch.object(Path, 'rename', fail_switch), self.assertRaises(OSError):
                INSTALLER.install(source, parent, True)
            self.assertEqual((target/'SKILL.md').read_text(), 'new')

    def test_privately_linked_sources_and_destinations_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'source'; source.mkdir(); (source/'SKILL.md').write_text('test')
            (source/'private').symlink_to('/missing-private-file')
            with self.assertRaises(ValueError): INSTALLER.install(source, Path(tmp)/'skills')
            (source/'private').unlink()
            with self.assertRaises(ValueError): INSTALLER.install(source, source/'nested')


class FeishuSetupTests(unittest.TestCase):
    def test_doctor_checks_cli_capabilities_without_auth_queries(self):
        calls=[]
        def run(argv, **kwargs):
            calls.append(argv[1:])
            return subprocess.CompletedProcess(argv, 0, 'lark-cli version 1.0.98' if '--version' in argv else '--new create app', '')
        with patch.object(SETUP.shutil, 'which', return_value='/mock/lark-cli'), patch.object(SETUP.subprocess, 'run', side_effect=run):
            report=SETUP.check_environment()
        self.assertTrue(report['lark_cli']['available'])
        self.assertTrue(report['lark_cli']['supports_app_creation'])
        self.assertEqual(calls, [['--version'], ['config','init','--help']])
        self.assertNotIn('/mock', str(report))

    def test_legacy_sdk_is_not_reported_as_creation_ready(self):
        def register_app(on_qr_code, on_status_change=None, source=None):
            raise AssertionError('legacy registration must not be called')
        sdk = types.SimpleNamespace(register_app=register_app)
        with patch.dict(sys.modules, {'lark_oapi':sdk}), patch.object(SETUP.shutil, 'which', return_value=None):
            report = SETUP.check_environment()
        self.assertTrue(report['sdk']['register_app'])
        self.assertFalse(report['ready_for_sdk_creation'])
        self.assertIn('addons', report['sdk']['missing_parameters'])
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {'lark_oapi':sdk}):
            args = argparse.Namespace(home_channel='oc_test_chat',app_id='',receive_id_type='chat_id',test=False)
            self.assertEqual(SETUP.create_app(args, {'app_name':'Test','description':'Test','source':'Test','agent':'Test'},Path(tmp)/'notify.env'), 2)

    def test_installed_sdk_contract_without_network(self):
        try:
            import lark_oapi as lark
        except ImportError:
            self.skipTest('install requirements-setup.txt to check the real SDK signature')
        if SETUP.missing_registration_parameters(lark.register_app):
            self.fail('installed SDK does not support the documented creation workflow')
        register = create_autospec(lark.register_app, return_value={
            'client_id':'cli_test_app','client_secret':'test-secret','user_info':{'tenant_brand':'feishu'}})
        with tempfile.TemporaryDirectory() as tmp, patch.object(lark,'register_app',register):
            args=argparse.Namespace(home_channel='oc_test_chat',app_id='',receive_id_type='chat_id',test=False)
            cfg={'app_name':'Test','description':'Test','source':'Test','agent':'Test'}
            self.assertEqual(SETUP.create_app(args,cfg,Path(tmp)/'notify.env'),0)
            self.assertEqual(register.call_args.kwargs['addons'], {'preset':False,'scopes':{'tenant':['im:message:send_as_bot']}})
            self.assertTrue(register.call_args.kwargs['create_only'])

    def test_live_app_selection_updates_instead_of_reusing_cached_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'notify.env'
            SETUP.write_env(target, {'FEISHU_APP_ID':'test-app','FEISHU_APP_SECRET':'old-test-secret','FEISHU_HOME_CHANNEL':'test-chat'})
            args = ['setup','--live','--app-id','test-app','--home-channel','test-chat','--env-out',str(target)]
            with patch.object(sys,'argv',args), patch.object(SETUP,'create_app',return_value=0) as create:
                self.assertEqual(SETUP.main(),0)
                create.assert_called_once()

    def test_cli_missing_and_timeout_are_reported(self):
        with patch.object(SETUP.shutil, 'which', return_value=None):
            self.assertFalse(SETUP.check_environment()['lark_cli']['available'])
        with patch.object(SETUP.shutil, 'which', return_value='/mock/lark-cli'), patch.object(SETUP.subprocess, 'run', side_effect=subprocess.TimeoutExpired('lark-cli',10)):
            self.assertIn('error', SETUP.check_environment()['lark_cli'])

    def test_env_is_private_preserves_unrelated_values_and_rejects_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'notify.env'; target.write_text('CUSTOM=preserved\nFEISHU_APP_SECRET=old-test-value\n')
            target.chmod(0o644)
            SETUP.write_env(target, {'FEISHU_APP_SECRET':'test-secret-value'})
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertIn('CUSTOM=preserved',target.read_text())
            before=target.read_bytes()
            with self.assertRaises(ValueError): SETUP.write_env(target, {'FEISHU_APP_SECRET':'test\nINJECTED=value'})
            self.assertEqual(target.read_bytes(), before)
            link=Path(tmp)/'linked.env';link.symlink_to(target)
            with self.assertRaises(ValueError): SETUP.write_env(link, {'FEISHU_APP_SECRET':'new-test-value'})

    def test_credentials_cannot_be_written_into_distributed_sources(self):
        for directory in [ROOT/'codex-notify-hook', ROOT.parent/'mcp/lark-doc']:
            target = directory/'private-test.env'
            with self.assertRaises(ValueError):
                SETUP.write_env(target, {'FEISHU_APP_SECRET':'your-test-secret'})
            self.assertFalse(target.exists())

    def test_manual_configuration_is_sdk_free_and_does_not_print_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'notify.env';out=io.StringIO()
            args=argparse.Namespace(test=False)
            with patch.object(SETUP.sys.stdin,'isatty',return_value=True), patch('builtins.input',side_effect=['your-app-id','your-chat-id','feishu']), patch.object(SETUP.getpass,'getpass',return_value='your-test-secret'), redirect_stdout(out):
                self.assertEqual(SETUP.configure_manually(args,{'agent':'Test'},target),0)
            self.assertNotIn('your-test-secret',out.getvalue())
            self.assertEqual(SETUP.read_env(target)['FEISHU_APP_SECRET'],'your-test-secret')

    def test_webhook_credentials_are_hidden_and_written_privately(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(SETUP.sys.stdin,'isatty',return_value=True), patch.object(SETUP.getpass,'getpass',side_effect=['https://open.feishu.cn/open-apis/bot/v2/hook/test-webhook','test-secret']):
            target=Path(tmp)/'webhook.env';out=io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(SETUP.configure_webhook(target),0)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o600)
            self.assertNotIn('test-webhook',out.getvalue())
            self.assertNotIn('test-secret',out.getvalue())

    def test_test_flag_without_a_config_cannot_report_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=subprocess.run([sys.executable,str(ROOT/'codex-notify-hook/scripts/create_feishu_agent_app.py'),'--test','--env-out',str(Path(tmp)/'absent.env')],env={**os.environ,'FEISHU_REUSE_ENV_PATHS':''},capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertNotIn('succeeded',result.stdout)


class PublicSourceTests(unittest.TestCase):
    def test_reserved_examples_are_allowed(self):
        self.assertEqual(PRIVACY.findings('your-email@example.com user@sub.example.com user@host.test'), [])

    def test_findings_report_only_location_and_type(self):
        value = 'oc_' + 'a' * 32
        found = PRIVACY.findings('first line\n' + value)
        self.assertEqual(found, [(2, 'Feishu identity')])
        self.assertNotIn(value, str(found))


if __name__ == '__main__':
    unittest.main()
