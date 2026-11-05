import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from monitor import allows,pdf_links,latest_candidates,fund_period,coverage,load_profiles,Publisher,refresh_house,merge_editions,run_once,publish_latest_pdfs
from fund_registry import FUND_HOUSES
from datetime import datetime
from monitor import prism_periods
from output_schema import normalize_extracted_funds

class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.profile={'id':'test','fund_house':'360 ONE Asset','pages':['https://amc.example/downloads/'],
            'allowed_prefixes':['https://amc.example/','https://cdn.example/factsheets/'],
            'strategy':'html','pdf_pattern':'factsheet','known_documents':[]}

    def test_official_allowlist_rejects_mirrors_downgrades_and_traversal(self):
        for url in ['http://amc.example/a.pdf','https://amc.example.evil/a.pdf',
                    'https://mirror.example/a.pdf','https://cdn.example/private/a.pdf',
                    'https://amc.example/%2e%2e/private.pdf','https://user:pass@amc.example/a.pdf']:
            self.assertFalse(allows(self.profile,url),url)
        self.assertTrue(allows(self.profile,'https://cdn.example/factsheets/a.pdf'))

    def test_links_preserve_spaces_base_tags_and_json_escapes(self):
        markup='<base href="/reports/"><a href="Fund Factsheet.pdf">Latest</a>'
        markup+=r'{"url":"https:\/\/cdn.example\/factsheets\/September 2026.pdf"}'
        links=pdf_links(markup,self.profile['pages'][0])
        self.assertIn('https://amc.example/reports/Fund Factsheet.pdf',links)
        self.assertIn('https://cdn.example/factsheets/September 2026.pdf',links)
        self.assertEqual(pdf_links('{"file_name":"Factsheet September 2026.pdf"}',self.profile['pages'][0]),[])

    def test_separate_scheme_pdfs_keep_their_own_latest_month(self):
        urls=['https://amc.example/Hybrid_Factsheet_September_2026.pdf',
              'https://amc.example/Hybrid_Factsheet_August_2026.pdf',
              'https://amc.example/Equity_Ex_Top_100_Factsheet_August_2026.pdf',
              'https://amc.example/Equity_Factsheet.pdf']
        selected=latest_candidates([{'url':url} for url in urls])
        self.assertEqual({x['url'] for x in selected},set(urls)-{urls[1]})

    def test_reporting_date_uses_data_month_and_rejects_ambiguous_pdf(self):
        self.assertEqual(fund_period({'Portfolio Date':'30th June 2026'},'July 2026 cover'),'2026-06')
        self.assertEqual(fund_period({'Portfolio Date':'31/08/2026'},''),'2026-08')
        self.assertEqual(fund_period({},'October 2026\nPortfolio Holding Details as on September 30, 2026'),'2026-09')
        with self.assertRaises(ValueError):fund_period({},'Portfolio as on August 31, 2026\nPortfolio as on September 30, 2026')

    def test_prism_looks_beyond_empty_previous_month_and_crosses_fiscal_year(self):
        self.assertEqual(list(prism_periods(datetime(2026,10,16),3)),
                         [('2026-2027','October'),('2026-2027','September'),('2026-2027','August')])
        self.assertEqual(list(prism_periods(datetime(2027,4,2),3)),
                         [('2027-2028','April'),('2026-2027','March'),('2026-2027','February')])

    def test_platinum_uses_complete_catalog_instead_of_stale_fund_page(self):
        from urllib.parse import parse_qs,urlsplit
        profile=next(p for p in load_profiles(Path(__file__).with_name('official_sources.json')) if p['id']=='platinum')
        client=Publisher(profile,False)
        responses=[{'children':[{'totalPages':2,'forms':[{'link':'https://miraeassetetf.co.in/wp-content/uploads/2026/09/Platinum-SIF-Factsheet-September-2026.pdf'}]}]},
                   {'children':[{'totalPages':2,'forms':[{'link':'https://miraeassetetf.co.in/wp-content/uploads/2026/09/Platinum-SIF-Factsheet-August-2026.pdf'},
                                                        {'link':'https://mirror.example/Platinum-SIF-Factsheet.pdf'}]}]}]
        with patch.object(client,'get_json',side_effect=responses) as mocked:
            found=client.discover()
        self.assertEqual(len(found),2)
        self.assertTrue(found[0]['url'].endswith('September-2026.pdf'))
        for expected,call in enumerate(mocked.call_args_list,1):
            endpoint=parse_qs(urlsplit(call.args[0]).query)['endpoint'][0]
            self.assertEqual(parse_qs(urlsplit(endpoint).query)['pageNo'],[str(expected)])
        client.session.close()

    def test_redirect_to_mirror_is_rejected_before_second_request(self):
        client=Publisher(self.profile,False)
        class Response:
            is_redirect=True;status_code=302;headers={'Location':'https://mirror.example/factsheet.pdf'}
            def close(self):pass
        with patch.object(client.session,'request',return_value=Response()) as mocked:
            with self.assertRaises(ValueError):client.request('https://amc.example/factsheet.pdf')
            self.assertEqual(mocked.call_count,1)
        client.session.close()

    def test_failed_discovery_preserves_last_good_and_does_not_mark_latest_verified(self):
        name='DynaSIF Equity Long-Short Fund'
        old={name:{'Factsheet Source':{'Reporting Month':'2026-08'},'Extraction Validation':{'Status':'passed'}}}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'360 ONE Asset.json').write_text(json.dumps(old))
            with patch.object(Publisher,'discover',side_effect=ValueError('AMC blocked')):
                status,funds=refresh_house(self.profile,root,{},False)
            self.assertEqual(funds,old);self.assertFalse(status['listing_verified'])
            self.assertIn('AMC blocked',status['errors'][0])

    def test_all_registered_schemes_and_publishers_are_covered(self):
        profiles=load_profiles(Path(__file__).with_name('official_sources.json'))
        self.assertEqual({p['fund_house'] for p in profiles},set(FUND_HOUSES.values()))
        report=coverage(profiles,{'sources':{}},{})
        self.assertEqual(report['Registered Schemes'],34)
        self.assertEqual(report['Extracted Schemes'],0)

    def test_primary_and_regular_editions_preserve_both_return_series(self):
        import copy
        primary={'Factsheet Source':{'Reporting Month':'2026-09','PDF URL':'https://amc.example/September.pdf','SHA256':'a'},
                 'Scheme Performance':[{'Plan':'Direct','Return':2}]}
        regular={'Factsheet Source':{'Reporting Month':'2026-09','PDF URL':'https://amc.example/September_Regular_123.pdf','SHA256':'b'},
                 'Scheme Performance':[{'Plan':'Regular','Return':1}]}
        for first,second in [(primary,regular),(regular,primary)]:
            result=merge_editions(copy.deepcopy(first),copy.deepcopy(second))
            self.assertEqual(result['Scheme Performance'],primary['Scheme Performance'])
            self.assertEqual(result['Additional Factsheet Editions'][0]['Scheme Performance'],regular['Scheme Performance'])
        revised=copy.deepcopy(primary);revised['Factsheet Source']['SHA256']='c'
        self.assertEqual(merge_editions(primary,revised)['Factsheet Source']['SHA256'],'c')

    def test_unexpected_publisher_error_retains_verified_data_in_combined_output(self):
        import hashlib
        profiles=load_profiles(Path(__file__).with_name('official_sources.json'))
        name='DynaSIF Equity Long-Short Fund'
        data={name:{'AMC / Fund House':'360 ONE Asset',
               'Factsheet Source':{'Reporting Month':'2026-08','PDF URL':'https://www.360.one/factsheet.pdf'},
               'Extraction Validation':{'Status':'passed'}}}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);file=root/'360 ONE Asset.json';file.write_text(json.dumps(data))
            state={'sources':{'dyna':{'json_sha256':hashlib.sha256(file.read_bytes()).hexdigest()}}}
            (root/'monitor_state.json').write_text(json.dumps(state))
            with patch('monitor.refresh_house',side_effect=RuntimeError('Unexpected publisher failure')):
                report=run_once(profiles,root,False)
            self.assertEqual(report['Extracted Schemes'],1)
            saved=json.loads((root/'latest/extraction'/ (name+'.json')).read_text())
            self.assertEqual(saved,normalize_extracted_funds(data))
            self.assertFalse((root/'all_sif_schemes.json').exists())

    def test_latest_folder_replaces_history_and_keeps_each_schemes_latest_month(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def data(name,month,content,url):
                path=root/(month+'-'+name+'.pdf');path.write_bytes(content)
                return {'AMC / Fund House':'Edelweiss Mutual Fund','Extraction Validation':{'Status':'passed'},
                    'Factsheet Source':{'Reporting Month':month,'Local PDF':str(path),
                        'SHA256':hashlib.sha256(content).hexdigest(),'PDF URL':url}}
            hybrid='Altiva Hybrid Long-Short Fund';equity='Altiva Equity Ex-Top 100 Long-Short Fund'
            old=data('old','2026-08',b'aug hybrid','https://amc.example/hybrid.pdf')
            entries=publish_latest_pdfs(root,{hybrid:old},{})
            old_copy=root/'latest'/entries[0]['File Name']
            unrelated=root/'latest/notes.pdf';unrelated.write_bytes(b'user file')
            new=data('new','2026-09',b'sep hybrid','https://amc.example/hybrid.pdf')
            eq=data('equity','2026-08',b'aug equity','https://amc.example/equity_ex_top_100.pdf')
            entries=publish_latest_pdfs(root,{hybrid:new,equity:eq},{})
            self.assertEqual(len(entries),2)
            self.assertEqual({tuple(e['Reporting Months']) for e in entries},{('2026-08',),('2026-09',)})
            self.assertFalse(old_copy.exists());self.assertTrue(unrelated.exists())
            self.assertTrue(Path(old['Factsheet Source']['Local PDF']).exists())
            files=root/'latest/extraction'
            self.assertEqual(json.loads((files/(hybrid+'.json')).read_text())[hybrid]['Factsheet Source']['Reporting Month'],'2026-09')
            self.assertTrue((files/(equity+'.json')).is_file())

    def test_refresh_uses_temporary_downloads_and_keeps_no_archive(self):
        import pymupdf
        name='DynaSIF Equity Long-Short Fund'
        with pymupdf.open() as pdf:
            page=pdf.new_page();page.insert_text((30,40),name)
            content=pdf.tobytes()
        extracted={name:{'AMC / Fund House':'360 ONE Asset','Portfolio Date':'September 30, 2026',
                         'Extraction Validation':{'Status':'passed'}}}
        candidate={'url':'https://amc.example/Factsheet_September_2026.pdf','page_url':self.profile['pages'][0]}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(Publisher,'discover',return_value=[candidate]),patch.object(Publisher,'request',return_value=content),patch('monitor.extract_factsheet',return_value=extracted):
                result,funds=refresh_house(self.profile,root,{},False)
            self.assertFalse((root/'archive').exists())
            self.assertFalse(list(root.glob('.sif-refresh-*')))
            self.assertFalse((root/'360 ONE Asset.pdf').exists())
            self.assertTrue((root/'latest/360 ONE Asset.pdf').exists())
            file=root/'latest/extraction'/(name+'.json')
            self.assertTrue(file.exists());self.assertIn(name,result['scheme_files'])
            self.assertEqual(Path(funds[name]['Factsheet Source']['Local PDF']),root/'latest/360 ONE Asset.pdf')
            with patch.object(Publisher,'discover',return_value=[candidate]),patch.object(Publisher,'request',return_value=content),patch('monitor.extract_factsheet',side_effect=AssertionError('Unchanged validated PDF should use scheme cache')):
                cached,cached_funds=refresh_house(self.profile,root,result,False)
            self.assertEqual(cached['status'],'unchanged');self.assertIn(name,cached_funds)
            # Repeat through the persistent per-scheme cache without a legacy house JSON.
            with patch.object(Publisher,'discover',side_effect=ValueError('AMC blocked')):
                second,saved=refresh_house(self.profile,root,cached,False)
            self.assertIn(name,saved);self.assertTrue(file.exists())

if __name__=='__main__':unittest.main()
