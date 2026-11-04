import base64
import asyncio
import json
from pathlib import Path
import socketserver
import struct
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from engine import Cancelled
from seedance_video import (Candidate, ConversationReader, download, follow_conversation,
                            model_candidates, mp4_duration, parse_conversation, _run_io)
from task_runtime import VideoDownloadError


def message(user, timestamp, content=None, **ext):
    return dict(user_type=user, create_time=timestamp, ext=ext, content=json.dumps(content or []))


def payload(messages):
    return {'status_code':0, 'downlink_body':{'batch_pull_singe_chain_downlink_body':{
        'message_map':{'123':{'messages':messages}}}}}


def video_block(url, model=None):
    return {'block_type':2074,'content':{'creation_block':{'creations':[
        {'type':2,'video':{'download_url':url,'video_model':json.dumps(model or {})}}]}}}


def mp4():
    def box(kind, body):return struct.pack('>I4s',len(body)+8,kind)+body
    movie = b'\0'*12 + struct.pack('>II',1000,15000) + b'\0'*80
    return box(b'ftyp',b'isom\0\0\0\0isom')+box(b'moov',box(b'mvhd',movie))


class ProxyHandler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            line=self.rfile.readline();self.server.requests.append(line)
            while self.rfile.readline() not in (b'\r\n',b''):pass
            body=b'not a video' if b'bad' in line else mp4()
            if b'partial' in line:
                self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Length: 10000\r\nConnection: close\r\n\r\n'+body)
                return
            self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n')
            self.wfile.flush()
            if b'slow' in line:
                self.server.cancel.set();time.sleep(1)
            self.wfile.write(body)
        except (OSError,ConnectionError):pass


