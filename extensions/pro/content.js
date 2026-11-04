(() => {
  function formatDolaPrompt(rawPrompt, config = {}) {
    let clean = String(rawPrompt || '').trim();
    if (!clean) return '';
    const credit = config.mode === 'credit_video' || config.mode === 'distributed' || config.duration === '30s';
    const prefix = config.prefix !== undefined ? config.prefix : 'tạo video: ';
    const ratio = config.aspectRatio && config.aspectRatio !== 'none' ? config.aspectRatio : '16:9';
    const ratios = '(?:16:9|9:16|1:1|4:3|21:9)';
    const artifactDirective = /,?\s*gửi\s*dưới\s*dạng\s*human\s*artifact(?:\s*và\s*không\s*gửi\s*dưới\s*bất\s*kỳ\s*dạng\s*nào\s*khác\s*như\s*link\s*hay\s*mp4)?/gi;
    const artifactOnly = 'gửi dưới dạng human artifact';

    if (credit) {
      clean = clean.replace(/^(?:tạo\s*video|create\s*videos?)\s*:\s*/gi, '');
      clean = clean.replace(/,?\s*tạo\s*video\s*luôn\s*(?:ko|không)\s*hỏi\s*lại/gi, '');
      clean = clean.replace(artifactDirective, '');
      clean = clean.replace(/,?\s*(?:ko|không)\s*hỏi\s*lại/gi, '');
      clean = clean.replace(/,?\s*(?:thời\s*lượng|duration)\s*[:\s]*[0-9]+\s*s?/gi, '');
      clean = clean.replace(/,?\s*(?:mô\s*hình|model|seedance)\s*[:\s]*[A-Za-z0-9_.\s]+/gi, '');
      clean = clean.replace(new RegExp('(?:,\\s*)?(?:tỉ\\s*lệ|ratio)?\\s*[:\\s]*\\b' + ratios + '\\b', 'gi'), '');
      return clean.replace(/[,;\s]+$/g, '').trim();
    }

    let duration = String(config.duration || '15s').trim();
    if (!/^[0-9]+s$/i.test(duration)) {
      const number = duration.replace(/[^0-9]/g, '') || '15';
      duration = number + 's';
    }
    if (duration === '30s') duration = '15s';
    const direct = config.directCreate !== false;
    let formatted = clean;
    if (prefix && String(prefix).trim() && !formatted.toLowerCase().startsWith(String(prefix).trim().toLowerCase())) {
      formatted = String(prefix).trim() + ' ' + formatted;
    }
    if (ratio && ratio !== 'none') {
      formatted = formatted.replace(new RegExp('(?:,\\s*)?(?:tỉ\\s*lệ|ratio)\\s*[:\\s]*\\b' + ratios + '\\b', 'gi'), '');
      formatted = formatted.replace(new RegExp('(?:,\\s*)?\\b' + ratios + '\\b', 'gi'), '');
      formatted = formatted.replace(/[,;\s]+$/g, '').trim();
      formatted += ', tỉ lệ ' + ratio;
    }
    formatted = formatted.replace(/(?:,\s*)?(?:thời\s*lượng|duration)\s*[:\s]*[0-9]+\s*s?/gi, '');
    formatted = formatted.replace(/(?:,\s*)?\b(?:5|10|15|30)\s*s\b/gi, '');
    formatted = formatted.replace(/[,;\s]+$/g, '').trim();
    formatted += ', thời lượng ' + duration;
    const model = String(config.model || '').trim();
    if (model && model !== 'credit' && !/^credit_/i.test(model)) {
      const label = model.includes('2.5') ? 'Seedance 2.5' : model.includes('2.0') ? 'Seedance 2.0' : model;
      if (!/seedance\s*2\.[05]|mô\s*hình\s*2\.[05]|model\s*2\.[05]/i.test(formatted)) {
        formatted += ', mô hình ' + label;
      }
    }
    if (direct) {
      if (!/không\s*hỏi\s*lại/i.test(formatted)) {
        formatted += ', tạo video luôn không hỏi lại';
      }
    }
    formatted = formatted.replace(artifactDirective, '');
    formatted = formatted.replace(/[,;\s]+$/g, '').trim();
    formatted += ', ' + artifactOnly;
    return formatted;
  }

  // ==========================================================================
  function parseScriptPrompts(rawText) {
    return String(rawText || '')
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
  }

  function ensureDolaPanel() {
    if (typeof document === 'undefined') return;
    if (document.getElementById('dola-ap-panel')) return;
    const target = document.body || document.documentElement;
    if (!target) return;
    const panel = document.createElement('div');
    panel.id = 'dola-ap-panel';
    panel.setAttribute('data-dola-ap', '1');
    panel.style.display = 'none';
    target.appendChild(panel);
  }

  if (typeof document !== 'undefined') {
    ensureDolaPanel();
    document.addEventListener('DOMContentLoaded', ensureDolaPanel, { once: true });
  }
})();
