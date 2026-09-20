"""Fresh inbox-style frontend using the existing HarborCheck API and database.
Run: python gmail_dashboard.py
"""
import os
import json
from pathlib import Path
from app import Application

ROOT = Path(__file__).resolve().parent


class InboxDashboard:
    def __init__(self):
        self.backend = Application()

    def __call__(self, environ, start_response):
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/inbox.css': ('inbox.css', 'text/css; charset=utf-8'),
                  '/inbox.js': ('inbox.js', 'text/javascript; charset=utf-8')}
        route = environ.get('PATH_INFO', '/')
        if route in assets and environ.get('REQUEST_METHOD') == 'GET':
            filename, content_type = assets[route]
            data = (ROOT / 'inbox_frontend' / filename).read_bytes()
            start_response('200 OK', [('Content-Type', content_type),
                ('Content-Length', str(len(data))), ('Cache-Control', 'no-store'),
                ('X-Content-Type-Options', 'nosniff')])
            return [data]
        if route == '/api/state' and environ.get('REQUEST_METHOD') == 'GET':
            response = {}
            def capture(status, headers):
                response.update(status=status, headers=headers)
            data = json.loads(b''.join(self.backend(environ, capture)))
            for row in data.get('emails', []):
                email = self.backend.index[row['email_id']]
                row['sender'] = email.get('from', '')
                row['preview'] = ' '.join(email.get('body', '').split())[:230]
            raw = json.dumps(data, ensure_ascii=False).encode()
            headers = [(k, v) for k, v in response['headers'] if k.lower() != 'content-length']
            start_response(response['status'], headers + [('Content-Length', str(len(raw)))])
            return [raw]
        if route.startswith('/api/') or route == '/health':
            return self.backend(environ, start_response)
        start_response('404 Not Found', [('Content-Type', 'text/plain')])
        return [b'Not found']


if __name__ == '__main__':
    application = InboxDashboard()
    port = int(os.environ.get('INBOX_PORT', '8001'))
    print(f'Inbox dashboard: http://localhost:{port}', flush=True)
    print('Admin token: ' + application.backend.token, flush=True)
    print('Use only one dashboard server for processing/review at a time.', flush=True)
    try:
        from waitress import serve
        serve(application, host='127.0.0.1', port=port)
    except ImportError:
        from wsgiref.simple_server import make_server
        make_server('127.0.0.1', port, application).serve_forever()
