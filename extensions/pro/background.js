globalThis.SeedanceExtensionLicense = {
  snapshot() {
    return { ok: false, configured: false };
  }
};
chrome.runtime.onInstalled.addListener(() => {});
