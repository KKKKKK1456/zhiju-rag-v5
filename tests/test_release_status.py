import unittest
from app.preview_server import Jobs


class Engine:
    def __init__(self, *args): pass
    def close(self): pass


class ReleaseStatusTests(unittest.TestCase):
    def test_release_and_elapsed_are_safe_and_detached(self):
        jobs = Jobs(False, Engine)
        jobs.thread.join()
        try:
            import time
            now = time.monotonic()
            jobs.current = dict(id='test', state='running', stage='retrieval',
                                started=now-10, updated=now-1)
            snapshot = jobs.snapshot()
            self.assertEqual(snapshot['release']['id'], 'v5-experience-20260924.1')
            self.assertFalse(snapshot['release']['accuracy_certified'])
            self.assertGreaterEqual(snapshot['job']['elapsed_seconds'], 10)
            self.assertNotIn('elapsed_seconds', jobs.current)
        finally:
            jobs.close()
