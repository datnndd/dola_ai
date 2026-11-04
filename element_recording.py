"""Record the decoded video element, not the surrounding page or overlays."""
import base64
import time
import uuid
from pathlib import Path
from engine import Cancelled, find_ffmpeg, run_process


def record_element(page, output, settings, cancel, report, provenance):
    ffmpeg=find_ffmpeg(settings.get('ffmpeg',''))
    if not ffmpeg: raise RuntimeError('Cần FFmpeg để chuyển bản ghi thẻ video sang MP4.')
    stage='khởi tạo ghi preview'
    key='studio_capture_'+uuid.uuid4().hex
    raw=Path(output).with_suffix('.capture.webm')
    try:
        page.bring_to_front()
        info=page.evaluate('''async key => {
            const videos=[...document.querySelectorAll('video')].filter(v=>v.readyState>=2 && !v.error && v.duration>0 && Number.isFinite(v.duration));
            const visible=videos.filter(v=>{const r=v.getBoundingClientRect();const css=getComputedStyle(v);
                return r.width>0 && r.height>0 && css.display!=='none' && css.visibility!=='hidden';});
            const area=v=>{const r=v.getBoundingClientRect();return r.width*r.height;};
            visible.sort((a,b)=>Number(!!b.closest('[role=dialog]'))-Number(!!a.closest('[role=dialog]')) || area(b)-area(a));
            const v=visible[0];
            if(!v) throw Error('Video chưa giải mã/phát được; không thể ghi thẻ video.');
            if(v.duration>180) throw Error('Chế độ thử chỉ ghi video tối đa 180 giây.');
            if(!v.captureStream || !window.MediaRecorder) throw Error('Trình duyệt không hỗ trợ ghi thẻ video.');
            const mime=['video/webm;codecs=vp8,opus','video/webm'].find(m=>MediaRecorder.isTypeSupported(m));
            if(!mime) throw Error('Không có codec ghi WebM.');
            const old={loop:v.loop,rate:v.playbackRate,paused:v.paused,time:v.currentTime};
            const s={v,old,chunks:[],done:false,error:'',bytes:0};window[key]=s;
            v.pause();v.loop=false;v.playbackRate=1;
            if(v.currentTime>.01) {
                await new Promise((resolve,reject)=>{
                    const timer=setTimeout(()=>{v.removeEventListener('seeked',done);reject(Error('Không tua được video về đầu.'));},10000);
                    function done(){clearTimeout(timer);resolve();}
                    v.addEventListener('seeked',done,{once:true});v.currentTime=0;
                });
            }
            s.stream=v.captureStream();
            // Tracks may arrive asynchronously after seeking/decoding.
            if(!s.stream.getVideoTracks().length) {
                await Promise.race([
                    new Promise(resolve=>s.stream.addEventListener('addtrack',resolve,{once:true})),
                    new Promise(resolve=>setTimeout(resolve,1500))
                ]);
            }
            if(!s.stream.getVideoTracks().length) throw Error('Nguồn video không cho phép captureStream hoặc chưa có track hình.');
            s.recorder=new MediaRecorder(s.stream,{mimeType:mime,videoBitsPerSecond:6000000});
            s.recorder.ondataavailable=e=>{if(e.data.size){s.bytes+=e.data.size;s.chunks.push(e.data);
                if(s.bytes>256*1024*1024){s.error='Bản ghi vượt 256 MB';s.recorder.stop();}}};
            s.recorder.onerror=()=>{s.error='MediaRecorder không ghi được nguồn video.';s.done=true;};
            s.recorder.onstop=()=>{s.done=true;};
            s.end=()=>{if(s.recorder.state!=='inactive')s.recorder.stop();};
            v.addEventListener('ended',s.end,{once:true});
            s.recorder.start(1000);await v.play();
            return {duration:v.duration,audio:s.stream.getAudioTracks().length>0};
        }''',key)
        report(f"Đang ghi riêng thẻ video từ đầu • {info['duration']:.1f}s • "+('có âm thanh' if info['audio'] else 'nguồn không có track âm thanh'))
        stage='chờ phát và ghi hết video'
        deadline=time.monotonic()+float(info['duration'])+45
        while True:
            if cancel.is_set(): raise Cancelled('Đã dừng ghi thẻ video.')
            state=page.evaluate('(key)=>({done:window[key].done,error:window[key].error})',key)
            if state['error']: raise RuntimeError(state['error'])
            if state['done']: break
            if time.monotonic()>deadline: raise TimeoutError('Video bị đứng khi ghi; chưa lưu bản ghi thiếu.')
            page.wait_for_timeout(250)
        # Pull bounded chunks instead of one giant base64 payload.
        stage='đọc dữ liệu bản ghi'
        count=page.evaluate('(key)=>window[key].chunks.length',key)
        with raw.open('wb') as file:
            for i in range(count):
                if cancel.is_set(): raise Cancelled('Đã dừng ghi thẻ video.')
                encoded=page.evaluate('''async ({key,i})=>{
                    const bytes=new Uint8Array(await window[key].chunks[i].arrayBuffer());
                    let text='';for(let j=0;j<bytes.length;j+=32768)text+=String.fromCharCode(...bytes.subarray(j,j+32768));
                    return btoa(text);
                }''',{'key':key,'i':i})
                file.write(base64.b64decode(encoded))
        if raw.stat().st_size<1024: raise RuntimeError('Bản ghi video rỗng; chưa xuất kết quả.')
        stage='chuyển bản ghi sang MP4 bằng FFmpeg'
        run_process([ffmpeg,'-y','-hide_banner','-loglevel','error','-i',str(raw),
                     '-map','0:v:0','-map','0:a?','-c:v','libx264','-preset','fast','-crf','18',
                     '-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(output)],cancel)
        provenance.update(mode='element_recording',watermark_status='unknown',
                          evidence='HTMLVideoElement.captureStream',audio=info['audio'])
        report('Đã ghi thẻ video và chuyển sang MP4. Logo có sẵn trong khung hình nguồn sẽ vẫn còn.')
        return True
    except Cancelled:
        raise
    except Exception as exc:
        raise RuntimeError(f'{stage}: {exc}') from None
    finally:
        try:
            page.evaluate('''key=>{const s=window[key];if(!s)return;
                s.v.removeEventListener('ended',s.end);
                if(s.recorder && s.recorder.state!=='inactive')s.recorder.stop();
                if(s.stream)s.stream.getTracks().forEach(t=>t.stop());
                s.v.pause();s.v.loop=s.old.loop;s.v.playbackRate=s.old.rate;s.v.currentTime=s.old.time;
                if(!s.old.paused)s.v.play().catch(()=>{});delete window[key];}''',key)
        except Exception: pass
        raw.unlink(missing_ok=True)


def record_api_preview(context, page, sources, output, settings, cancel, report, provenance):
    """Load an API-provided MP4 into an isolated, same-origin preview before recording."""
    from urllib.parse import urlsplit
    import hashlib
    import json
    for candidate in sources:
        if cancel.is_set(): raise Cancelled('Đã dừng tải preview.')
        response=None
        try:
            response=context.request.get(candidate.url,headers={'Referer':page.url},timeout=60000,max_redirects=0)
            if response.status!=200: continue
            if not any(t in response.headers.get('content-type','') for t in ('video/','octet-stream')):continue
            data=response.body()
            if len(data)<1024 or len(data)>256*1024*1024 or b'ftyp' not in data[:64]:continue
        except Exception:
            continue
        finally:
            if response is not None:response.dispose()
        report('Đã lấy nguồn video; FFmpeg đang giải mã và xuất lại toàn bộ hình/âm thanh.')
        import tempfile
        ffmpeg=find_ffmpeg(settings.get('ffmpeg',''))
        if not ffmpeg:raise RuntimeError('Chưa tìm thấy FFmpeg; chọn ffmpeg.exe trong Cài đặt.')
        with tempfile.TemporaryDirectory(prefix='studio_video_') as folder:
            raw=Path(folder)/'source.mp4'
            encoded=Path(folder)/'encoded.mp4'
            raw.write_bytes(data)
            run_process([ffmpeg,'-y','-hide_banner','-loglevel','error','-xerror',
                         '-i',str(raw),'-map','0:v:0','-map','0:a?',
                         '-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p',
                         '-c:a','aac','-movflags','+faststart',str(encoded)],cancel)
            run_process([ffmpeg,'-hide_banner','-loglevel','error','-xerror',
                         '-i',str(encoded),'-f','null','-'],cancel)
            if encoded.stat().st_size<1024:raise RuntimeError('FFmpeg xuất file rỗng.')
            import shutil
            shutil.copyfile(encoded,output)
        provenance.update(mode='ffmpeg_preview_transcode',watermark_status='provider_marked_unwatermarked' if candidate.evidence=='fallback_api:confirmed_unwatermarked' else 'unknown',api_evidence=candidate.evidence,
                          source_sha256=hashlib.sha256(candidate.url.encode()).hexdigest())
        report('Đã xuất MP4 H.264 bằng FFmpeg và kiểm tra giải mã toàn bộ file; logo trong nguồn vẫn giữ nguyên.')
        return True
    raise RuntimeError('Chưa tải được MP4 hợp lệ từ nguồn API của hội thoại.')

