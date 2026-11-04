"""Reject visible Dola AI branding before accepting a supposedly clean source."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading

from engine import Cancelled, find_ffmpeg, run_process

_OCR_LOCK = threading.Semaphore(2)


class WatermarkCheckError(RuntimeError):
    pass
_SCRIPT = r'''
param([string]$Folder)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
[Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime] | Out-Null
$asTask=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await($Operation, $ResultType) {
    $task=$asTask.MakeGenericMethod($ResultType).Invoke($null,@($Operation))
    $task.Wait()
    $task.Result
}
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US'))
if(-not $engine) { $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if(-not $engine) { throw 'OCR unavailable' }
$checked=0;$detected=$false
foreach($image in Get-ChildItem -LiteralPath $Folder -Filter 'frame_*.png') {
    $file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($image.FullName)) ([Windows.Storage.StorageFile])
    $stream=Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap=Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            $result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $checked++
            if($result.Text -match '(?i)\bd[o0]la\s*a[i1l|]\b') { $detected=$true;break }
        } finally { $bitmap.Dispose() }
    } finally { $stream.Dispose() }
}
@{checked=$checked;detected=$detected} | ConvertTo-Json -Compress
'''


def has_dola_watermark(video, cancel, ffmpeg=''):
    """Sample decoded frames; True means logo detected, not a logo-removal step.

    A negative result is only a sample check, never a guarantee about every frame.
    Fail closed when the local verifier cannot run.
    """
    while not _OCR_LOCK.acquire(timeout=.2):
        if cancel.is_set():raise Cancelled('Đã dừng kiểm tra video.')
    try:
        executable=find_ffmpeg(ffmpeg)
        if not executable:raise WatermarkCheckError('Thiếu FFmpeg để kiểm tra logo.')
        powershell=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        if not powershell.is_file():raise WatermarkCheckError('Thiếu Windows OCR để kiểm tra logo; chưa nhận video là nguồn sạch.')
        with tempfile.TemporaryDirectory(prefix='studio_logo_check_') as folder:
            root=Path(folder)
            # First frame plus samples every 2 seconds (up to 60 seconds).
            # Keep text readable even for low-resolution preview sources.
            run_process([executable,'-y','-hide_banner','-loglevel','error','-i',str(video),
                         '-vf',r"select='eq(n,0)+gte(t-prev_selected_t,2)',scale=1080:-2",
                         '-fps_mode','vfr','-frames:v','31',str(root/'frame_%03d.png')],cancel)
            if not list(root.glob('frame_*.png')):raise WatermarkCheckError('Không đọc được khung hình để kiểm tra logo.')
            script=root/'check.ps1';script.write_text(_SCRIPT,encoding='utf-8-sig')
            process=subprocess.Popen([str(powershell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                                      '-File',str(script),'-Folder',str(root)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                import time
                deadline=time.monotonic()+90
                while True:
                    if cancel.is_set():raise Cancelled('Đã dừng kiểm tra logo.')
                    if time.monotonic()>deadline:raise WatermarkCheckError('Kiểm tra logo quá thời gian; chưa nhận video là nguồn sạch.')
                    try:
                        stdout,_=process.communicate(timeout=.2)
                        break
                    except subprocess.TimeoutExpired:pass
            finally:
                if process.poll() is None:process.kill();process.communicate()
            if process.returncode:
                raise WatermarkCheckError('Windows OCR chưa kiểm tra được logo; chưa nhận video là nguồn sạch.')
            result=json.loads(stdout.decode('utf-8-sig'))
            if not result.get('checked'):raise WatermarkCheckError('Chưa kiểm tra được khung hình video.')
            return bool(result['detected'])
    finally:
        _OCR_LOCK.release()
