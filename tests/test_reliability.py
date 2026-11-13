"""Offline regression cases. Headlines here are synthetic unless noted as stored titles."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from datetime import datetime, timezone

from src import main, reliability as q

NOW = '2026-11-13T08:00:00+00:00'
SETTINGS = json.loads(main.SETTINGS_PATH.read_text())
SCORING = json.loads(main.SCORING_PATH.read_text())


def record(title='Intel Kiryat Gat plant explosion', url='https://example.org/story', date=NOW, **kw):
    return {'incident_id': 'PSI-TEST-1', 'title': title, 'stream': 'Semiconductor', 'reported_at': date,
            'status': 'Active', 'review_status': 'Confirmed', 'summary': title, 'scores': {},
            'sources': [{'url': url, 'title': title, 'domain': 'example.org', 'published_at': date}], **kw}


def article(title='Intel Kiryat Gat plant gas leak', url='https://example.org/story'):
    return {'title': title, 'url': url, 'source_domain': 'example.org', 'published_at': NOW}


class ScreeningTests(unittest.TestCase):
    def test_positive_events(self):
        for title in [
            'SK hynix Cheongju fab fluorine leak prompts evacuation',
            'Gas cabinet fire at semiconductor wafer fab',
            'Micron cleanroom evacuated after silane fire',
            'Explosion at a Mississippi chemical plant injures workers',
            'Refinery toxic gas release prompts shelter-in-place',
            'Thermal runaway at battery plant forces evacuation',
            'Molten metal explosion at steel foundry injures workers',
            'Hokkaido University student dies following hydrofluoric acid exposure',
            'Intel shares fall after fatal explosion at Kiryat Gat chip plant',
            'Intel plant reports suspected gas leak',
            'No fire reported at Tehran refinery after flaring',
        ]:
            with self.subTest(title=title): self.assertEqual(q.screen(title)['decision'], 'Retain')

    def test_negative_business_and_ambiguous_terms(self):
        # Includes titles actually present in the repository's November diagnostics.
        for title in [
            'TSMC, Samsung, and SK Hynix: Why This Investment Trust Is Reducing Its AI Exposure',
            'Semiconductor and AI Revolution',
            '7 Best Semiconductor ETFs to Buy for 2026',
            'Chip Stocks Have Been Hit Hard. Nvidia and 4 More at Fire-Sale Prices.',
            'Electronic Grade Hydrofluoric Acid Market in India: Size, Trends and Forecast to 2035',
            'CGA launches new semiconductor gas standards library',
            'Nippon Sanso expands South Korea semiconductor gas capacity',
            'Burckhardt Compression secures orders for semiconductor hydrogen application',
            'Nearly 4 in 10 Korean industrial technology leaks involve semiconductors',
            'Oregon Foundry Fire: Historic Vacant Building Destroyed',
            'City of Oregon Mayor addresses fire at former Paragon Foundry',
            'Cloud Foundry releases new software',
            'New font foundry release',
            'Foundry VTT game announces explosion effects',
            'Fire destroys the Foundry restaurant',
            'XLK vs. SOXX: Broad Tech Exposure or Semiconductor Bet?',
            'Buy TI and NVIDIA for Exposure to Semiconductor Boom',
            'Goldman Sachs Ties Semiconductor Exposure to 54% of Return Gaps',
            'xETFs Launches KSMH to Provide Targeted Exposure to Korea’s AI Semiconductor Ecosystem',
            'Hydrogen Mass Spectrometry Leak Detectors Market To 2035',
            'Largest attack on Moscow sees Ukraine fire hundreds of drones',
        ]:
            with self.subTest(title=title): self.assertEqual(q.screen(title)['decision'], 'Out of scope')

    def test_foundry_is_not_automatically_semiconductor(self):
        self.assertEqual(q.screen('Explosion at metal foundry injures three')['stream'], 'Cross-Industry')

    def test_business_sidebar_does_not_rescue_article(self):
        self.assertEqual(q.screen('Semiconductor ETF growth', body='Related: explosion at chemical plant')['decision'], 'Out of scope')

    def test_ambiguous_hazard_stays_in_review(self):
        self.assertEqual(q.screen('Intel investigates chemical exposure')['decision'], 'Needs context')

    def test_word_boundaries(self):
        self.assertEqual(main.keyword_hits('semiconductor production is fabulous', ['duct', 'fab', 'fire']), [])
        self.assertEqual(main.keyword_hits('A fab exhaust duct fire', ['duct', 'fab', 'fire']), ['duct','fab','fire'])

    def test_boilerplate_not_summary(self):
        s='Comprehensive up-to-date news coverage, aggregated from sources all over the world by Google News.'
        self.assertEqual(q.clean_content(s), '')

    def test_no_auto_confirmation(self):
        self.assertEqual(main.review_status_for(5,SETTINGS), 'Needs review')
        view=q.build_review_index({'incidents':[record()]},{})['incidents'][0]
        self.assertEqual(view['confirmed_facts'], [])
        self.assertIn('not independent verification',view['legacy_status_note'])

    def test_government_spoof_rejected(self):
        self.assertFalse(main.is_government_domain('agency.gov.evil.com'))
        self.assertTrue(main.is_government_domain('mom.gov.sg'))


class DuplicateTests(unittest.TestCase):
    def test_same_url_tracking_canonicalization(self):
        a=record(url='https://example.org/story?utm_source=x&id=2#section')
        b=record(url='https://example.org/story?id=2')
        self.assertTrue(main.same_incident(a,b))
        self.assertEqual(q.match_records(a,b)['kind'],'same_report')

    def test_cross_publisher_requires_review(self):
        a=record('Intel Kiryat Gat plant explosion injures workers',url='https://a.example/one')
        b=record('Explosion injures workers at Intel plant in Kiryat Gat',url='https://b.example/two')
        self.assertEqual(q.match_records(a,b)['kind'],'possible_duplicate')
        self.assertFalse(main.same_incident(a,b))

    def test_same_words_different_dates_sites_or_companies_do_not_merge(self):
        a=record()
        for b in [record(date='2026-09-01'),record(incident_date='2026-11-12'),record('Intel Icheon plant explosion',url='https://different.example/1'),record('Micron Kiryat Gat plant explosion',url='https://different.example/2')]:
            if b.get('incident_date'): a['incident_date']='2026-11-13'
            else: b['sources'][0]['url']='https://different.example/3'
            with self.subTest(b=b): self.assertIsNone(q.match_records(a,b))

    def test_followup_leak_is_not_prior_explosion(self):
        a=record('Intel plant reports suspected gas leak less than a week after fatal explosion',url='https://a.example/leak')
        b=record('Intel plant fatal explosion',url='https://b.example/blast')
        self.assertIsNone(q.match_records(a,b))

    def test_generic_titles_not_grouped(self):
        self.assertIsNone(q.match_records(record('Chemical plant fire',url='https://a.example/1'),record('Chemical plant fire',url='https://b.example/2')))

    def test_discovery_key_retains_recurring_titles(self):
        self.assertNotEqual(main.article_key(article(url='https://a.example/1')),main.article_key(article(url='https://a.example/2')))

    def test_source_update_preserves_previous_statement(self):
        a=article();candidate=main.build_candidate(a,'Semiconductor',SCORING,SETTINGS,{},NOW,{},1,1)
        records=[]
        main.merge_candidate(candidate,records,NOW,SETTINGS)
        changed=copy.deepcopy(candidate);changed['sources'][0]['description']='Updated source statement'
        changed['sources'][0]['updated_at']=NOW
        result,item=main.merge_candidate(changed,records,NOW,SETTINGS)
        self.assertEqual(result,'updated');self.assertEqual(len(records),1)
        self.assertEqual(item['sources'][0]['versions'][0]['description'],'')
        self.assertEqual(item['sources'][0]['updated_at'],NOW)


    def test_publisher_resolution_updates_same_source(self):
        a=article(url='https://news.google.com/rss/articles/one')
        first=main.build_candidate(a,'Semiconductor',SCORING,SETTINGS,{},NOW,{},1,1)
        records=[];main.merge_candidate(first,records,NOW,SETTINGS)
        resolved=main.build_candidate(a,'Semiconductor',SCORING,SETTINGS,{'final_url':'https://example.org/original','retrieval_status':'retrieved'},NOW,{},1,1)
        main.merge_candidate(resolved,records,NOW,SETTINGS)
        self.assertEqual(len(records),1);self.assertEqual(len(records[0]['sources']),1)
        self.assertEqual(records[0]['sources'][0]['original_url'],'https://example.org/original')
        self.assertEqual(records[0]['sources'][0]['versions'][0]['url'],a['url'])


class DateAndEvidenceTests(unittest.TestCase):
    def test_naive_invalid_and_missing_dates(self):
        self.assertEqual(q.parse_date('2026-11-13').tzinfo,timezone.utc)
        for value in [None,'nonsense','2026-99-99']:
            self.assertIsNone(q.parse_date(value))

    def test_event_date_precedes_report_for_window(self):
        self.assertEqual(main.reported_datetime(record(incident_date='2020-01-01')).year,2020)
        self.assertEqual(main.reported_datetime(record()).year,2026)

    def test_seen_date_is_not_publication(self):
        response=Mock(status_code=200);response.json.return_value={'articles':[{'url':'https://a.example/1','title':'Intel plant fire','seendate':'20261113T080000Z'}]}
        with patch.object(main.SESSION,'get',return_value=response):
            s=main.fetch_gdelt('query','test',1)[0]
        self.assertIsNone(s['published_at']);self.assertTrue(s['discovered_at'])

    def test_citations_and_uncertainties(self):
        r=record('Suspected gas leak at Intel Kiryat Gat plant injures workers')
        p=q.evidence_projection(r)
        self.assertTrue(p['preliminary_or_disputed']);self.assertTrue(p['reported_consequences'])
        self.assertEqual(p['company_mentions'][0]['value'],'Intel')
        self.assertEqual(p['location_mentions'][0]['value'],'Kiryat Gat')
        self.assertEqual(p['confirmed_facts'],[])
        self.assertEqual(p['reported_claims'][0]['source_url'],'https://example.org/story')

    def test_location_not_inferred_from_source_country(self):
        r=record('Chemical plant explosion');r['sources'][0]['source_country']='Singapore'
        self.assertEqual(q.evidence_projection(r)['location_mentions'],[])

    def test_review_requires_provenance(self):
        r=record();review={'status':'Reviewed','reviewed_by':'Test reviewer','reviewed_at':NOW,'notes':'Synthetic test only','confirmed_facts':[{'text':'Test fact','source_url':r['sources'][0]['url']}]}
        self.assertTrue(q.validate_reviews({'reviews':{r['incident_id']:review}},[r]))
        for field in ['reviewed_by','reviewed_at','notes']:
            bad=copy.deepcopy(review);bad.pop(field)
            with self.assertRaises(ValueError):q.validate_reviews({'reviews':{r['incident_id']:bad}},[r])
        bad=copy.deepcopy(review);bad['confirmed_facts'][0]['source_url']='https://unrelated.example/'
        with self.assertRaises(ValueError):q.validate_reviews({'reviews':{r['incident_id']:bad}},[r])

    def test_schema_has_no_automatic_tiers(self):
        item=q.build_review_index({'incidents':[record()]},{})['incidents'][0]
        self.assertTrue(item['tier'].startswith('Not assigned'))

    def test_url_schemes(self):
        for url in ['javascript:alert(1)','data:text/html,hi','file:///etc/passwd','https://user:pass@example.org']:
            self.assertFalse(q.safe_url(url))
        self.assertTrue(q.safe_url('https://example.org/news'))


class FeedAndPreservationTests(unittest.TestCase):
    def collect(self, gdelt, rss):
        settings=copy.deepcopy(SETTINGS);settings['queries']=[{'name':'test','query':'x','stream':'Semiconductor'}]
        with patch.object(main,'fetch_gdelt',side_effect=gdelt if isinstance(gdelt,Exception) else None,return_value=gdelt),patch.object(main,'fetch_google_news_rss',side_effect=rss if isinstance(rss,Exception) else None,return_value=rss),patch.object(main,'fetch_article_context',return_value={'retrieval_status':'fetch_failed'}),patch.object(main,'utc_now',return_value=datetime(2026,11,13,tzinfo=timezone.utc)):
            return main.collect_candidates(settings,SCORING,NOW,{})

    def test_partial_failure_retains_rss_candidate(self):
        records,diag=self.collect(main.DiscoveryError('GDELT HTTP 429'),[article()])
        self.assertEqual(len(records),1);self.assertEqual(diag['health'],'degraded')
        self.assertEqual(diag['article_retrieval'],{'fetch_failed':1})

    def test_partial_failure_retains_gdelt_candidate(self):
        records,diag=self.collect([article()],main.DiscoveryError('RSS unavailable'))
        self.assertEqual(len(records),1);self.assertEqual(diag['health'],'degraded')

    def test_both_fail_is_explicit(self):
        records,diag=self.collect(main.DiscoveryError('GDELT down'),main.DiscoveryError('RSS down'))
        self.assertEqual(records,[]);self.assertEqual(diag['health'],'unavailable')

    def test_empty_success_is_not_outage(self):
        records,diag=self.collect([],[])
        self.assertEqual(records,[]);self.assertEqual(diag['health'],'healthy')

    def test_invalid_rss_html_and_gdelt_payload_fail(self):
        response=Mock(status_code=200,content=b'<html>Unavailable</html>');response.json.return_value={}
        with patch.object(main.SESSION,'get',return_value=response):
            with self.assertRaises(main.DiscoveryError):main.fetch_google_news_rss('x','x',1)
            with self.assertRaises(main.DiscoveryError):main.fetch_gdelt('x','x',1)

    def test_google_wrapper_is_not_article_enrichment(self):
        response=Mock(status_code=200,url='https://news.google.com/articles/x')
        with patch.object(main.SESSION,'get',return_value=response):result=main.fetch_article_context(response.url)
        self.assertEqual(result['retrieval_status'],'aggregator_only');self.assertEqual(result['description'],'')

    def test_http_failure_does_not_discard_headline(self):
        candidate=main.build_candidate(article(),'Semiconductor',SCORING,SETTINGS,{'retrieval_status':'fetch_failed'},NOW,{},1,1)
        self.assertIsNotNone(candidate);self.assertEqual(candidate['sources'][0]['retrieval_status'],'fetch_failed')

    def test_snapshots_never_overwrite_same_day(self):
        with tempfile.TemporaryDirectory() as d,patch.object(main,'HISTORY_DIR',Path(d)):
            for n in range(3):main.snapshot_history({'n':n},{},{},NOW)
            snapshots=list(Path(d).glob('*-incidents.json'))
            self.assertEqual(len(snapshots),3)
            self.assertEqual(json.loads((Path(d)/'2026-11-13-incidents.json').read_text()),{'n':0})

    def test_total_failure_keeps_database_and_report_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); db=root/'incidents.json'; report=root/'latest_report.json';diag=root/'diagnostics.json'
            db.write_text(json.dumps({'last_run':'2026-11-09','incidents':[record()]}));report.write_text('{"old_report": true}')
            before=(db.read_bytes(),report.read_bytes())
            with patch.multiple(main,INCIDENTS_PATH=db,REPORT_PATH=report,DIAGNOSTICS_PATH=diag,REVIEW_INDEX_PATH=root/'review.json',REVIEWS_PATH=root/'reviews.json',UNKNOWN_SOURCES_PATH=root/'unknown.json',PUBLIC_DATA_DIR=root/'public'),patch.object(main,'collect_candidates',return_value=([],{'generated_at':NOW,'health':'unavailable','providers':{}})):
                main.main()
            self.assertEqual(before,(db.read_bytes(),report.read_bytes()))
            self.assertEqual(json.loads(diag.read_text())['health'],'unavailable')
            self.assertEqual(json.loads((root/'review.json').read_text())['data_as_of'],'2026-11-09')

    def test_projection_keeps_all_original_ids_and_content(self):
        db=json.loads(main.INCIDENTS_PATH.read_text());before=copy.deepcopy(db)
        result=q.build_review_index(db,{})
        self.assertEqual(db,before)
        self.assertEqual({x['incident_id'] for x in result['incidents']},{x['incident_id'] for x in db['incidents']})
        self.assertEqual(len(result['incidents']),len(db['incidents']))


    def test_outage_carries_forward_last_known_success(self):
        previous={'generated_at':'2026-11-09','providers':{'GDELT':{'queries_succeeded':2}}}
        current={'generated_at':NOW,'providers':{'GDELT':{'queries_succeeded':0,'queries_failed':1}}}
        q.provider_health(current,previous)
        self.assertEqual(current['last_success_at'],'2026-11-09')
        self.assertEqual(current['providers']['GDELT']['last_success_at'],'2026-11-09')

    def test_successful_main_run_and_repeat_preserve_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);db=root/'incidents.json';report=root/'latest_report.json'
            original=record(url='https://example.org/old',date='2026-01-01')
            original['incident_id']='PSI-2026-0001'
            db.write_text(json.dumps({'last_run':'2026-11-09','incidents':[original]}))
            candidate=main.build_candidate(article(),'Semiconductor',SCORING,SETTINGS,{},NOW,{},1,1)
            diag={'generated_at':NOW,'health':'healthy','providers':{}}
            with patch.multiple(main,INCIDENTS_PATH=db,REPORT_PATH=report,DIAGNOSTICS_PATH=root/'diagnostics.json',REVIEW_INDEX_PATH=root/'review.json',REVIEWS_PATH=root/'reviews.json',UNKNOWN_SOURCES_PATH=root/'unknown.json',PUBLIC_DATA_DIR=root/'public',HISTORY_DIR=root/'history'),patch.object(main,'collect_candidates',return_value=([candidate],diag)),patch.object(main,'iso_now',return_value=NOW):
                main.main();main.main()
            saved=json.loads(db.read_text())['incidents']
            self.assertEqual(len(saved),2)
            old=next(i for i in saved if i['incident_id']==original['incident_id'])
            self.assertEqual(old['sources'],original['sources']);self.assertEqual(old['status'],'Archived')
            self.assertEqual(old['legacy_review_status'],'Confirmed')
            self.assertEqual(len(list((root/'history').glob('*-incidents.json'))),2)
            self.assertEqual(len(json.loads((root/'review.json').read_text())['incidents']),2)


if __name__=='__main__':unittest.main()
