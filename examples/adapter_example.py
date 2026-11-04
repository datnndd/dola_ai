"""Protocol template only. Implement with an authorized provider SDK/API."""
import json
import sys


def main():
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    request = json.load(sys.stdin)
    if request.get('protocol') != 1:
        print(json.dumps({'error': 'Unsupported adapter protocol'}))
        return
    # 1. Resolve authorized credentials for request['account'].
    # 2. Validate settings/model/duration against the provider's capabilities.
    # 3. Submit prompt and images, poll within the configured timeout.
    # 4. Download the result to settings['output'] with a unique filename.
    # 5. print(json.dumps({'video_path': str(absolute_mp4_path)}))
    print(json.dumps({'error': 'Adapter mẫu chưa kết nối dịch vụ AI. Cần triển khai API/SDK của nhà cung cấp.'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
