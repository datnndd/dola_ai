from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading
import unittest
from unittest.mock import patch

from browser_runtime import browser_runtime
from dola_browser import launch_context
from seedance_parallel import browser_endpoint, page_target, run_chat


class ReuseTests(unittest.TestCase):
    def test_worker_reuses_ready_page_with_extension_and_keeps_it_open(self):
        with tempfile.TemporaryDirectory() as folder:
            settings=dict(use_gpm=False,use_3big_browser=True,load_extension=True,_parallel_tabs=True)
            account=dict(id='offline',profile=folder,proxy_mode='off')
            with browser_runtime(settings) as runtime:
                context=launch_context(runtime,settings,account,visible=False)
                try:
                    page=context.pages[0]
                    context.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body='<textarea>ready</textarea>'))
                    page.goto('https://www.dola.com/chat')
                    target=page_target(context,page)
                    def job(task,config,account,cancel,report,*args,**kwargs):
                        self.assertTrue(config['_reuse_ready_page'])
                        self.assertEqual(page_target(kwargs['shared_context'],kwargs['task_page']),target)
                        self.assertEqual(kwargs['task_page'].url,'https://www.dola.com/chat')
                        if False:yield
                        return 'ready'
                    with patch('seedance_parallel.render_dola_steps',job),ThreadPoolExecutor(1) as pool:
                        future=pool.submit(run_chat,browser_endpoint(account),dict(id='task'),account,settings,
                                           threading.Event(),lambda _:None,'test',None,None,None,target)
                        while not future.done():page.wait_for_timeout(50)
                        self.assertEqual(future.result(),'ready')
                    self.assertFalse(page.is_closed())
                    self.assertEqual(len(context.pages),1)
                    self.assertEqual(page.url,'https://www.dola.com/chat')
                finally:context.close()


if __name__=='__main__':unittest.main()