class VideoTests(unittest.TestCase):
    def test_failed_download_after_submit_is_not_automatically_regenerated(self):
        from auto_jobs import AutoJobs
        self.assertFalse(AutoJobs.hold_failure(object(), None,
                         {'_dola_submission_attempted':True}, VideoDownloadError('CDN temporarily unavailable')))

    def test_io_works_inside_patchright_running_event_loop(self):
        async def work():return 42
        async def caller():return _run_io(work)
        self.assertEqual(asyncio.run(caller()),42)
    def test_agent_file_artifact_is_video_and_commands_are_not_sources(self):
        blocks=[{'block_type':10019,'content':{'file_operation_block':{'content':'curl https://untrusted.example/ignore.mp4'}}},
                {'block_type':10020,'content':{'file_block':{'type':'mp4','name':'cat.mp4','url':'https://cdn.example/cat.mp4'}}},
                {'block_type':10020,'content':{'file_block':{'type':'png','name':'cat.png','url':'https://cdn.example/cat.png'}}}]
        state=parse_conversation(payload([message(1,1),message(2,2,blocks,is_finish='1')]),'123')
        self.assertEqual(state['candidates'],[Candidate('https://cdn.example/cat.mp4','dola_download_url')])
        self.assertTrue(state['finished'])

    def test_ignore_users_and_older_turns_and_rank_bitrate(self):
        model={'video_list':{'low':{'main_url':base64.b64encode(b'https://cdn.example/low.mp4').decode(),'bitrate':100},
                             'high':{'main_url':base64.b64encode(b'https://cdn.example/high.mp4').decode(),'bitrate':500}}}
        messages=[message(2,1,[video_block('https://cdn.example/old.mp4')]),message(1,2,has_video_gen='1'),
                  message(2,3,[video_block('https://cdn.example/new.mp4',model)],has_video_gen='1',is_finish='1')]
        state=parse_conversation(payload(messages),'123')
        self.assertTrue(state['accepted'])
        self.assertEqual([c.url for c in state['candidates']],['https://cdn.example/high.mp4','https://cdn.example/low.mp4','https://cdn.example/new.mp4'])
        self.assertFalse(parse_conversation(payload([message(1,1,has_video_gen='1')]),'123')['accepted'])
        with self.assertRaises(RuntimeError):parse_conversation(payload([]),'wrong')

    def test_batch_to_single_read_only_fallback(self):
        calls=[]
        def evaluate(script, args):
            calls.append(args['endpoint'])
            self.assertIn('cmd:batch?3101:3100',script)
            if args['endpoint']=='batch':return dict(http_status=200,payload=payload([])['downlink_body'])
            return dict(http_status=200,payload={'status_code':0,'downlink_body':{'pull_singe_chain_downlink_body':{'messages':[]}}})
        page=SimpleNamespace(evaluate=evaluate,context=SimpleNamespace(cookies=lambda:[]),is_closed=lambda:False)
        reader=ConversationReader(page,'123');reader.read();reader.read()
        self.assertEqual(calls,['batch','single','single'])

    def test_download_proxy_part_validation_cancellation(self):
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads=True;allow_reuse_address=True
        server=Server(('127.0.0.1',0),ProxyHandler);server.requests=[];server.cancel=threading.Event()
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        proxy={'server':f'http://127.0.0.1:{server.server_address[1]}'}
        try:
            with tempfile.TemporaryDirectory() as folder:
                target=Path(folder)/'video.mp4'
                duration=download('http://media.example.test/clip.mp4',target,proxy,'https://www.dola.com/chat/123',threading.Event(),{})
                self.assertEqual(duration,15)
                self.assertEqual(mp4_duration(target),15)
                self.assertFalse(target.with_name('video.mp4.part').exists())
                self.assertIn(b'http://media.example.test/clip.mp4',server.requests[0])
                for name in ('bad','partial'):
                    path=Path(folder)/(name+'.mp4')
                    with self.assertRaises(Exception):download('http://media.example.test/'+name,path,proxy,'https://www.dola.com/chat/123',threading.Event(),{})
                    self.assertFalse(path.exists());self.assertFalse(path.with_name(path.name+'.part').exists())
                with self.assertRaises(Cancelled):
                    download('http://media.example.test/slow',Path(folder)/'slow.mp4',proxy,'https://www.dola.com/chat/123',server.cancel,{})
                self.assertFalse((Path(folder)/'slow.mp4.part').exists())
        finally:server.shutdown();server.server_close();thread.join(2)

    def test_follow_accepts_api_result_and_falls_back_without_submit_or_reload(self):
        accepted=dict(accepted=True,clarifying=False,finished=False,loading=True,failures=[],text='',candidates=[],fallback_apis=[])
        completed=dict(accepted,finished=True,loading=False,candidates=[Candidate('https://cdn.example/model.mp4','model_main_url'),Candidate('https://cdn.example/final.mp4','dola_download_url')])
        calls=[];reports=[]
        def save(url,target,proxy,*args):
            calls.append((url,proxy))
            if url.endswith('model.mp4'):raise OSError('expired')
            Path(target).write_bytes(mp4());return 15
        steps=SimpleNamespace(wait=lambda _:None)
        page=SimpleNamespace(is_closed=lambda:False)
        config={'_browser_proxy':{'server':'http://127.0.0.1:9999'},'video_source':'api_preview'}
        with tempfile.TemporaryDirectory() as folder, patch('seedance_video.ConversationReader') as reader, \
             patch('seedance_video.resolve_fallbacks',return_value=[]),patch('seedance_video.download',side_effect=save):
            reader.return_value.read.side_effect=[accepted,completed]
            job=follow_conversation(page,object(),'https://www.dola.com/chat/123',Path(folder)/'out.mp4',config,threading.Event(),reports.append,steps,resume=True)
            try:
                while True:next(job)
            except StopIteration as done:self.assertEqual(done.value['evidence'],'dola_download_url')
            self.assertEqual(len(calls),2)
            self.assertTrue(all(proxy==config['_browser_proxy'] for _,proxy in calls))
            self.assertTrue(any('đã nhận tạo video' in report for report in reports))

    def test_ui_share_gates_api_read_for_the_new_reply(self):
        completed=dict(accepted=True,clarifying=False,finished=True,loading=False,failures=[],text='',
                       candidates=[Candidate('https://cdn.example/final.mp4','dola_download_url')],
                       fallback_apis=[],user_turn=1)
        reports=[]
        baseline={'assistant':(4,'old reply'),'shares':4}
        with patch('seedance_video._ui_reply_finished',side_effect=[(False,''),(True,'new reply')]), \
             patch('seedance_video.ConversationReader') as reader, \
             patch('seedance_video.resolve_fallbacks',return_value=[]), \
             patch('seedance_video.download',return_value=15):
            reader.return_value.read.return_value=completed
            job=follow_conversation(SimpleNamespace(is_closed=lambda:False),object(),
                'https://www.dola.com/chat/123',Path('unused.mp4'),{},threading.Event(),
                reports.append,SimpleNamespace(wait=lambda _:None),ui_baseline=baseline)
            next(job)                 # enter polling loop
            next(job)                 # no new Share yet
            reader.return_value.read.assert_not_called()
            with self.assertRaises(StopIteration):
                next(job)             # new Share appears; API may now be read
            reader.return_value.read.assert_called_once()
            self.assertTrue(any('Đã thấy nút Share của lượt mới' in line for line in reports))

    def test_configured_deadline_replaces_fixed_polling_limits(self):
        pending = dict(accepted=False, clarifying=False, finished=False, loading=False,
                       failures=[], text='', candidates=[], fallback_apis=[], user_turn=1)
        # Pending generation, transient network failure, and waiting for the
        # second turn must all survive old 90/180/600s limits, but stop at 900s.
        text_only = dict(pending, finished=True, text='Dola đang xử lý yêu cầu')
        for kind in ('pending', 'network', 'second_turn', 'text_only'):
            now = [0.0]
            config = {'timeout': 900, '_task_deadline': 900}
            if kind == 'second_turn': config['_after_user_turn'] = 1
            with self.subTest(kind=kind), patch('seedance_video.time.monotonic', side_effect=lambda: now[0]), patch('seedance_video.ConversationReader') as reader:
                if kind == 'network': reader.return_value.read.side_effect = OSError('temporary')
                elif kind == 'text_only': reader.return_value.read.return_value = text_only
                else: reader.return_value.read.return_value = pending
                job = follow_conversation(SimpleNamespace(is_closed=lambda:False), object(),
                    'https://www.dola.com/chat/123', Path('unused.mp4'), config,
                    threading.Event(), lambda _:None, SimpleNamespace(wait=lambda _:None), resume=True)
                next(job)
                for elapsed in (181, 601, 899):
                    now[0] = elapsed
                    next(job)
                now[0] = 900
                with self.assertRaisesRegex(TimeoutError, 'Hết thời gian task'): next(job)

    def test_explicit_no_logo_never_silently_downloads_service_fallback(self):
        state=dict(accepted=True,clarifying=False,finished=True,loading=False,failures=[],text='',
                   candidates=[Candidate('https://cdn.example/unknown.mp4','dola_download_url')],fallback_apis=[])
        with patch('seedance_video.ConversationReader') as reader,patch('seedance_video.resolve_fallbacks',return_value=[]),patch('seedance_video.download') as save:
            reader.return_value.read.return_value=state
            job=follow_conversation(SimpleNamespace(is_closed=lambda:False),object(),'https://www.dola.com/chat/123',
                                    Path('unused.mp4'),{'video_source':'no_logo','fallback_to_dola_link':False},threading.Event(),lambda _:None,
                                    SimpleNamespace(wait=lambda _:None),resume=True)
            with self.assertRaises(VideoDownloadError):
                while True:next(job)
            save.assert_not_called()

    def test_default_no_logo_mode_falls_back_to_dola_link(self):
        state=dict(accepted=True,clarifying=False,finished=True,loading=False,failures=[],text='',
                   candidates=[Candidate('https://cdn.example/fallback.mp4','dola_download_url')],fallback_apis=[])
        reports=[]
        with patch('seedance_video.ConversationReader') as reader, \
             patch('seedance_video.resolve_fallbacks',return_value=[]), \
             patch('seedance_video.download',return_value=15) as save:
            reader.return_value.read.return_value=state
            job=follow_conversation(SimpleNamespace(is_closed=lambda:False),object(),
                'https://www.dola.com/chat/123',Path('unused.mp4'),{'video_source':'no_logo'},
                threading.Event(),reports.append,SimpleNamespace(wait=lambda _:None),resume=True)
            with self.assertRaises(StopIteration) as done:
                while True:next(job)
            self.assertEqual(done.exception.value['evidence'],'dola_download_url')
            save.assert_called_once()
            self.assertTrue(any('có thể có logo' in line for line in reports))


if __name__=='__main__':unittest.main()
