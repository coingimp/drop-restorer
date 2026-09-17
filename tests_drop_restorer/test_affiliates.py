import html
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from bs4 import BeautifulSoup
from drop_restorer.core.affiliates import parse_table, validate_target, verify_offer, fetch_table, resolve_offer, USER_AGENT
from drop_restorer.core.models import RestorationError
from drop_restorer.core.pipeline import Pipeline
from drop_restorer.core.seo_contract import save_json
from drop_restorer.preview.server import PreviewServer
from tests_drop_restorer.fixtures import FixtureArchive, request


TARGET='https://cryptocasinokingdom.com/test-affiliate?brand=alpha&dept=cz&st=site&p=88&tag=a%2Fb&empty=&dup=1&dup=2&encoded=a+b'
def table_markup(target=TARGET):
    return '<div class="casino-list" data-dept="cz" data-st="site" data-p="88" data-sfx="tag=a%2Fb"><a class="js-go-link" data-slug="alpha" href="'+html.escape(target,quote=True)+'"><img alt="Alpha"></a><a class="js-go-link" data-slug="alpha" href="'+html.escape(target,quote=True)+'">Play</a><script>alert(1)</script><a href="https://unrelated.example/">Unrelated</a></div>'


class AffiliateTests(unittest.TestCase):
    def test_provider_style_contents_do_not_become_article_text(self):
        original=parse_table(table_markup(),88,'https://restored.example','/casino-rating/')
        styled='\ufeff<style>.hds-logo{width:150px!important}</style>'+table_markup().replace('</div>','<style>@media(max-width:768px){body{color:red}}</style></div>')
        table=parse_table(styled,88,'https://restored.example','/casino-rating/')
        soup=BeautifulSoup(table['html'],'lxml')
        self.assertFalse(soup.select('style,script'))
        self.assertNotIn('width:150px',soup.get_text())
        self.assertNotIn('@media',soup.get_text())
        self.assertNotIn('\ufeff',table['html'])
        self.assertEqual(table['offers'],original['offers'])
        fragment=parse_table(styled.replace('<div ', '<section ').replace('</div>', '</section>'),88,'https://restored.example','/casino-rating/')
        self.assertEqual(len(BeautifulSoup(fragment['html'],'lxml').select('a.js-go-link')),2)

    def test_exact_target_brand_duplicate_keys_and_multiple_table_ids(self):
        table=parse_table(table_markup(),88,'https://restored.example','/casino-rating/')
        offer=table['offers'][0]
        self.assertEqual(len(table['offers']),1)
        self.assertEqual(offer['target'],TARGET)
        self.assertEqual(offer['brand'],'Alpha')
        self.assertEqual(offer['links'],2)
        self.assertEqual(offer['context_issues'],[])
        self.assertEqual([v for k,v in offer['parameters'] if k=='dup'],['1','2'])
        soup=BeautifulSoup(table['html'],'lxml')
        self.assertEqual({a['href'] for a in soup.select('a[href]')},{offer['go']})
        self.assertEqual(offer['go'],'/go/alpha')
        self.assertTrue(all(a.get('referrerpolicy')=='same-origin' for a in soup.select('a[href]')))
        self.assertFalse(soup.select('script'))
        for table_id,route in ((99,'/casino-rating/'),(88,'/casino-bonuses/')):
            other=parse_table(table_markup(),table_id,'https://restored.example',route)
            self.assertNotEqual(offer['id'],other['offers'][0]['id'])

    def test_invalid_destination_and_alias_fail_closed(self):
        for target in ('javascript:alert(1)','https://127.0.0.1/a','http://example.com/','https://user:pw@example.com/','https://example.com/%0d%0aX:test','https://host.local/x'):
            with self.assertRaises((RestorationError,ValueError)):
                validate_target(target)
        with self.assertRaises(RestorationError):
            parse_table(table_markup().replace('data-slug="alpha"','data-slug="../beta"'),88,'https://restored.example','/')

    def test_conflicting_or_missing_provider_parameters_never_autofixed(self):
        for target in (TARGET+'&dept=other',TARGET.replace('&p=88','')):
            offer=parse_table(table_markup(target),88,'https://restored.example','/')['offers'][0]
            self.assertTrue(offer['context_issues'])
            self.assertEqual(offer['target'],target)

    def test_provider_520_regression_and_documented_tracking_aliases(self):
        client=Mock();client.get.return_value.status_code=200
        client.get.return_value.text=table_markup();client.get.return_value.content=table_markup().encode()
        fetch_table(88,'https://restored.example','/casino-rating/',client)
        self.assertEqual(client.get.call_args.kwargs['headers']['User-Agent'],USER_AGENT)
        self.assertIn('cb',client.get.call_args.kwargs['params'])
        target='https://gmbl.guru/lldrp?st=Drops&pr=All-Drops&d=mpd&net=network&br=alpha&g=czech-republic&s=restored.example&pg=restored.example/casino-rating/'
        markup=table_markup(target).replace('data-dept="cz"','data-dept="mpd"').replace('data-st="site"','data-st="drops"').replace('data-p="88"','data-p="All-Drops"').replace('data-sfx="tag=a%2Fb"','data-sfx=""')
        offer=parse_table(markup,88,'https://restored.example','/casino-rating/')['offers'][0]
        self.assertEqual(offer['context_issues'],[])
        self.assertEqual(offer['target'],target)
        for bad in (target.replace('br=alpha','br=beta'),target+'&br=alpha',target.replace('d=mpd','d=other'),target.replace('pg=restored.example/','pg=other.example/')):
            row=parse_table(markup.replace(html.escape(target,quote=True),html.escape(bad,quote=True)),88,'https://restored.example','/casino-rating/')['offers'][0]
            self.assertTrue(row['context_issues'],bad)

    def test_go_routes_ignore_cookies_and_client_target_and_require_matching_brand(self):
        with tempfile.TemporaryDirectory() as folder:
            pipeline=Pipeline(Path(folder),client=FixtureArchive())
            build=pipeline.finish(pipeline.run(request()),'keep')
            first=parse_table(table_markup(),88,build.request.origin,'/casino-rating/')['offers'][0]
            second=parse_table(table_markup(TARGET.replace('p=88','p=99')),99,build.request.origin,'/casino-bonuses/')['offers'][0]
            save_json(build.root/'affiliate-manifest.json',{'offers':[first,second]})
            server=PreviewServer(build,audit_mode=True)
            try:
                for offer in (first,second):
                    headers={'Referer':server.origin+offer['source_route']}
                    r=requests.get(server.origin+offer['go'],headers=headers,cookies={'cwn_u':'forged','cwn_uslug':'beta'},allow_redirects=False)
                    self.assertEqual(r.status_code,302);self.assertEqual(r.headers['Location'],offer['target'])
                    for suffix in ('/?offer=forged','?offer='+first['id']+'&url=https://evil.example/'):
                        variant=requests.get(server.origin+offer['go']+suffix,headers=headers,allow_redirects=False)
                        self.assertEqual(variant.status_code,301)
                        self.assertEqual(variant.headers['Location'],server.origin+offer['go'])
                    wrong=requests.get(server.origin+'/go/beta?offer='+offer['id'],allow_redirects=False)
                    self.assertEqual(wrong.status_code,404)
                self.assertEqual(requests.get(server.origin+'/go/alpha',allow_redirects=False).status_code,404)
                self.assertEqual(requests.get(server.origin+'/go/alpha',headers={'Referer':'https://evil.example/casino-rating/'},allow_redirects=False).status_code,404)
            finally:
                server.stop()

    def test_clean_alias_cannot_select_conflicting_campaigns_on_same_page(self):
        rows=[parse_table(table_markup(target),table,'https://restored.example','/casino-rating/')['offers'][0]
              for table,target in ((88,TARGET),(99,TARGET.replace('p=88','p=99')))]
        for ref in ('','https://restored.example/casino-rating/'):
            self.assertIsNone(resolve_offer(rows,'alpha','https://restored.example',ref))
        self.assertIsNone(resolve_offer(rows,'../alpha','https://restored.example'))

    def test_same_campaign_clean_link_preserves_page_tracking_and_direct_visits(self):
        rows=[]
        for route in ('/casino-bonuses/','/casino-rating/'):
            target='https://gmbl.guru/lldrp?st=Drops&pr=All-Drops&d=mpd&net=network&br=alpha&g=czech-republic&s=restored.example&pg=restored.example'+route
            markup=table_markup(target).replace('data-dept="cz"','data-dept="mpd"').replace('data-st="site"','data-st="drops"').replace('data-p="88"','data-p="All-Drops"').replace('data-sfx="tag=a%2Fb"','data-sfx=""')
            rows.append(parse_table(markup,88,'https://restored.example',route)['offers'][0])
        for row in rows:
            self.assertEqual(resolve_offer(rows,'alpha','https://restored.example','https://restored.example'+row['source_route']+'?ignored=1')['target'],row['target'])
        self.assertEqual(resolve_offer(rows[::-1],'alpha','https://restored.example')['target'],rows[0]['target'])
        changed=[dict(row) for row in rows]
        changed[1]['target']=changed[1]['target'].replace('net=network','net=other')
        self.assertIsNone(resolve_offer(changed,'alpha','https://restored.example'))

    @patch('drop_restorer.core.affiliates.public_host')
    def test_reachability_is_separate_from_brand_confirmation(self,_):
        offer={'id':'fixture','target':TARGET};client=Mock()
        client.get.return_value.status_code=403;client.get.return_value.headers={}
        self.assertEqual(verify_offer(offer,client)['status'],'unknown')
        client.get.return_value.status_code=200
        self.assertEqual(verify_offer(offer,client)['status'],'reachable')
        client.get.return_value.status_code=302;client.get.return_value.headers={'Location':TARGET}
        self.assertEqual(verify_offer(offer,client)['status'],'fail')


if __name__=='__main__':
    unittest.main()
