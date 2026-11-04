"""Offline integration checks. Uses temporary Chrome profiles; never sends to Dola."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from browser_runtime import browser_runtime
from dola_browser import BrowserSteps, find_3big_browser, launch_context
from seedance_browser import migrate_settings, proxy_account
from seedance_ui import prepare, prompt_text, upload_images


class Steps:
    def __init__(self, page):
        self.page = page

    def click(self, locator):
        locator.click()

    def wait_until(self, predicate, timeout, message):
        import time
        end = time.monotonic() + min(timeout, 3)
        while not predicate():
            if time.monotonic() > end:
                raise TimeoutError(message)
            self.page.wait_for_timeout(20)


HTML = '''<!doctype html><meta charset="utf-8">
<div data-testid="chat_input">
 <button data-valid-btn="mode-select-action-btn" id="mode">Fast</button>
 <button id="create">Create Video</button>
 <div id="fields" hidden>
  <button data-input-engine-actionbar-control-key="video-model">Seedance 2.5</button>
  <button data-input-engine-actionbar-control-key="video-ratio">16:9</button>
  <button data-input-engine-actionbar-control-key="video-duration">5s</button>
 </div>
 <textarea></textarea><input type="file" accept="image/*">
 <div data-testid="attachment_area"></div>
 <button data-testid="chat_input_send_button">Send</button>
</div><div id="menus"></div>
<script>
const state = document.documentElement.dataset;
state.sent='[]';state.uploads='[]';
const mode=document.querySelector('#mode'), menus=document.querySelector('#menus');
function choose(values,target){
 menus.innerHTML='<div role="menu"></div>';
 for(const value of values){
  const button=document.createElement('button');
  button.setAttribute('role','menuitem');button.textContent=value;
  button.onclick=()=>{target.textContent=value;menus.innerHTML='';};
  menus.firstElementChild.append(button);
 }
}
mode.onclick=()=>choose(['Fast','Pro'],mode);
document.querySelector('#create').onclick=()=>document.querySelector('#fields').hidden=false;
for(const [key,values] of Object.entries({'video-model':['Seedance 2.5','Seedance 2.0'],
 'video-ratio':['16:9','9:16'],'video-duration':['5s','10s','15s']})){
 const button=document.querySelector(`[data-input-engine-actionbar-control-key="${key}"]`);
 button.onclick=()=>choose(values,button);
}
document.querySelector('input').onchange=(event)=>{
 const input=event.target, names=JSON.parse(state.uploads);
 names.push(input.files[0].name);state.uploads=JSON.stringify(names);
 const remove=document.createElement('button');
 remove.setAttribute('data-testid','attachment-delete-btn');
 remove.textContent=input.files[0].name;remove.onclick=()=>remove.remove();
 document.querySelector('[data-testid="attachment_area"]').append(remove);input.value='';
};
document.querySelector('[data-testid="chat_input_send_button"]').onclick=()=>{
 const sent=JSON.parse(state.sent);sent.push(document.querySelector('textarea').value);
 state.sent=JSON.stringify(sent);
};
</script>'''


class SeedanceTests(unittest.TestCase):
    def test_qt_settings_save_and_old_workspace_load(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication, QMainWindow
        import studio_qt
        from studio_core import defaults
        self.__class__._qt_app = QApplication.instance() or QApplication([])
        parent = QMainWindow()
        parent.settings = defaults()
        errors = []
        parent.error = lambda *args: errors.append(args)
        parent.save = lambda: None
        parent.log = lambda *args: None
        parent.apply_proxy_rotation = lambda: None
        parent.check_license = lambda: None
        parent.refresh_gallery = lambda: None
        dialog = studio_qt.SettingsDialog(parent)
        dialog.seedance_flow.setCurrentText('Cơ bản · Form tạo video')
        dialog.save()
        self.assertFalse(errors)
        self.assertFalse(parent.settings['use_gpm'])
        self.assertEqual(parent.settings['seedance_flow'], 'video_form')
        dialog.close()
        parent.close()
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            stored = dict(version=2, settings=dict(use_gpm=True, gpm_api='9495', load_extension=False),
                          accounts=[dict(id='old', profile='old/gpm_profile_locks/old', gpm_profile_id='gpm-old')],
                          tasks=[dict(id='task', status='Chờ xử lý')], hidden=[])
            (data/'workspace.json').write_text(json.dumps(stored), encoding='utf-8')
            obj = SimpleNamespace(settings=defaults(), load_error='')
            with patch.object(studio_qt, 'DATA', data):
                studio_qt.Studio.load(obj)
            self.assertFalse(obj.load_error)
            self.assertFalse(obj.settings['use_gpm'])
            self.assertTrue(obj.settings['load_extension'])
            self.assertNotIn('gpm_profile_id', obj.accounts[0])
            self.assertEqual(obj.accounts[0]['legacy_gpm']['id'], 'gpm-old')
    def test_settings_and_gpm_profile_migration(self):
        settings = dict(use_gpm=True, gpm_api='9495', gpm_hidden=True)
        accounts = [dict(id='../old', profile='old/gpm_profile_locks/id', gpm_profile_id='old-id',
                         cookie_file='saved-cookie.json')]
        with tempfile.TemporaryDirectory() as folder:
            migrate_settings(settings, accounts, folder)
            self.assertFalse(settings['use_gpm'])
            self.assertFalse(settings['show_browser'])
            self.assertTrue(settings['use_3big_browser'])
            self.assertTrue(Path(accounts[0]['profile']).is_relative_to(Path(folder)/'chrome_profiles'))
            self.assertFalse(Path(accounts[0]['profile']).exists())
            self.assertNotIn('gpm_profile_id', accounts[0])
            self.assertEqual(accounts[0]['legacy_gpm']['id'], 'old-id')
            self.assertEqual(accounts[0]['cookie_file'], 'saved-cookie.json')
            previous = dict(accounts[0])
            migrate_settings(settings, accounts, folder)
            self.assertEqual(accounts[0], previous)

    def test_proxy_resolver_and_lease_release(self):
        account = dict(id='test', proxy_mode='default')
        expected = dict(account, proxy={'server':'http://proxy.example:8080'})
        with patch('proxyvn.gpm_proxy_account', return_value=expected) as resolver, \
             patch('proxy_pool.pool') as pool:
            actual, lease = proxy_account({'proxy_source':'pvn'}, account, None, None, unique=True)
            self.assertEqual(actual, expected)
            self.assertTrue(resolver.call_args.kwargs['unique'])
            lease.close(); lease.close()
            pool.return_value.release.assert_called_once_with('test')

    def test_prompts(self):
        task = {'prompt':'A cat, camera pauses for 5 seconds.'}
        config = dict(seedance_flow='video_form',duration='30')
        self.assertEqual(prompt_text(task,config), task['prompt'])
        self.assertEqual(prompt_text(task,dict(config, dola_mode='skill30')),task['prompt'])
        config.update(seedance_flow='pro_chat',duration='15')
        self.assertIn('human artifact',prompt_text(task,config))
        config['duration']='30'
        with self.assertRaises(ValueError):
            prompt_text(task,config)

    def test_browser_form_images_and_one_submit(self):
        settings=dict(use_3big_browser=True,use_gpm=False)
        with browser_runtime(settings) as runtime:
            browser=runtime.chromium.launch(executable_path=find_3big_browser(),headless=True)
            try:
                page=browser.new_page();page.set_content(HTML)
                cfg=dict(seedance_flow='video_form',dola_mode='pro',model='Seedance 2.0',ratio='9:16',duration='10')
                prepare(page,cfg,Steps(page),lambda _:None)
                self.assertEqual(page.locator('[data-input-engine-actionbar-control-key="video-duration"]').inner_text(),'10s')
                self.assertEqual(page.locator('[data-input-engine-actionbar-control-key="video-model"]').inner_text(),'Seedance 2.0')
                self.assertEqual(page.locator('[data-input-engine-actionbar-control-key="video-ratio"]').inner_text(),'9:16')
                with self.assertRaises(TimeoutError):
                    prepare(page,dict(cfg,duration='30'),Steps(page),lambda _:None)
                self.assertEqual(json.loads(page.locator('html').get_attribute('data-sent')),[])
                page.evaluate("document.querySelector('#menus').innerHTML=''")
                with tempfile.TemporaryDirectory() as folder:
                    images=[Path(folder)/'first.png',Path(folder)/'second.png']
                    for image in images:image.write_bytes(b'fixture')
                    upload_images(page,[str(p) for p in images],Steps(page),lambda _:None)
                self.assertEqual(json.loads(page.locator('html').get_attribute('data-uploads')),['first.png','second.png'])
                steps=BrowserSteps(page,{'action_delay':.2},threading.Event(),lambda _:None)
                text='A cat\nwith a red scarf'
                steps.type_prompt(page.locator('textarea'),text)
                steps.submit(page.locator('textarea'))
                self.assertEqual(json.loads(page.locator('html').get_attribute('data-sent')),[text])
            finally:
                browser.close()

    def test_three_chats_parallel_and_disconnect(self):
        import seedance_parallel as parallel
        barrier=threading.Barrier(3,timeout=20)
        settled=[]
        def local_chat(endpoint,task,account,settings,cancel,report,name,*args):
            with browser_runtime(settings) as runtime:
                browser=runtime.chromium.connect_over_cdp(endpoint)
                page=browser.contexts[0].new_page()
                try:
                    page.goto('data:text/html,<title>'+task['id']+'</title>')
                    barrier.wait()
                    self.assertEqual(page.title(),task['id'])
                    page.wait_for_timeout(100)
                    if task['id']=='0':raise RuntimeError('single chat failed')
                    return task['id']
                finally:page.close()
        def local_launch(runtime,settings,account,**kwargs):
            return launch_context(runtime,settings,account,visible=False,**kwargs)
        with tempfile.TemporaryDirectory() as folder:
            account=dict(id='local',name='local',profile=str(Path(folder)/'profile'),proxy_mode='off')
            cfg=dict(use_3big_browser=True,use_gpm=False,load_extension=False,tabs_per_profile=3,lock_root=str(Path(folder)/'locks'))
            tasks=[dict(id=str(i),name=str(i)) for i in range(3)]
            with patch.object(parallel,'run_chat',local_chat),patch.object(parallel,'launch_context',local_launch), \
                 patch.object(parallel.BrowserSteps,'navigate',lambda self,_:self.page.goto('data:text/html,local')), \
                 patch.object(parallel,'ensure_chrome_session',return_value='Local'):
                parallel.run_profile(tasks,account,cfg,threading.Event(),lambda *args:lambda _:None,
                                     lambda task,value,error:settled.append((task['id'],value,error)))
        self.assertEqual(len(settled),3)
        self.assertEqual(sum(error is None for _,_,error in settled),2,settled)

    def test_cancellation_before_worker_join(self):
        from seedance_parallel import workers
        stop=threading.Event()
        with self.assertRaises(RuntimeError):
            with workers(1,stop) as pool:
                future=pool.submit(stop.wait,2)
                raise RuntimeError('owner stopped')
        self.assertTrue(future.result())


if __name__=='__main__':
    unittest.main()
