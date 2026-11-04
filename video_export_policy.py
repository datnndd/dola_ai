"""Bound the clean-source wait in the legacy export workflow."""
def use_served_video(video_seen_at, now, grace=20):
    return video_seen_at is not None and now-video_seen_at >= grace
def verified_candidates(clean, conversation, players, attachments):
    """Only current-turn sources; marked URLs excluded, remaining files need OCR."""
    from dola_media import VideoSource, watermark_status, media_url
    sources = list(clean)
    seen = {s.url for s in sources}
    for url in list(attachments) + list(players):
        if url in seen or watermark_status(url) == 'marked':
            continue
        if not url.startswith('blob:') and not media_url(url):
            continue
        sources.append(VideoSource(url, conversation, (), 'assistant_mp4_attachment'))
        seen.add(url)
    return sources
