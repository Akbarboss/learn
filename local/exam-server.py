#!/usr/bin/env python3
"""
Red Tide test — local server.

Runs on one computer and serves the test to any phone on the same Wi-Fi.
The phone needs nothing: no Claude account, no app, no install.

    python exam-server.py

It prints an address like http://192.168.1.14:8765 — open that on the phone.

What it does that the hosted test cannot:

  * reads each learner's ticked words straight from the shared Firebase
    database, so there is no code to copy across;
  * marks answers with Claude through this machine's API key, so the
    learner never signs in and never pays;
  * keeps every finished test in results/ so you can look at them later.

Without an API key it still runs: answers are checked against the
dictionary, and anything the dictionary rejects is saved for you to look
over at /results instead of being called wrong.

The key is read from ANTHROPIC_API_KEY, or from api-key.txt next to this
file. It is never sent to the phone and never written to the results.
"""

import http.server
import json
import os
import re
import socket
import socketserver
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, 'results')
PORT = int(os.environ.get('EXAM_PORT', '8765'))

FIREBASE = 'https://learn-english-wordbook-default-rtdb.firebaseio.com'
PEOPLE = ['Akbar', 'Abror', 'Muhammadali']

MODEL = os.environ.get('EXAM_MODEL', 'claude-haiku-4-5-20251001')
API_URL = 'https://api.anthropic.com/v1/messages'


def read_key():
    key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if key:
        return key
    path = os.path.join(HERE, 'api-key.txt')
    if os.path.exists(path):
        with io_open(path) as fh:
            return fh.read().strip()
    return ''


def io_open(path, mode='r'):
    return open(path, mode, encoding='utf-8')


API_KEY = read_key()


def lan_ip():
    """The address other devices on this Wi-Fi should use."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()


def slug(text):
    """Must match the wordbook's own slug(), or the ticks will not line up."""
    s = text.lower()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = re.sub(r'^-|-$', '', s)
    return s[:48]


def load_words():
    """Pull [en, ru, uz] out of the wordbook, in its own order."""
    page = io_open(os.path.join(HERE, '..', 'index.html')).read()
    start = page.index('const DATA=[')
    end = page.index('\n];', start)
    row_re = re.compile(r'^\["([^"]*)","([^"]*)","([^"]*)","([^"]*)"')
    chap_re = re.compile(r'^\["([^"]*)","[^"]*","[^"]*","[a-z]+",\[$')
    out = []
    for line in page[start:end].split('\n'):
        if chap_re.match(line):
            continue
        if not line.startswith('["') or not line.rstrip().endswith((']', '],')):
            continue
        m = row_re.match(line)
        if m:
            en, ipa, ru, uz = m.groups()
            out.append([en, ru, uz])
    return out


WORDS = load_words()
KEYS = [slug(w[0]) for w in WORDS]


def firebase_learned(name):
    """That learner's ticked words, as indices into WORDS."""
    url = '%s/%s.json' % (FIREBASE, name)
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(url, timeout=12, context=ctx) as r:
            data = json.loads(r.read().decode('utf-8') or 'null')
    except Exception as e:
        return None, str(e)
    if not isinstance(data, dict):
        return [], None
    have = set(data.get('learned') or [])
    return [i for i, k in enumerate(KEYS) if k in have], None


JUDGE_PROMPT = (
    "You are marking one answer in a vocabulary test. The learner is a teenager "
    "learning English; their first languages are Uzbek and Russian.\n\n"
    "{direction}\n\n"
    "English entry: {en}\n"
    "Russian in our dictionary: {ru}\n"
    "Uzbek in our dictionary: {uz}\n"
    "The learner answered: {given}\n\n"
    "Accept the answer when it means the same thing: a synonym we did not list, "
    "another grammatical form, a different but fair wording, a spelling slip, or "
    "Uzbek written in Latin letters. Reject it when it means something else, when "
    "it is only loosely associated, or when it is empty or nonsense.\n\n"
    'Reply with only JSON: {{"ok": true or false, "note": "..."}} where note is at '
    "most eight words in Russian saying why."
)


