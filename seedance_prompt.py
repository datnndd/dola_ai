"""Python port of the supplied Pro extension's formatDolaPrompt function."""
import re


def format_dola_prompt(raw_prompt, config=None):
    config = config or {}
    clean = str(raw_prompt or '').strip()
    if not clean:
        return ''
    credit = config.get('mode') in ('credit_video', 'distributed') or config.get('duration') == '30s'
    prefix = config.get('prefix', 'tạo video: ')
    ratio = config.get('aspectRatio')
    ratio = ratio if ratio and ratio != 'none' else '16:9'

    def sub(pattern, text):
        return re.sub(pattern, '', text, flags=re.I)

    # JavaScript \b uses ASCII word characters, even in Vietnamese text.
    left = r'(?<![A-Za-z0-9_])'
    right = r'(?![A-Za-z0-9_])'
    ratios = r'(?:16:9|9:16|1:1|4:3|21:9)'
    artifact_directive = (r',?\s*gửi\s*dưới\s*dạng\s*human\s*artifact'
                          r'(?:\s*và\s*không\s*gửi\s*dưới\s*bất\s*kỳ\s*dạng\s*nào\s*khác'
                          r'\s*như\s*link\s*hay\s*mp4)?')
    artifact_only = ('gửi dưới dạng human artifact và không gửi dưới bất kỳ dạng nào khác '
                     'như link hay mp4')
    if credit:
        clean = sub(r'^(?:tạo\s*video|create\s*videos?)\s*:\s*', clean)
        clean = sub(r',?\s*tạo\s*video\s*luôn\s*(?:ko|không)\s*hỏi\s*lại', clean)
        clean = sub(artifact_directive, clean)
        clean = sub(r',?\s*(?:ko|không)\s*hỏi\s*lại', clean)
        clean = sub(r',?\s*(?:thời\s*lượng|duration)\s*[:\s]*[0-9]+\s*s?', clean)
        clean = sub(r',?\s*(?:mô\s*hình|model|seedance)\s*[:\s]*[A-Za-z0-9_.\s]+', clean)
        clean = sub(r'(?:,\s*)?(?:tỉ\s*lệ|ratio)?\s*[:\s]*' + left + ratios + right, clean)
        return sub(r'[,;\s]+$', clean).strip()

    duration = str(config.get('duration') or '15s').strip()
    if not re.fullmatch(r'[0-9]+s', duration, re.I):
        number = re.sub(r'[^0-9]', '', duration) or '15'
        duration = number + 's'
    if duration == '30s':
        duration = '15s'
    direct = config.get('directCreate') is not False
    formatted = clean
    if prefix and prefix.strip() and not formatted.lower().startswith(prefix.strip().lower()):
        formatted = prefix.strip() + ' ' + formatted
    if ratio and ratio != 'none':
        formatted = sub(r'(?:,\s*)?(?:tỉ\s*lệ|ratio)\s*[:\s]*' + left + ratios + right, formatted)
        formatted = sub(r'(?:,\s*)?' + left + ratios + right, formatted)
        formatted = sub(r'[,;\s]+$', formatted).strip()
        formatted += ', tỉ lệ ' + ratio
    formatted = sub(r'(?:,\s*)?(?:thời\s*lượng|duration)\s*[:\s]*[0-9]+\s*s?', formatted)
    formatted = sub(r'(?:,\s*)?' + left + r'(?:5|10|15|30)\s*s' + right, formatted)
    formatted = sub(r'[,;\s]+$', formatted).strip()
    formatted += ', thời lượng ' + duration
    model = str(config.get('model') or '').strip()
    if model and model != 'credit' and not re.match(r'credit_', model, re.I):
        label = 'Seedance 2.5' if '2.5' in model else 'Seedance 2.0' if '2.0' in model else model
        if not re.search(r'seedance\s*2\.[05]|mô\s*hình\s*2\.[05]|model\s*2\.[05]', formatted, re.I):
            formatted += ', mô hình ' + label
    if direct:
        if not re.search(r'không\s*hỏi\s*lại', formatted, re.I):
            formatted += ', tạo video luôn không hỏi lại'
    # Replace older/shorter variants so every Pro chat prompt carries exactly
    # one explicit output contract, independent of the direct-create toggle.
    formatted = sub(artifact_directive, formatted)
    formatted = sub(r'[,;\s]+$', formatted).strip()
    formatted += ', ' + artifact_only
    return formatted
