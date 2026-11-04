"""Recognize assistant MP4 attachments, including cards with download buttons."""
SCAN = r'''(baseline) => {
    const excluded='[data-message-author-role="user"], [data-message-role="user"], .justify-end, form, [contenteditable="true"], nav, aside, [data-input-engine-action-source]';
    const fileName=/\.mp4(?=$|[\s?#)\]"'])/i;
    const matches=[];
    document.querySelectorAll('[data-studio-mp4-result]').forEach(e=>e.removeAttribute('data-studio-mp4-result'));
    for(const el of document.querySelectorAll('main a, main button, main [role="button"], main [data-download-url], main [data-file-url], main [data-file-name], main [data-filename], main [data-testid*="file" i], main [class*="file-card" i], main [class*="fileCard"], main [data-flow-product-card-root], main [data-testid*="product-card" i], main [data-testid*="flow-product" i]')) {
        if(el.closest(excluded))continue;
        const text=[el.getAttribute('download'),el.getAttribute('title'),el.getAttribute('aria-label'),el.getAttribute('data-file-name'),el.getAttribute('data-filename'),el.innerText,...[...el.querySelectorAll('span,[title],[data-file-name]')].map(n=>n.getAttribute('title')||n.getAttribute('data-file-name')||n.innerText)].filter(Boolean).join(' ');
        const raw=el.getAttribute('href')||el.getAttribute('data-download-url')||el.getAttribute('data-file-url')||'';
        const isCard=el.matches('[data-flow-product-card-root],[data-testid*="product-card" i],[data-testid*="flow-product" i],[class*="file-card" i],[class*="fileCard"],[class*="video-card" i]');
        const hasDownloadHint = /(?:\.mp4|tải xuống|tải về|tai xuong|tai ve|download|tải video|save video|lưu)/i.test(text + ' ' + raw)
            || el.hasAttribute('download')
            || el.hasAttribute('data-download-url')
            || el.hasAttribute('data-file-url')
            || !!el.querySelector('a[download], a[href*="download" i], button[aria-label*="tải" i], button[aria-label*="download" i], [role="button"][aria-label*="tải" i]');
        if(!hasDownloadHint && !isCard) continue;
        const key=raw+'|'+text;
        if((baseline||[]).includes(key))continue;
        // Never treat a paragraph discussing a filename as a downloadable file.
        // Product-card (preview MP4) có tên file ở div con, nút tải là icon ↓: ưu tiên nút tải,
        // sau đó bất kỳ nút/link nào, cuối cùng dùng chính card để bấm.

        let action=el.matches('a[href],button,[role="button"]') ? el : (
            el.querySelector('a[download],a[href*="download" i],button[aria-label*="download" i],button[aria-label*="tải" i],button[aria-label*="save" i],[role="button"][aria-label*="tải" i]')
            || el.querySelector('a[href],button,[role="button"]')
            || (isCard || raw ? el : null));
        if(!action || action.closest(excluded) || !el.getClientRects().length)continue;
        if(matches.some(item=>item.action===action))continue;
        const href=raw||action.getAttribute('href')||action.getAttribute('data-download-url')||'';
        let url='';try {const u=new URL(href,location.href); if(href && ['http:','https:','blob:'].includes(u.protocol))url=u.href;}catch{}
        action.setAttribute('data-studio-mp4-result',String(matches.length));
        matches.push({action,url,key});
    }
    return {count:matches.length,urls:matches.map(m=>m.url).filter(Boolean),keys:matches.map(m=>m.key)};
}'''


def scan_files(page, baseline=()):
    try:
        result = page.evaluate(SCAN, list(baseline))
        return result if isinstance(result, dict) else {'count': 0, 'urls': []}
    except Exception:
        return {'count': 0, 'urls': []}


def download_file_card(page, output, cancel, report, baseline=()):
    """Use the site's real download handler; validate bytes before accepting it."""
    from dola_browser import check_cancel
    from pathlib import Path
    temp = Path(str(output) + '.attachment')
    try:
        check_cancel(cancel)
        if not scan_files(page, baseline)['count']:
            return False
        card = page.locator('[data-studio-mp4-result]').last
        from dola_media import media_url
        href = card.get_attribute('href') or ''
        if href and not href.startswith('blob:'):
            from urllib.parse import urljoin
            if not media_url(urljoin(page.url, href)):
                return False
        # Card preview: nút tải (↓) thường hiện khi hover; ưu tiên bấm đúng nút tải trong card.
        try:
            card.hover(timeout=2000)
        except Exception:
            pass
        target = card
        try:
            dl = card.locator('a[download], a[href*="download" i], button[aria-label*="tải" i], button[aria-label*="download" i], '
                              'button[aria-label*="save" i], [role="button"][aria-label*="tải" i], button:has-text("Tải xuống"), button:has-text("Tải về"), button:has-text("Download")')
            if dl.count():
                target = dl.first
        except Exception:
            pass
        with page.expect_download(timeout=15000) as pending:
            target.click(timeout=3000)
        pending.value.save_as(str(temp))
        check_cancel(cancel)
        with temp.open('rb') as stream:
            header = stream.read(64)
        is_media = (b'ftyp' in header or b'\x1aE\xdf\xa3' in header or header.startswith(b'\x89PNG') or header.startswith(b'\xff\xd8') or header.startswith(b'RIFF'))
        if temp.stat().st_size < 1024 or not is_media:
            report('File đính kèm chưa phải MP4 hợp lệ; tiếp tục chờ video.')
            return False
        temp.replace(output)
        report('Đã tải file MP4 đính kèm của câu trả lời.')
        return True
    except Exception:
        check_cancel(cancel)
        return False
    finally:
        temp.unlink(missing_ok=True)