def ask_claude(item, given):
    """Returns (ok, note) or (None, reason) when no verdict could be had."""
    if not API_KEY:
        return None, 'nokey'

    if item.get('dir') == 1:
        direction = ('The learner saw the English word and had to give its meaning '
                     'in Russian or Uzbek.')
    else:
        shown = 'Russian' if item.get('show') == 'ru' else 'Uzbek'
        direction = ('The learner saw the %s translation and had to give the '
                     'English word.' % shown)

    body = json.dumps({
        'model': MODEL,
        'max_tokens': 200,
        'messages': [{
            'role': 'user',
            'content': JUDGE_PROMPT.format(
                direction=direction, en=item['en'], ru=item['ru'],
                uz=item['uz'], given=given),
        }],
    }).encode('utf-8')

    req = urllib.request.Request(API_URL, data=body, method='POST')
    req.add_header('content-type', 'application/json')
    req.add_header('x-api-key', API_KEY)
    req.add_header('anthropic-version', '2023-06-01')
    try:
        with urllib.request.urlopen(req, timeout=30,
                                    context=ssl.create_default_context()) as r:
            payload = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:200]
        print('  [judge] HTTP %s %s' % (e.code, detail))
        return None, 'http_%s' % e.code
    except Exception as e:
        print('  [judge] %s' % e)
        return None, 'unreachable'

    text = ''
    for block in payload.get('content', []):
        if block.get('type') == 'text':
            text += block.get('text', '')
    m = re.search(r'\{.*\}', text, re.S)
    if not m:
        return None, 'unparsed'
    try:
        got = json.loads(m.group(0))
    except Exception:
        return None, 'unparsed'
    if not isinstance(got.get('ok'), bool):
        return None, 'unparsed'
    return got['ok'], str(got.get('note') or '')


def save_result(rec):
    os.makedirs(RESULTS, exist_ok=True)
    stamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    who = re.sub(r'[^A-Za-z]', '', str(rec.get('who') or 'someone'))
    path = os.path.join(RESULTS, '%s_%s.json' % (stamp, who))
    with io_open(path, 'w') as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    return os.path.basename(path)


def all_results():
    if not os.path.isdir(RESULTS):
        return []
    out = []
    for name in sorted(os.listdir(RESULTS), reverse=True):
        if not name.endswith('.json'):
            continue
        try:
            with io_open(os.path.join(RESULTS, name)) as fh:
                rec = json.load(fh)
            rec['_file'] = name
            out.append(rec)
        except Exception:
            pass
    return out


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def log_message(self, fmt, *args):
        pass    # the interesting lines are printed by hand below

    def _send(self, code, payload, ctype='application/json; charset=utf-8'):
        raw = payload if isinstance(payload, bytes) else \
            json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = self.path.split('?')[0]
        query = {}
        if '?' in self.path:
            for part in self.path.split('?', 1)[1].split('&'):
                if '=' in part:
                    k, v = part.split('=', 1)
                    query[k] = urllib.parse.unquote(v)

        if path == '/':
            self.path = '/exam.html'
            return super().do_GET()

        if path == '/api/words':
            return self._send(200, {'words': WORDS, 'people': PEOPLE,
                                    'claude': bool(API_KEY)})

        if path == '/api/learned':
            who = query.get('who', '')
            if who not in PEOPLE:
                return self._send(400, {'error': 'unknown learner'})
            idx, err = firebase_learned(who)
            if idx is None:
                print('  [learned] %s: could not reach Firebase (%s)' % (who, err))
                return self._send(200, {'indices': [], 'offline': True})
            print('  [learned] %s: %d ticked words' % (who, len(idx)))
            return self._send(200, {'indices': idx, 'offline': False})

        if path == '/results':
            return self._send(200, results_page().encode('utf-8'),
                              'text/html; charset=utf-8')

        return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        try:
            data = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
        except Exception:
            return self._send(400, {'error': 'bad json'})

        if self.path == '/api/judge':
            item = data.get('item') or {}
            given = str(data.get('given') or '')
            ok, note = ask_claude(item, given)
            if ok is None:
                return self._send(200, {'verdict': 'unknown', 'reason': note})
            print('  [judge] %-22s <- %-22s %s' % (
                item.get('en', '')[:22], given[:22], 'OK' if ok else 'no'))
            return self._send(200, {'verdict': 'ok' if ok else 'no', 'note': note})

        if self.path == '/api/result':
            data['at'] = datetime.now().isoformat(timespec='seconds')
            name = save_result(data)
            print('  [result] %s  %s/%s  -> %s' % (
                data.get('who'), data.get('ok'), data.get('total'), name))
            return self._send(200, {'saved': name})

        return self._send(404, {'error': 'no such endpoint'})


