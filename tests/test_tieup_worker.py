"""Bounded transport worker checks with controlled fake network clients."""
import threading
import time
import unittest
from unittest.mock import Mock
from app.services.tieup_worker import TieupWorker, TieupJob
from app.services.utanet_service import LookupResult


class WorkerTests(unittest.TestCase):
    def setUp(self): self.workers=[]
    def tearDown(self):
        for worker in self.workers:
            worker.shutdown()
            if worker._thread: worker._thread.join(1)
    def worker(self,client,max_pending=8):
        worker=TieupWorker(client,max_pending=max_pending); self.workers.append(worker); return worker
    def wait_results(self,worker,count=1):
        results=[];deadline=time.monotonic()+2
        while len(results)<count and time.monotonic()<deadline:
            results+=worker.drain()
            if len(results)<count: time.sleep(.005)
        self.assertGreaterEqual(len(results),count)
        return results
    def test_lazy_no_thread_before_submit(self):
        w=self.worker(Mock())
        self.assertIsNone(w._thread)
    def test_result_correlated_by_job_token_not_sequence(self):
        client=Mock();client.lookup.side_effect=lambda t,a,**k:LookupResult(t,a,comment='Info',status='found')
        w=self.worker(client);job=TieupJob('Song','Artist');w.submit(job)
        token,result=self.wait_results(w)[0]
        self.assertEqual(token,job.token);self.assertEqual(result.comment,'Info')
    def test_jobs_use_one_non_gui_thread(self):
        threads=[]
        def lookup(t,a,**kw):
            threads.append(threading.get_ident());return LookupResult(t,a,status='not_found')
        w=self.worker(Mock(lookup=lookup))
        for i in range(4):w.submit(TieupJob(str(i),'A'))
        self.wait_results(w,4)
        self.assertEqual(len(set(threads)),1)
        self.assertNotEqual(threads[0],threading.get_ident())
    def test_queue_evicts_oldest_without_blocking(self):
        started=threading.Event()
        def lookup(t,a,cancel):
            started.set();cancel.wait(2);return LookupResult(t,a,status='cancelled')
        w=self.worker(Mock(lookup=lookup),max_pending=2)
        w.submit(TieupJob('Active','A'));self.assertTrue(started.wait(1))
        jobs=[TieupJob(str(i),'A') for i in range(3)]
        for j in jobs:w.submit(j)
        self.assertEqual(len(w._queue),2)
        self.assertTrue(jobs[0].cancel.is_set())
        token,result=self.wait_results(w)[0]
        self.assertEqual(token,jobs[0].token);self.assertEqual(result.status,'cancelled')
    def test_cancel_active_and_pending(self):
        started=threading.Event()
        def lookup(t,a,cancel):
            started.set();cancel.wait(2);return LookupResult(t,a,status='cancelled')
        w=self.worker(Mock(lookup=lookup));a=TieupJob('A','A');b=TieupJob('B','B')
        w.submit(a);self.assertTrue(started.wait(1));w.submit(b);w.cancel_all()
        self.assertTrue(a.cancel.is_set());self.assertTrue(b.cancel.is_set());self.assertFalse(w._queue)
    def test_exception_becomes_error_result(self):
        w=self.worker(Mock(lookup=Mock(side_effect=ValueError('bad'))))
        w.submit(TieupJob('A','A'))
        self.assertEqual(self.wait_results(w)[0][1].status,'error')
    def test_shutdown_rejects_new_jobs(self):
        w=self.worker(Mock());w.shutdown()
        self.assertFalse(w.submit(TieupJob('A','A')))
    def test_results_queue_is_bounded(self):
        w=self.worker(Mock())
        for i in range(100):w._put(str(i),LookupResult(str(i),'A'))
        results=w.drain()
        self.assertEqual(len(results),32);self.assertEqual(results[-1][0],'99')
    def test_zero_pending_normalized(self):
        self.assertEqual(self.worker(Mock(),max_pending=0).max_pending,1)

if __name__=='__main__':unittest.main()
