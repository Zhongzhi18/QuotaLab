from pathlib import Path
import unittest
import quota_lab
from app_paths import default_data_root


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parent


class PublicReleaseTests(unittest.TestCase):
    def test_public_version_is_consistent(self):
        self.assertEqual(quota_lab.APP_VERSION, '1.0.0')
        self.assertIn("version.textContent = 'v1.0.0'", (ROOT/'ui_art_direction.js').read_text(encoding='utf-8'))
        self.assertIn('filevers=(1,0,0,0)', (ROOT/'packaging/windows-version-1.0.0.txt').read_text(encoding='utf-8'))

    def test_existing_plans_keep_their_data_root_and_instance_family(self):
        self.assertEqual(default_data_root(environment={'LOCALAPPDATA':'C:/Users/example/AppData/Local'},home=Path('C:/Users/example'),platform_name='win32').name,'QuotaLab-dev-v0143')
        self.assertIn('Instance-v0143',(ROOT/'quota_lab.py').read_text(encoding='utf-8'))

    def test_readable_build_entry_points_exist(self):
        self.assertTrue((REPOSITORY/'scripts/build-windows.ps1').is_file())
        self.assertTrue((REPOSITORY/'scripts/check-windows-package.ps1').is_file())
        self.assertTrue((REPOSITORY/'docs/使用指南.md').is_file())

    def test_pipeline_builds_and_checks_the_public_version(self):
        workflow=(REPOSITORY/'.github/workflows/windows-release.yml').read_text(encoding='utf-8')
        self.assertIn('working-directory: app',workflow)
        self.assertIn('QuotaLab-1.0.0-Windows-x64.zip',workflow)
        self.assertIn('v1.0.0',workflow)
        self.assertNotIn('development/v0143',workflow)

    def test_portable_build_includes_runtime_notices(self):
        build=(ROOT/'scripts/build_windows_v0143.ps1').read_text(encoding='utf-8')
        for text in ('Python.txt','PyInstaller.txt','tzdata-Apache.txt','THIRD_PARTY_NOTICES.md'):
            self.assertIn(text,build)


if __name__=='__main__':unittest.main()
