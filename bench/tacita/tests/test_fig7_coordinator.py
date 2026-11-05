"""Exercise real subprocess gates with synthetic protocol workers, not benchmark data."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'scripts' if (ROOT/'scripts/run.py').is_file() else ROOT.parents[1]/'scripts/tacita'
sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location('round_coordinator',SCRIPTS/'run.py')
coordinator=importlib.util.module_from_spec(spec); spec.loader.exec_module(coordinator)

WORKER=r'''
import os,sys,json,time
from pathlib import Path
phase=sys.argv[1]; args=sys.argv[2:]
def flag(k):return args[args.index(k)+1]
if phase=='bridge':
 n,a,L,m,t=map(int,args[:5]);keyfile=Path(args[6])
 Path(os.environ['BENCH_KAHE_SAMPLE']).write_text(' '.join(['1']*8192))
 time.sleep(.15)
 print('setup_ready phase=kahe',flush=True)
 assert input()=='START'
 time.sleep(.06)
 keyfile.write_text((' '.join(['1']*8192)+'\n')*n)
 Path(os.environ['BENCH_KAHE_AUDIT']).write_text(json.dumps(dict(sent_bytes_each=[10]*n,client_operation_times_s=[.01]*n,client_sample_times_s=[.01])))
 print(f'ready setup_s=.15 client_mean_s=.01 client_observed_mean_s=.01 client_serial_wall_s=.06 server_pre_s=.01 client_bytes={n*10} active_client_bytes={a*10} dropout_client_bytes={(n-a)*10} profile=fixed-upstream-100k log_n=13 log_t=168',flush=True)
 input()
 for i in range(a):input()
 time.sleep(.06)
 print('result_ready server_finalize_s=.06',flush=True)
 time.sleep(.2) # post-result assertion/log overhead must not enter E2E
 print('done server_finalize_s=.06 correctness=pass',flush=True)
else:
 n=int(flag('--n-clients'));a=int(flag('--active-clients'));m=int(flag('--committee-size'));t=int(flag('--threshold'))
 sample=json.loads(Path(os.environ['BENCH_STE_SAMPLE']).read_text());assert sample['values'][0][0]==1
 time.sleep(.15)
 print('setup_ready phase=ste',flush=True)
 assert input()=='START'
 fixture=json.loads(Path(os.environ['BENCH_STE_FIXTURE']).read_text());assert len(fixture['values'])==n
 if os.environ.get('TEST_STE_FAIL'):sys.exit(4)
 time.sleep(.06)
 metrics=dict(measurement_revision='round-v2',correctness='pass',decoded_sum=[a]*8192,recovered_mask_seeds=[[0]*32]*a,
 online_wall_s=.06,setup_s=.15,client_compute_s=.02,client_observed_mean_s=.02,client_batch_wall_s=.06,
 server_compute_s=.01,client_to_server_bytes=n*20,active_client_to_server_bytes=a*20,dropout_client_to_server_bytes=(n-a)*20,
 total_bytes=n*20+m*3+t*2,client_sent_bytes_each=[20]*n,client_operation_times_s=[.02]*n,
 committee_request_bytes_each=[3]*m,committee_reply_bytes_each=[0]*(m-t)+[2]*t,client_sample_times_s=[.02])
 Path(flag('--output')).write_text(json.dumps(metrics))
'''

class CoordinatorTests(unittest.TestCase):
    def exercise(self,fail=False):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp); worker=tmp/'worker.py';worker.write_text(WORKER)
            args=SimpleNamespace(n_clients=4,active_clients=3,vector_len=32,committee_size=3,threshold=2,
                                 threads=2,kahe_bridge='mock-bridge',keep_fixtures=False)
            popen=subprocess.Popen; children=[]
            def spawn(command,**kw):
                phase='bridge' if command[0]=='mock-bridge' else 'ste'
                child=popen([sys.executable,str(worker),phase,*command[1:]],**kw)
                children.append(child);return child
            def seed(command,log):
                Path(command[-1]).write_text(json.dumps(dict(seeds=[[0]*32]*4,mask_seeds=[[0]*32]*4)))
            with patch.object(coordinator,'run_logged',side_effect=seed), \
                 patch.object(coordinator.subprocess,'Popen',side_effect=spawn), \
                 patch.dict(os.environ,dict(TEST_STE_FAIL='1') if fail else {},clear=False):
                if fail:
                    with self.assertRaises(RuntimeError):coordinator.fig7_one(args,Path('mock-ste'),tmp/'out')
                    self.assertEqual(json.loads((tmp/'out/status.json').read_text())['status'],'failed')
                else:
                    result=coordinator.fig7_one(args,Path('mock-ste'),tmp/'out')
                    self.assertGreaterEqual(result['end_to_end_s'],.18)
                    # Setups alone are .3 seconds; stop timer before final .2-second check.
                    self.assertGreater(result['driver_wall_s']-result['end_to_end_s'],.4)
                    self.assertEqual(result['client_to_server_bytes'],120)
                    self.assertEqual(result['active_client_to_server_bytes'],90)
                    self.assertEqual(result['dropout_client_to_server_bytes'],30)
                    audit=json.loads((tmp/'out/round_audit.json').read_text())
                    self.assertEqual(sum(x['sent_bytes'] for x in audit['clients']),120)
                    self.assertFalse((tmp/'out/sample-key.txt').exists())
                self.assertTrue(all(p.poll() is not None for p in children))
    def test_setup_and_postcheck_excluded_from_continuous_round(self):self.exercise()
    def test_failed_ste_kills_waiting_kahe_and_does_not_pass(self):self.exercise(True)

if __name__=='__main__':unittest.main()
