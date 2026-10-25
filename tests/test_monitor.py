import argparse
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer
from grocery import __main__ as app
from grocery.monitor import EventBus, make_handler, safe, worker


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.bus = EventBus(self.folder, 'replay', 'scripted', 'home')

    def tearDown(self):
        self.tmp.cleanup()

    def options(self, **changes):
        options = dict(start_delay=0, image=None, scenario='restock', inbound_milk=False,
                       mode='replay', voice='scripted', speak=False, unseen_policy='unknown',
                       presence='home', silence_policy='hold')
        options.update(changes)
        return argparse.Namespace(**options)

    def test_actual_offline_workflow_generates_paired_tool_events_and_order(self):
        original = (app.Agent, app.trace, app.ROOT)
        worker(self.bus, self.options())
        state = self.bus.snapshot()
        self.assertEqual(state['status'], 'completed', state.get('error'))
        self.assertTrue(state['order']['simulated'])
        self.assertEqual(state['order']['total_cents'], 1346)
        self.assertEqual(state['cart']['version'], 2)
        names = [s['name'] for s in state['steps']]
        for expected in ('observe_fridge','update_inventory','get_household_context','build_cart','edit_cart','listen','checkout'):
            self.assertIn(expected, names)
        events = [json.loads(line) for line in (self.folder/'events.jsonl').read_text().splitlines()]
        started = {e['id'] for e in events if e['type']=='step_started'}
        completed = {e['id'] for e in events if e['type']=='step_completed'}
        self.assertEqual(started, completed)
        self.assertEqual((app.Agent,app.trace,app.ROOT), original)
        self.assertTrue((self.folder/'preferences.md').is_file())
        # Newer application versions export purchase lists/signals; keep them isolated.
        exported = list(self.folder.rglob('*purchase*')) + list(self.folder.rglob('*order_signal*'))
        if 'save_purchase_list' in names:
            self.assertTrue(exported)

    def test_inbound_delivery_is_not_purchased_twice(self):
        worker(self.bus, self.options(inbound_milk=True, presence='away'))
        state=self.bus.snapshot()
        self.assertEqual(state['status'],'completed',state.get('error'))
        self.assertEqual([line['sku'] for line in state['order']['lines']],['eggs_6'])
        self.assertNotIn('listen',[step['name'] for step in state['steps']])

    def test_full_fridge_finishes_without_cart_or_order(self):
        worker(self.bus,self.options(scenario='full'))
        state=self.bus.snapshot()
        self.assertEqual(state['status'],'no_restock',state.get('error'))
        self.assertIsNone(state['order'])
        self.assertIsNone(state['cart'])

    def test_failure_event_is_real_and_reraised(self):
        def fail():
            raise ValueError('test tool failed')
        with self.assertRaises(ValueError): self.bus.call('build_cart',fail)
        self.assertEqual(self.bus.snapshot()['steps'][0]['status'],'failed')
        self.assertEqual(self.bus.snapshot()['steps'][0]['result'],{'error':'test tool failed'})

    def test_credentials_and_reasoning_are_removed(self):
        self.assertEqual(safe({'api_key':'secret','authorization':'secret','reasoning_content':'private','result':{'key':'secret','stock':2}}),{'result':{'stock':2}})

    def test_snapshot_does_not_allow_external_mutation(self):
        state=self.bus.snapshot();state['steps'].append({'bad':True})
        self.assertEqual(self.bus.snapshot()['steps'],[])

    def test_http_api_is_read_only_and_serves_no_arbitrary_files(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.bus))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            with urlopen(base+'/api/state') as response:
                self.assertEqual(json.load(response)['status'],'waiting')
                self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
            with urlopen(base+'/') as response:
                self.assertIn(b'Agent activity',response.read())
            for method in ('POST','PUT','PATCH','DELETE'):
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base+'/api/state',data=b'{}',method=method))
                self.assertEqual(error.exception.code,405)
            for path in ('/.env','/../.env','/grocery/core.py'):
                with self.assertRaises(HTTPError) as error: urlopen(base+path)
                self.assertEqual(error.exception.code,404)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)


if __name__=='__main__': unittest.main()