def esc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def results_page():
    rows = all_results()
    body = []
    for r in rows:
        total = r.get('total') or 0
        ok = r.get('ok') or 0
        pct = round(ok / total * 100) if total else 0
        body.append(
            '<article><header><b>%s</b><span>%s</span></header>'
            '<p class="score">%d / %d <i>(%d%%)</i></p>' % (
                esc(r.get('who')), esc(r.get('at', '')), ok, total, pct))
        missed = r.get('missed') or []
        if missed:
            body.append('<p class="m">Missed: %s</p>' %
                        esc(', '.join(str(x) for x in missed)))
        unsure = r.get('unsure') or []
        if unsure:
            body.append('<p class="u"><b>Needs your eye</b> (no Claude verdict):</p><ul>')
            for u in unsure:
                body.append('<li>%s &mdash; answered <b>%s</b></li>' %
                            (esc(u.get('en')), esc(u.get('given'))))
            body.append('</ul>')
        body.append('</article>')
    if not rows:
        body.append('<p class="empty">No tests taken yet.</p>')

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Test results</title><style>'
        ':root{color-scheme:light dark;--bg:#EEF1F5;--fg:#141A22;--dim:#5A6878;'
        '--line:#D6DDE6;--card:#fff;--accent:#2B4C8C;--bad:#A8322F}'
        '@media(prefers-color-scheme:dark){:root{--bg:#10141A;--fg:#E6EBF2;'
        '--dim:#9AAABE;--line:#252E3A;--card:#181E27;--accent:#7FA8E8;--bad:#EF8A86}}'
        'body{margin:0;background:var(--bg);color:var(--fg);padding:26px 18px 60px;'
        'font:16px/1.5 system-ui,sans-serif}'
        'main{max-width:640px;margin:0 auto}'
        'h1{font-size:1.5rem;margin:0 0 20px}'
        'article{background:var(--card);border:1px solid var(--line);border-radius:7px;'
        'padding:16px 18px;margin-bottom:12px}'
        'header{display:flex;justify-content:space-between;gap:12px;align-items:baseline}'
        'header span{color:var(--dim);font-size:.82rem}'
        '.score{font-size:1.5rem;margin:6px 0 0;color:var(--accent)}'
        '.score i{font-size:1rem;font-style:normal;color:var(--dim)}'
        '.m{color:var(--dim);font-size:.9rem;margin:8px 0 0}'
        '.u{color:var(--bad);font-size:.9rem;margin:10px 0 4px}'
        'ul{margin:0;padding-left:20px;font-size:.9rem;color:var(--dim)}'
        '.empty{color:var(--dim)}'
        '</style></head><body><main><h1>Test results</h1>' +
        ''.join(body) + '</main></body></html>')


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ip = lan_ip()
    print('')
    print('  Red Tide test server')
    print('  ' + '-' * 46)
    print('  words loaded    %d' % len(WORDS))
    print('  marking         %s' % (
        'Claude (%s)' % MODEL if API_KEY else
        'dictionary only - no API key, unclear answers go to /results'))
    print('')
    print('  On the phone open:   http://%s:%d' % (ip, PORT))
    print('  Results for you:     http://%s:%d/results' % (ip, PORT))
    print('')
    print('  Both devices must be on the same Wi-Fi. Ctrl+C to stop.')
    print('')
    try:
        with Server(('0.0.0.0', PORT), Handler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n  stopped\n')
    except OSError as e:
        print('\n  could not start on port %d: %s' % (PORT, e))
        print('  another copy may already be running, or try EXAM_PORT=8790\n')
        sys.exit(1)


if __name__ == '__main__':
    main()
