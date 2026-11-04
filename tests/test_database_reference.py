import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import app_analysis as a
import database_reference as dbref
import reference_materials as refs
import tc_drafts as drafts
from test_app_analysis import apk


def database_bytes(folder):
    path = Path(folder) / 'fixture.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE SessionInfo(LevelNum INTEGER,EpisodeNum INTEGER,StageNum INTEGER,StepNum INTEGER,SessionOrd INTEGER,SessionType TEXT,SessionTitle TEXT,SessionSubTitle TEXT,StageTitle TEXT,StageTitleKr TEXT)')
        # Insert out of order to verify the user journey is explicitly ordered.
        db.executemany('INSERT INTO SessionInfo VALUES(?,?,?,?,?,?,?,?,?,?)', [
            (2,1,1,1,1,'단어','두 번째 레벨','','',''),
            (1,1,1,2,2,'문장','인사 문장','','',''),
            (1,1,1,1,1,'단어','인사 단어','','','')])
        db.execute('CREATE TABLE "quoted""table"(content TEXT, media BLOB)')
        db.execute('INSERT INTO "quoted""table" VALUES (?,?)', ('<script>example</script>', b'a'*100000))
        db.execute('CREATE VIEW hidden_view AS SELECT * FROM SessionInfo')
    return path.read_bytes()


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = database_bytes(self.temp.name)
        # Exercise the real parser on our fixture without starting a process.
        def inspect_fixture(command, **kwargs):
            self.assertEqual(command[:2], [sys.executable, '-I'])
            self.assertEqual(kwargs['timeout'], 20)
            self.assertEqual(Path(command[3]).name, 'reference.db')
            try:
                output = json.dumps(dbref.inspect_file(command[3])).encode()
                return subprocess.CompletedProcess(command, 0, output, b'')
            except sqlite3.DatabaseError:
                return subprocess.CompletedProcess(command, 1, b'', b'')
        process = patch.object(dbref.subprocess, 'run', side_effect=inspect_fixture)
        process.start()
        self.addCleanup(process.stop)
        self.store = a.AnalysisStore(Path(self.temp.name)/'analysis')
        self.sha = self.store.register(apk(), 'fixture.apk')[0]['sha256']
        self.refs = refs.ReferenceStore(self.store)
        self.ref = self.refs.add(self.sha, io.BytesIO(self.raw), 'curriculum.dat', 'database')[0]
        self.req = self.refs.add(self.sha, io.BytesIO('학습 화면의 시작 버튼으로 진입하고 다음 버튼으로 진행한다.'.encode()), 'spec.txt', 'requirements')[0]
        self.settings = dict(reference_ids=[self.ref['id'], self.req['id']], scope='1레벨 전부 학습하기', count=0, include_images=False)

    def test_readonly_summary_and_samples(self):
        preview = self.ref['preview']
        self.assertEqual(self.ref['sha256'], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(preview['table_count'], 2)
        self.assertNotIn('hidden_view', [u['label'] for u in preview['units']])
        self.assertIn('[BLOB]', json.dumps(preview))
        self.assertEqual(preview['curriculum']['levels'], [{'level':1,'sessions':2},{'level':2,'sessions':1}])
        self.assertEqual([s['StepNum'] for s in dbref.select_sessions(preview,'레벨 1 학습하기')], [1,2])
        self.assertEqual(len(dbref.select_sessions(preview,'각 레벨별 학습하기')),3)
        self.assertEqual(len(dbref.select_sessions(preview,'1레벨, 2레벨 학습하기')),3)

    def test_invalid_formats_unknown_levels_and_incomplete_scope(self):
        for data in (b'not a sqlite database', b'SQLite format 3\x00' + b'broken'):
            with self.assertRaises(a.AnalysisError):
                self.refs.add(self.sha,io.BytesIO(data),'bad.dat','database')
        for scope in ('99레벨 학습','1~3레벨 학습','레벨 학습'):
            with self.assertRaises(a.AnalysisError):
                dbref.select_sessions(self.ref['preview'],scope)
        preview = self.ref['preview']
        preview['curriculum']['complete'] = False
        with self.assertRaises(a.AnalysisError):
            dbref.select_sessions(preview,'1레벨 학습')
        with self.assertRaises(a.AnalysisError):
            self.refs.add(self.sha,io.BytesIO(b'notes'),'notes.txt','database')
        other = self.refs.add(self.sha,io.BytesIO(self.raw),'other.sqlite','other')[0]
        self.assertEqual(other['preview']['format'],'database')

    def test_scope_context_does_not_cut_off_later_levels(self):
        context, _, _ = drafts.make_context(self.store,self.refs,self.sha,self.settings,{})
        scope = context['database_scope']
        self.assertEqual(scope['session_count'],2)
        self.assertEqual(scope['levels'],[1])
        self.assertEqual([u['db_session'] for u in scope['units']],['SessionInfo/1/1/1/1','SessionInfo/1/1/1/2'])
        all_context, _, _ = drafts.make_context(self.store,self.refs,self.sha,{**self.settings,'scope':'각 레벨별 학습하기'},{})
        self.assertEqual(all_context['database_scope']['session_count'],3)
        self.assertEqual(all_context['database_scope']['units'][-1]['db_session'],'SessionInfo/2/1/1/1')

    def test_ordered_generation_preserves_support_and_reports_gaps(self):
        from test_tc_drafts import result
        calls = []
        def runner(instructions, context, *args):
            calls.append(context)
            self.assertNotIn('units', context['database_scope'])
            self.assertTrue(any(e['kind']=='requirements' for e in context['evidence']))
            if len(calls) == 2:
                return dict(summary='보완',questions=['문장 완료 조건 필요'],cases=[]),{}
            output = result(context)
            output['cases'][0]['source_ids'] = context['coverage_unit']['source_ids']
            output['cases'][0]['title'] = context['coverage_unit']['title']
            return output,{}
        service = drafts.DraftService(self.store,self.refs,runner,lambda:dict(configured=True,provider='fixture',model='fixture'))
        _, fingerprint, _ = service.context(self.sha,self.settings)
        batch = service.start(self.sha,{**self.settings,'context_hash':fingerprint,'consent':True})
        service.worker.join(5)
        self.assertFalse(service.worker.is_alive())
        job = service.get(self.sha,batch['id'])
        self.assertEqual(job['status'],'completed')
        self.assertEqual(len(calls),2)
        self.assertEqual(job['progress']['needs_material'],1)
        self.assertEqual(job['progress']['total'],2)
        self.assertEqual(calls[1]['previous_session'],calls[0]['coverage_unit']['title'])
        self.assertEqual(len(job['cases']),1)

    def test_http_registration_and_context(self):
        from flask import Flask
        app=Flask(__name__)
        @app.errorhandler(a.AnalysisError)
        def error(exc):
            return {'error':str(exc)},exc.status
        refs.register_routes(app,self.store)
        client=app.test_client()
        response=client.post('/api/app-analysis/builds/'+self.sha+'/references',data={'kind':'database','file':(io.BytesIO(self.raw),'http.sqlite3')})
        self.assertIn(response.status_code,(200,201))
        self.assertEqual(response.json['reference']['preview']['format'],'database')


class DatabaseProcessTests(unittest.TestCase):
    def test_timeout_is_reported_and_temp_file_removed(self):
        paths = []
        def timeout(command, **kwargs):
            paths.append(Path(command[3]))
            self.assertTrue(paths[-1].exists())
            self.assertEqual(kwargs['timeout'], 20)
            self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
            raise subprocess.TimeoutExpired(command, 20)
        with patch.object(dbref.subprocess, 'run', side_effect=timeout):
            with self.assertRaises(a.AnalysisError):
                dbref.database_preview(b'SQLite format 3\x00fixture')
        self.assertFalse(paths[0].exists())

    def test_failed_or_oversized_process_output_is_rejected(self):
        for code, output in ((1, b''), (0, b'x' * (8 * 1024 * 1024 + 1))):
            with self.subTest(code=code), patch.object(dbref.subprocess, 'run',
                    return_value=subprocess.CompletedProcess([], code, output, b'')):
                with self.assertRaises(a.AnalysisError):
                    dbref.database_preview(b'SQLite format 3\x00fixture')

    def test_invalid_json_is_not_accepted(self):
        with patch.object(dbref.subprocess, 'run',
                return_value=subprocess.CompletedProcess([], 0, b'not json', b'')):
            with self.assertRaises(json.JSONDecodeError):
                dbref.database_preview(b'SQLite format 3\x00fixture')


if __name__ == '__main__':
    unittest.main()
