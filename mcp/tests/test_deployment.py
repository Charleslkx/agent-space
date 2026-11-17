"""Offline checks for deployable templates and multi-service coexistence."""
import os
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SERVICES = {'brave-search-mcp': 8766, 'firecrawl-mcp': 8767,
            'exa-mcp': 8769, 'lark-cli-mcp': 8768, 'lark-doc': 8765}


class DeploymentTests(unittest.TestCase):
    def test_all_sites_render_for_custom_hosts_without_nginx_variable_expansion(self):
        zones = []
        for service, port in SERVICES.items():
            with self.subTest(service=service):
                domain = f'{service}.example.com'
                result = subprocess.run(
                    ['bash', str(ROOT / 'scripts/nginx.sh'), service, 'render'],
                    env={**os.environ, 'DOMAIN': domain}, text=True, capture_output=True,
                    check=True)
                rendered = result.stdout
                self.assertNotIn('YOUR_', rendered)
                self.assertIn(f'server_name {domain};', rendered)
                self.assertIn(f'/live/{domain}/fullchain.pem', rendered)
                self.assertIn(f'proxy_pass http://127.0.0.1:{port};', rendered)
                self.assertIn('proxy_set_header Host $host;', rendered)
                self.assertIn('proxy_buffering off;', rendered)
                self.assertIn('location / {', rendered)
                zones.extend(re.findall(r'limit_req_zone .*? zone=(\w+):', rendered))
        self.assertEqual(len(zones), len(SERVICES))
        self.assertEqual(len(zones), len(set(zones)), 'cohosted zones must be unique')

    def test_invalid_domains_fail_before_host_mutations(self):
        for domain in ['https://example.com', 'example.com/path', 'example.com:443',
                       'a..example.com', '-a.example.com', 'a-.example.com',
                       'A.example.com', 'a' * 64 + '.example.com',
                       'example.com;id', 'example.com\nfoo']:
            for script, args in [('nginx.sh', ['exa-mcp', 'tls']),
                                 ('linux.sh', ['exa-mcp', '8769', 'install'])]:
                with self.subTest(domain=domain, script=script):
                    result = subprocess.run(['bash', str(ROOT / 'scripts' / script), *args],
                                            env={**os.environ, 'DOMAIN': domain},
                                            capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertRegex(result.stderr, 'invalid DOMAIN|DOMAIN must be')


if __name__ == '__main__':
    unittest.main()
