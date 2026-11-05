"""Round-v2 accounting regression tests. All fixtures below are synthetic."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import suite
from test_suite import fixture, sender_audit


class RoundContractTests(unittest.TestCase):
    def setUp(self):
        self.config = suite.read_json(Path(__file__).resolve().parents[1]/'config.json')

    def test_all_participants_are_sent_once_and_only_survivors_delivered(self):
        for protocol in ('osa', 'opa', 'willow'):
            job = suite.Job(protocol, 1000, 800, 50, 10000, 1)
            raw = fixture(protocol, job)
            audit = sender_audit(raw, job)
            suite.validate_round_audit(audit, raw, job)
            row = suite.normalize(raw, job, [], 32, 100.0)
            self.assertEqual(row['client_sent_bytes'], 1000)
            self.assertEqual(row['active_client_sent_bytes'], 800)
            self.assertEqual(row['dropout_client_sent_bytes'], 200)
            self.assertEqual(row['client_send_bytes_mean'], 1)
            self.assertEqual(row['end_to_end_s'], 2.5)
            self.assertNotEqual(row['end_to_end_s'], row['driver_wall_s'])
            self.assertEqual(len(audit['committee_request_bytes']), 50)
            self.assertEqual(sum(b>0 for b in audit['committee_reply_bytes']), job.t)

    def test_omitting_any_sender_or_forward_or_adding_receiver_fails(self):
        job=suite.Job('osa',1000,800,50,10000,1)
        raw=fixture('osa',job); audit=sender_audit(raw,job)
        variants=[]
        bad=copy.deepcopy(audit); bad['clients'].pop(); variants.append(bad)
        bad=copy.deepcopy(audit); bad['committee_request_bytes']=bad['committee_request_bytes'][:40]; variants.append(bad)
        bad=copy.deepcopy(audit); bad['clients'][999]['delivered']=True; variants.append(bad)
        bad=copy.deepcopy(audit); bad['committee_reply_bytes'][-1]=1; variants.append(bad)
        bad=copy.deepcopy(audit); bad['end_to_end_s']=100; variants.append(bad)
        for bad in variants:
            with self.assertRaises(ValueError): suite.validate_round_audit(bad,raw,job)
        raw['total_bytes']+=raw['client_to_server_bytes']
        with self.assertRaises(ValueError): suite.normalize(raw,job,[],32,100)

    def test_invalid_old_binary_cannot_enter_new_results(self):
        job=suite.Job('osa',1000,800,50,10000,1)
        raw=fixture('osa',job); del raw['measurement_revision']
        with self.assertRaises(ValueError): suite.normalize(raw,job,[],32,100)

    def test_complete_grid_and_parameter_constraints(self):
        jobs=suite.plan(self.config,'aggregation')
        self.assertEqual(len(jobs),264)
        self.assertEqual(len(suite.plan(self.config,'dropout')),48)
        for job in jobs:
            if job.protocol=='osa':
                self.assertEqual(job.rss,16)
                self.assertTrue(job.rss < job.t <= job.m)
                self.assertGreater(job.t, job.m-job.rss)
            if job.protocol=='opa':
                self.assertGreaterEqual(job.m,24)
                self.assertGreater(job.t,16)
        self.assertEqual(suite.Job('opa',1000,800,50,10000,1).t,34)
        self.assertEqual(suite.Job('osa',1000,800,50,10000,1).t,40)

    def test_no_dropout_and_40_percent_dropout_contract(self):
        for active in (1000,600):
            job=suite.Job('osa',1000,active,50,10000,1)
            raw=fixture('osa',job); audit=sender_audit(raw,job)
            suite.validate_round_audit(audit,raw,job)
            row=suite.normalize(raw,job,[],32,100)
            self.assertEqual(row['dropout_client_sent_bytes'],1000-active)

    def test_twenty_samples_outside_round_with_fixed_32_threads(self):
        job=suite.Job('osa',1000,800,50,10000,1)
        _,env,_=suite.commands(job,self.config,Path('attempt'))
        self.assertEqual(env['BENCH_THREADS'],'32')
        self.assertEqual(env['BENCH_CLIENT_WARMUP'],'3')
        self.assertEqual(env['BENCH_CLIENT_SAMPLES'],'20')

if __name__=='__main__': unittest.main()
