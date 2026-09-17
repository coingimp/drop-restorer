from pathlib import Path
import tempfile
import time
import unittest

from drop_restorer.core.pipeline import Pipeline
from drop_restorer.web.server import create_app
from tests_drop_restorer.fixtures import FixtureArchive, request


class DownloadProgressTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / 'var/drop-restorer/development-qa/progress-fixtures'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)

    def test_pipeline_reports_pages_resources_bytes_and_detailed_log(self):
        activity, logs = [], []
        build = Pipeline(Path(self.temp.name), log=logs.append, client=FixtureArchive(),
                         activity=lambda event: activity.append(dict(event))).run(request())

        assets = [event for event in activity if event.get('scope') == 'asset']
        self.assertTrue(any(event.get('status') == 'page' and event.get('page') == 1 and event.get('total') == 2
                            for event in assets))
        completed = [event for event in assets if event.get('status') == 'downloaded']
        self.assertTrue(completed)
        self.assertEqual(completed[-1]['downloaded'], len(completed))
        self.assertGreater(completed[-1]['bytes'], 0)
        self.assertTrue(completed[-1]['resource'].startswith('https://example.com/'))
        self.assertTrue(any('RESOURCE: скачан' in line and (' B' in line or ' KB' in line or ' MB' in line)
                            for line in logs))
        saved = (build.root / 'run.log').read_text(encoding='utf-8')
        self.assertIn('RESOURCE: скачан', saved)

    def test_selected_build_restores_last_500_persisted_log_lines(self):
        workspace = Path(self.temp.name)
        root = workspace / 'var/drop-restorer'
        build = Pipeline(root, client=FixtureArchive()).run(request())
        lines = [f'line {number}' for number in range(510)]
        (build.root / 'run.log').write_text('\n'.join(lines) + '\n', encoding='utf-8')

        first = create_app(workspace)
        first.extensions['workspace'].select(build)
        first.extensions['workspace'].close()
        second = create_app(workspace)
        try:
            restored = list(second.extensions['workspace'].logs)
            self.assertEqual(len(restored), 500)
            self.assertEqual(restored[0], 'line 10')
            self.assertEqual(restored[-1], 'line 509')
        finally:
            second.extensions['workspace'].close()

    def test_new_restoration_job_clears_only_the_screen_log(self):
        workspace = Path(self.temp.name)
        app = create_app(workspace)
        state = app.extensions['workspace']
        try:
            state.logs.extend(['old build line 1', 'old build line 2'])
            state.start('new restoration', lambda: None, reset_logs=True)
            for _ in range(100):
                if not state.job['busy']:
                    break
                time.sleep(.01)
            self.assertFalse(state.job['busy'])
            self.assertEqual(list(state.logs), [])
        finally:
            state.close()


if __name__ == '__main__':
    unittest.main()
