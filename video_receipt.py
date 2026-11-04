"""Fail-closed media receipt checks for a single task tab."""
PROBE = r"""() => {
    const key='__studioVideoReceiptV2';
    function scan() {
        const roots=[...document.querySelectorAll('main, [class*="message-list"], [class*="list_items"]')];
        if(!roots.length) return {known:false,video:false,pending:false};
        const excluded='[data-message-author-role="user"], [data-message-role="user"], nav, aside, [contenteditable="true"], [data-input-engine-action-source], .message-action-button-main';
        const inReply=el=>!el.closest(excluded);
        const shown=el=>!!el.getClientRects().length && getComputedStyle(el).display!=='none' && getComputedStyle(el).visibility!=='hidden';
        let video=false,pending=false;
        for(const root of roots) {
            // Đuôi selector giống StudioRelay (3 BIG): video, data-video-url, data-play-url, src *.mp4
            for(const el of root.querySelectorAll('video, source, xg-player, .xgplayer, [data-xgplayerid], [mediatype="video"], [class*="block-video-"], [data-flow-product-card-root], [data-testid*="video" i], [data-video-url], [data-play-url], [src*=".mp4"]')) {
                if(!inReply(el))continue;
                // Even a temporarily hidden video with a source is a receipt.
                if(shown(el) || (el.tagName==='VIDEO' && (el.currentSrc || el.src || el.querySelector('source[src]'))) || (el.tagName==='SOURCE' && el.src)) video=true;
                if(el.getAttribute('data-video-url') || el.getAttribute('data-play-url')) video=true;
            }
            for(const el of root.querySelectorAll('[class*="video" i]')) {
                if(!inReply(el)||!shown(el))continue;
                const r=el.getBoundingClientRect();
                if(r.width>=80 && r.height>=60) video=true;
            }
            for(const el of root.querySelectorAll('a[href]')) {
                if(!inReply(el))continue;
                try {const u=new URL(el.href);if(['http:','https:'].includes(u.protocol) && u.hostname.endsWith('.dola.com') && u.pathname.includes('/video/')) video=true;}catch{}
            }
            // A large picture in the assistant reply is a video poster or result.
            // It is not proof that the reply is text-only.
            for(const img of root.querySelectorAll('img')) {
                if(!inReply(img)||!shown(img))continue;
                const r=img.getBoundingClientRect();
                if(r.width>=100 && r.height>=80) pending=true;
            }
        }
        return {known:true,video,pending};
    }
    let state=window[key];
    if(!state) {
        state={seen:false,pendingSeen:false};window[key]=state;
        const remember=()=>{const r=scan();state.seen ||= r.video;state.pendingSeen ||= r.pending;};
        state.observer=new MutationObserver(remember);
        state.observer.observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['src','class','style','poster']});
        remember();
    }
    const result=scan();state.seen ||= result.video;state.pendingSeen ||= result.pending;
    return {...result,video:state.seen,pending:state.pendingSeen};
}"""


def media_receipt(page):
    try:
        result=page.evaluate(PROBE)
        if isinstance(result,dict) and result.get('known') is True:
            from mp4_attachment import scan_files
            if scan_files(page).get('count'):
                result['video'] = True
            return result
    except Exception:
        pass
    # Navigation, detached DOM and selector errors must never permit a retry.
    return {'known':False,'video':False,'pending':False}


def retry_is_safe(page):
    state=media_receipt(page)
    return state['known'] and not state['video'] and not state['pending']
