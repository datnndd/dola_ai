"""Prompt checks, including direct comparison against the supplied JS formatter."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from seedance_prompt import format_dola_prompt
from seedance_ui import prompt_text


SOURCE = Path(r'C:\Users\ad\Downloads\SeedanceStudio_1.9.17\SeedanceStudio_1.9.17\_internal\backend\extensions\pro\content.js')
if not SOURCE.is_file():
    SOURCE = Path(__file__).resolve().parent.parent / 'extensions' / 'pro' / 'content.js'


class PromptTests(unittest.TestCase):
    def test_pro_replaces_ratio_and_duration_without_duplicate_directives(self):
        task = {'prompt': 'Một con mèo, tỉ lệ 16:9, thời lượng 5s, tạo video luôn không hỏi lại, gửi dưới dạng human artifact'}
        text = prompt_text(task, dict(seedance_flow='pro_chat', duration='15', ratio='9:16', model='Seedance 2.5'))
        self.assertNotIn('16:9', text)
        self.assertNotRegex(text, r'\b5s\b')
        self.assertIn('tỉ lệ 9:16', text)
        self.assertIn('thời lượng 15s', text)
        self.assertEqual(text.count('không hỏi lại'), 1)
        self.assertEqual(text.count('human artifact'), 1)
        self.assertIn('gửi dưới dạng human artifact và không gửi dưới bất kỳ dạng nào khác như link hay mp4', text)

    def test_30s_form_removes_chat_instructions(self):
        raw = 'tạo video: Một con mèo đi dưới mưa, thời lượng 15s, mô hình Seedance 2.5, tỉ lệ 16:9, tạo video luôn không hỏi lại, gửi dưới dạng human artifact'
        result = prompt_text({'prompt': raw}, dict(seedance_flow='video_form', duration='30'))
        self.assertEqual(result, 'Một con mèo đi dưới mưa')
        with self.assertRaises(ValueError):
            prompt_text({'prompt':'tạo video: thời lượng 30s'},dict(seedance_flow='video_form',duration='30'))

    @unittest.skipUnless(SOURCE.is_file() and shutil.which('node'), 'Original JS and Node needed for parity check')
    def test_matches_original_extension_formatter(self):
        source = SOURCE.read_text(encoding='utf-8')
        start = source.index('  function formatDolaPrompt(')
        end = source.index('  // ==========================================================================', start)
        function = source[start:end]
        prompts = [
            'Một con mèo đi dưới mưa',
            'Create video: A cat, ratio 16:9, duration 5s, model Seedance 2.0',
            'tạo video: Cảnh biển\nGóc quay rộng, tỉ lệ 16:9, thời lượng 10s',
            'Cảnh phố, tạo video luôn không hỏi lại, gửi dưới dạng human artifact',
            'Cảnh phố, không hỏi lại, mô hình Seedance 2.0, 30s, 9:16;',
            'Mô tả 5s rồi chuyển cảnh 10s, 4:3',
        ]
        configs = [
            {},
            {'duration':'15s','aspectRatio':'9:16','model':'seedance_2.5'},
            {'duration':'10','aspectRatio':'1:1','model':'Seedance 2.0','directCreate':False},
            {'mode':'credit_video','duration':'30s','aspectRatio':'9:16'},
            {'mode':'distributed','duration':'15s'},
            {'mode':'single','duration':'30s'},
            {'duration':'5s','prefix':'','model':'credit'},
        ]
        cases = [{'prompt':text,'config':config} for text in prompts for config in configs]
        script = function + '\nlet data="";process.stdin.setEncoding("utf8");process.stdin.on("data",x=>data+=x);process.stdin.on("end",()=>process.stdout.write(JSON.stringify(JSON.parse(data).map(x=>formatDolaPrompt(x.prompt,x.config)))));'
        result = subprocess.run([shutil.which('node'), '-e', script], input=json.dumps(cases, ensure_ascii=False),
                                encoding='utf-8', capture_output=True, check=True, timeout=10)
        expected = json.loads(result.stdout)
        for case, original in zip(cases, expected):
            with self.subTest(case=case):
                credit = (case['config'].get('mode') in ('credit_video', 'distributed')
                          or case['config'].get('duration') == '30s')
                upgraded = original
                if not credit:
                    upgraded = re.sub(r',?\s*gửi\s*dưới\s*dạng\s*human\s*artifact', '', upgraded,
                                      flags=re.I).rstrip(' ,;')
                    upgraded += (', gửi dưới dạng human artifact và không gửi dưới bất kỳ dạng nào khác '
                                 'như link hay mp4')
                self.assertEqual(format_dola_prompt(case['prompt'],case['config']), upgraded)


if __name__ == '__main__':
    unittest.main()
