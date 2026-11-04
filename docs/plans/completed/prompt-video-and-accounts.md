# Plan: Prompt-to-Video & Account Management (Seedance AI Studio)

## Context & Goal
Restore and verify full **Prompt-to-Video (`Nhập prompt ra video`)** and **Account Management (`Quản lý tài khoản`)** functionality in the Qt6 application (`studio_qt.py` / `run_studio.py`), ensuring that:
1. Users can enter single or multi-line prompts directly in the main window, select default video generation parameters (Model, Duration, Aspect Ratio, Dola Mode), attach reference images or edit per-task configurations, and run tasks manually (`▶ Tạo video ngay`, `+ Thêm vào bảng`, `+ Prompt kèm ảnh…`, `▶ Chạy tất cả`, `▶ Chạy đã chọn`, context menu & double-click actions).
2. Account management (`Quản lý tài khoản` / `AccountsDialog`) supports adding accounts (manual login, Facebook login, Facebook batch cookie import, Google cookie import, Dola JSON cookie import), checking live/die status, opening/logging in, configuring proxies (`ProxyDialog` & `SettingsDialog`), renaming accounts, filtering by state (`Tất cả`, `Live`, `Die`, `Hết điểm`, `Nghỉ 7 ngày`, `Chưa kiểm tra`), toggling `Dùng chạy`, and deleting sessions/dead profiles.
3. Browser & extension runtime works out-of-the-box with bundled `extensions/pro` and Patchright/Playwright Chromium.

## Changes Implemented
1. **Environment & Extension Bundle**
   - Provisioned `.venv` (CPython 3.12.13) with all dependencies from `requirements.txt`.
   - Added MV3 extension bundle in `extensions/pro/` (`manifest.json`, `background.js`, `content.js`) providing `SeedanceExtensionLicense.snapshot()`, `#dola-ap-panel`, and `formatDolaPrompt`.
2. **Browser & Backend Discovery**
   - `account_browser_backend.py`: Default `account_backend(account)` to `'chrome'` when `backend` is unspecified and `hidemium_profile_id` is absent.
   - `seedance_browser.py` & `dola_browser.py`: Fall back to `LOCALAPPDATA/ms-playwright/chromium-*/chrome-win*/chrome.exe` when `browser/` is not bundled next to the source/EXE so `--load-extension` works reliably on Chromium 137+.
3. **Prompt-to-Video & Account Management UI (`studio_qt.py`)**
   - Restored the quick prompt composer (`quick_prompt`, `quick_model`, `quick_duration`, `quick_ratio`, `quick_mode`, `quick_run_button`, `add_lines_button`, `add_with_img_button`) and task control bar (`run_all`, `run_selected_button`, `stop_button`, `bulk_config_button`, `select_failed_button`, `delete_selected_button`, `auto_button`).
   - Implemented `sync_quick_settings_ui`, `save_quick_settings`, `_consume_quick_prompts`, `add_quick_task`, `add_and_run_quick_task`, `add_task_with_dialog`, `run_all_tasks`, `run_selected_tasks`, `request_task_runs`, `_on_row_double_clicked`, full `_row_menu`, `open_video`, `reveal_video`, and `open_task_conversation`.
   - Fixed `batch_worker` so manual prompt-to-video runs do not lock the user's account into a 7-day cooldown (`run_success` is only emitted on manual runs if `cooldown_after_manual_run` is explicitly set; `AutoJobs` retains its single-use cooldown behavior).
   - Enhanced `AccountsDialog` and `AccountRow` with `Đổi tên` (`rename_account_dialog`) and the `Nghỉ 7 ngày` (`rest`) filter chip, and updated `ConfigDialog` to include `Skill 30s`.

## Validation Evidence
- Executed full unit & integration test suite (`.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v`): **52 tests ran, 52 passed (`OK`, 0 failures, 0 skipped)**, including:
  - `tests/test_prompt_and_accounts_ui.py` (5 end-to-end tests covering single/multi-line prompt creation, quick settings synchronization, Dola prompt-to-video execution, `PromptDialog` + `ConfigDialog` + saved conversation video recovery, and `AccountsDialog` cookie import / rename / proxy / check live / filter / delete).
  - `tests/test_extension_loading.py`, `tests/test_profile_proxy.py`, `tests/test_tab_reuse.py`, `tests/test_seedance.py`, `tests/test_seedance_30s.py`, `tests/test_seedance_prompt.py`, `tests/test_seedance_video.py`, `tests/test_session_recovery_bounded.py`, `tests/test_fast_cookie_session.py`, `tests/test_hidemium.py`, `tests/test_auto_single_use.py`.
