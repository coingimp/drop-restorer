import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from drop_restorer.core.pipeline import Pipeline
from drop_restorer.core.models import Build, RestorationError
from drop_restorer.core.cleaner import parse_html
from drop_restorer.core.checklist import inspect_site, decide, approve_site
from drop_restorer.core.packager import approve, package
from drop_restorer.core.seo_routes import prepare, resolve, write_staging
from drop_restorer.core.seo_contract import fingerprint, save_json
from tests_drop_restorer.fixtures import FixtureArchive, FixtureAgent, request, attest_fixture


class SeoChecklistTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.pipeline=Pipeline(Path(self.temp.name),client=FixtureArchive(),agent=FixtureAgent())
        pending=self.pipeline.run(request());pending=self.pipeline.suggest_metadata(pending)
        self.build=self.pipeline.finish(pending,'agent')

    def test_all_58_requirements_gate_theme_and_unknown_browser_cannot_be_confirmed(self):
        report=inspect_site(self.build)
        self.assertEqual(len(report['items']),58)
        self.assertEqual(self.build.status,'site_review')
        self.assertFalse((self.build.root/'content.xml').exists())
        self.assertFalse((self.build.root/'theme/functions.php').exists())
        self.assertFalse(report['can_build'])
        unknown=next(r for r in report['items'] if r['key']=='readable')
        self.assertEqual(unknown['status'],'not_checked')
        with self.assertRaises(RestorationError):
            decide(self.build,unknown['id'],'confirm','Trying to bypass missing browser evidence')
        with self.assertRaises(RestorationError):
            self.pipeline.build_theme(self.build)
        attest_fixture(self.build)
        self.pipeline.build_theme(self.build)
        self.assertTrue((self.build.root/'content.xml').exists())
        site=json.loads((self.build.root/'theme/site.json').read_text())
        self.assertNotIn('decisions',site['seo_policy'])

    def test_affiliate_check_rejects_query_parameters_even_when_target_is_correct(self):
        from tests_drop_restorer.test_affiliates import table_markup
        from drop_restorer.core.affiliates import parse_table
        route=next(p.route for p in self.build.pages if p.casino)
        table=parse_table(table_markup(),88,self.build.request.origin,route)
        self.build.seo_policy['widget_id']=88
        path=self.build.root/'affiliate-manifest.json'
        save_json(path,{'tables':[table],'offers':table['offers']})
        report=inspect_site(self.build)
        self.assertEqual(next(r for r in report['items'] if r['key']=='affiliate_go')['status'],'pass')
        table['html']=table['html'].replace('/go/alpha','/go/alpha?offer=legacy')
        save_json(path,{'tables':[table],'offers':table['offers']})
        report=inspect_site(self.build)
        self.assertEqual(next(r for r in report['items'] if r['key']=='affiliate_go')['status'],'fail')

    def test_changed_content_invalidates_reviews_and_browser_evidence(self):
        attest_fixture(self.build)
        self.assertTrue(inspect_site(self.build)['can_build'])
        asset=next((self.build.root/'theme/assets').glob('*.css'))
        asset.write_text(asset.read_text()+'\nbody{color:red}')
        report=inspect_site(self.build)
        self.assertFalse(report['can_build'])
        self.assertGreater(report['counts']['not_checked'],0)
        self.assertGreater(report['counts']['review'],0)

    def test_casino_shell_loss_is_a_reported_error_even_with_empty_editor(self):
        casino = next(page for page in self.build.pages if page.casino)
        soup = parse_html(casino.html)
        self.assertEqual(soup.select_one('#dr-editor-content').get_text(strip=True), '')
        for region in ('header', 'footer'):
            soup.select_one(region).decompose()
        casino.html = str(soup)
        write_staging(self.build)
        report = inspect_site(self.build)
        for key in ('content_visual', 'menu_visual', 'footer'):
            row = next(item for item in report['items'] if item['key'] == key)
            self.assertEqual(row['status'], 'fail', row)
            self.assertTrue(any(item['route'] == casino.route and 'потеряна' in item['detail']
                                for item in row['evidence']), row)
        self.assertFalse(report['checks_passed'])

    def test_url_policy_rewrites_menu_and_rejects_reserved_targets(self):
        self.build.seo_policy['url_style']='no_slash';prepare(self.build)
        self.assertEqual(resolve(self.build,'/about/'),(301,'/about'))
        self.assertEqual(resolve(self.build,'/about/index.html'),(301,'/about'))
        self.assertEqual(resolve(self.build,'/about?utm_source=x'),(301,'/about'))
        self.assertEqual(resolve(self.build,'/missing'),(404,''))
        report=inspect_site(self.build)
        self.assertEqual(next(r for r in report['items'] if r['key']=='menu_links')['status'],'pass')
        for route in ('/go/brand/','/robots.txt','/assets/fake/','/wp-admin/'):
            self.build.seo_policy['url_overrides']={self.build.pages[1].key:route}
            with self.assertRaises(RestorationError):
                prepare(self.build)

    def test_sidebar_casino_menu_is_an_automatic_failure_even_if_labeled_main(self):
        page = self.build.pages[0]
        soup = parse_html(page.html)
        aside = soup.new_tag('aside')
        aside.append(soup.select_one('.dr-navigation').extract())
        soup.body.append(aside)
        page.html = str(soup)
        write_staging(self.build)
        report = inspect_site(self.build)
        row = next(item for item in report['items'] if item['key'] == 'menu_visual')
        self.assertEqual(row['status'], 'fail')
        self.assertTrue(any('боковом' in item['detail'] for item in row['evidence']))

    def test_automatic_errors_cannot_be_overridden_and_provider_failure_is_not_pass(self):
        self.build.pages[0].html=self.build.pages[0].html.replace('</head>','<script>gtag("config","bad")</script></head>')
        write_staging(self.build)
        self.build.seo_policy['widget_id']=88
        save_json(self.build.root/'affiliate-manifest.json',{'errors':[{'route':'/casino-rating/','error':'HTTP 520'}],'offers':[]})
        report=inspect_site(self.build)
        for key in ('technical','affiliate_go','affiliate_links'):
            row=next(r for r in report['items'] if r['key']==key)
            self.assertEqual(row['status'],'fail')
            with self.assertRaises(RestorationError):
                decide(self.build,row['id'],'confirm','Cannot waive automatic error')

    def test_fingerprint_excludes_network_observation_but_includes_target(self):
        path=self.build.root/'affiliate-manifest.json'
        manifest={'offers':[{'target':'https://example.com/?x=1'}]}
        save_json(path,manifest);before=fingerprint(self.build)
        manifest['offers'][0]['verification']={'status':'reachable'}
        save_json(path,manifest);self.assertEqual(before,fingerprint(self.build))
        manifest['offers'][0]['target']='https://example.com/?x=2'
        save_json(path,manifest);self.assertNotEqual(before,fingerprint(self.build))

    def test_owner_acceptance_preserves_failures_and_missing_browser_evidence(self):
        self.build.pages[0].html=self.build.pages[0].html.replace('</head>','<title>Duplicate</title></head>')
        write_staging(self.build)
        before=inspect_site(self.build)
        self.assertGreater(before['counts'].get('fail',0),0)
        self.assertGreater(before['counts'].get('not_checked',0),0)
        self.pipeline.agent=None
        self.pipeline.build_theme(self.build,owner_approved=True,viewed_fingerprint=before['fingerprint'])
        restored=Build.load(self.build.root)
        report=inspect_site(restored)
        self.assertEqual(restored.status,'ready')
        self.assertTrue(report['can_build'])
        self.assertFalse(report['checks_passed'])
        self.assertEqual(before['counts'],report['counts'])
        self.assertEqual(report['owner_approval']['by'],'owner')
        self.assertTrue(report['owner_approval']['unresolved'])
        self.assertTrue((restored.root/'content.xml').is_file())
        runtime=json.loads((restored.root/'theme/site.json').read_text(encoding='utf-8'))
        self.assertNotIn('owner_approvals',runtime)
        self.assertFalse(report['ready_for_public_indexing'])

    def test_changed_site_cannot_reuse_owner_acceptance(self):
        approve_site(self.build,fingerprint(self.build))
        old=fingerprint(self.build)
        asset=next((self.build.root/'theme/assets').glob('*.css'))
        asset.write_text(asset.read_text()+'\nbody{color:red}')
        report=inspect_site(Build.load(self.build.root))
        self.assertIsNone(report['owner_approval'])
        self.assertFalse(report['can_build'])
        with self.assertRaises(RestorationError):
            self.pipeline.build_theme(self.build,owner_approved=True,viewed_fingerprint=old)
        with self.assertRaises(RestorationError):
            self.pipeline.build_theme(self.build,owner_approved=True)

    def test_owner_can_package_with_findings_but_not_after_changes(self):
        self.pipeline.build_theme(self.build,owner_approved=True,viewed_fingerprint=fingerprint(self.build))
        page=self.build.root/'pages'/(self.build.pages[0].key+'.html')
        page.write_text(page.read_text(encoding='utf-8').replace('id="dr-primary-menu"','id="broken-menu"'),encoding='utf-8')
        digest=self.build.digest()
        approve(self.build,digest,accept_findings=True)
        output=self.build.root/'owner-approved-fixture.zip'
        package(self.build,output)
        from drop_restorer.core.packager import bundle_path
        with zipfile.ZipFile(bundle_path(output)) as archive:
            report=json.loads(archive.read('checks.json'))
            seo=json.loads(archive.read('seo-checklist.json'))
            decisions=json.loads(archive.read('owner-approvals.json'))
        self.assertFalse(report['passed'])
        self.assertGreater(report['errors'],0)
        self.assertFalse(seo['checks_passed'])
        self.assertEqual(decisions['package']['digest'],digest)
        self.assertEqual(decisions['package']['audit_errors'],report['errors'])
        self.assertTrue(decisions['package']['unresolved'])
        style=self.build.root/'theme/style.css'
        style.write_text(style.read_text(encoding='utf-8')+'\n/* changed */',encoding='utf-8')
        with self.assertRaises(RestorationError):
            package(self.build,self.build.root/'stale-owner-approval.zip')


if __name__=='__main__':
    unittest.main()
