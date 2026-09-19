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
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
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


def find_cli():
    """The Claude Code command line, if it is installed on this machine."""
    named = os.environ.get('EXAM_CLAUDE_CLI', '').strip()
    if named and os.path.exists(named):
        return named
    found = shutil.which('claude')
    if found:
        return found
    appdata = os.environ.get('APPDATA', '')
    for name in ('claude.cmd', 'claude.exe', 'claude'):
        guess = os.path.join(appdata, 'npm', name)
        if appdata and os.path.exists(guess):
            return guess
    return ''


CLAUDE_CLI = find_cli()
NEUTRAL_DIR = tempfile.mkdtemp(prefix='wordbook-judge-')
CLI_NOT_LOGGED_IN = False     # set once, so the console is not spammed


def cli_argv():
    """A .cmd needs cmd.exe to start it; a real executable does not."""
    if os.name == 'nt' and CLAUDE_CLI.lower().endswith(('.cmd', '.bat')):
        return [os.environ.get('COMSPEC', 'cmd.exe'), '/c', CLAUDE_CLI]
    return [CLAUDE_CLI]


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


def judge_prompt(item, given):
    if item.get('dir') == 1:
        direction = ('The learner saw the English word and had to give its meaning '
                     'in Russian or Uzbek.')
    else:
        direction = ('The learner saw both the Russian and the Uzbek translation '
                     'and had to give the English word.')
    return JUDGE_PROMPT.format(direction=direction, en=item['en'], ru=item['ru'],
                               uz=item['uz'], given=given)


BATCH_PROMPT = (
    "You are marking answers in a vocabulary test. The learner is a teenager "
    "learning English; their first languages are Uzbek and Russian.\n\n"
    "Accept an answer when it means the same thing: a synonym not in our "
    "dictionary, another grammatical form, a different but fair wording, a "
    "spelling slip, or Uzbek written in Latin letters. Reject it when it means "
    "something else, when it is only loosely associated, or when it is empty "
    "or nonsense.\n\n"
    "Here are {n} answers to mark:\n\n{rows}\n\n"
    "Reply with only a JSON array of {n} objects, one per number, in order:\n"
    '[{{"n": 1, "ok": true, "note": "..."}}, ...]\n'
    "note is at most eight words in Russian saying why."
)


def batch_rows(items):
    out = []
    for i, it in enumerate(items, 1):
        if it.get('dir') == 1:
            asked = 'shown the English word, had to answer in Russian or Uzbek'
        else:
            asked = ('shown the Russian and the Uzbek translation together, '
                     'had to answer in English')
        out.append(
            '%d. English: %s | Russian: %s | Uzbek: %s\n'
            '   asked: %s\n'
            '   learner answered: %s'
            % (i, it.get('en', ''), it.get('ru', ''), it.get('uz', ''),
               asked, it.get('given', '')))
    return '\n\n'.join(out)


def read_verdict_list(text, n):
    """Pull the JSON array back out, and keep only well-formed entries."""
    m = re.search(r'\[.*\]', text or '', re.S)
    if not m:
        return None
    try:
        got = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(got, list):
        return None
    out = [None] * n
    for row in got:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get('n', 0)) - 1
        except Exception:
            continue
        if 0 <= idx < n and isinstance(row.get('ok'), bool):
            out[idx] = (row['ok'], str(row.get('note') or ''))
    return out


def judge_many(items):
    """One call for every answer the dictionary could not place.

    N separate calls would each pay the command line's ~7 second startup;
    together they are one wait the learner never sees, because this runs
    after the last question.
    """
    if not items:
        return []
    prompt = BATCH_PROMPT.format(n=len(items), rows=batch_rows(items))
    text = ''
    if CLAUDE_CLI and not CLI_NOT_LOGGED_IN:
        text, _ = cli_raw(prompt)
    if not text and API_KEY:
        text, _ = api_raw(prompt)
    if not text:
        return [None] * len(items)
    return read_verdict_list(text, len(items)) or [None] * len(items)


def read_verdict(text):
    """Pull {"ok": ..., "note": ...} out of whatever came back."""
    m = re.search(r'\{.*\}', text or '', re.S)
    if not m:
        return None, 'unparsed'
    try:
        got = json.loads(m.group(0))
    except Exception:
        return None, 'unparsed'
    if not isinstance(got.get('ok'), bool):
        return None, 'unparsed'
    return got['ok'], str(got.get('note') or '')


def cli_raw(prompt):
    """Whatever the Claude Code command line wrote, or ('', reason).

    Uses the subscription, no API key. The prompt goes in on stdin so a long
    multilingual prompt never has to survive Windows shell quoting.
    """
    global CLI_NOT_LOGGED_IN
    if CLI_NOT_LOGGED_IN:
        return '', 'cli_logged_out'
    try:
        p = subprocess.run(cli_argv() + ['--strict-mcp-config', '-p'],
                           input=prompt, cwd=NEUTRAL_DIR,
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=240)
    except subprocess.TimeoutExpired:
        return '', 'cli_timeout'
    except Exception as e:
        print('  [judge] cli: %s' % e)
        return '', 'cli_failed'

    out = (p.stdout or '') + (p.stderr or '')
    if 'Not logged in' in out or '/login' in out:
        CLI_NOT_LOGGED_IN = True
        print('')
        print('  !! The Claude command line is not logged in.')
        print('     Open a terminal, run:  claude')
        print('     then type:  /login   — once, then restart this server.')
        print('     Until then answers the dictionary cannot place are put aside.')
        print('')
        return '', 'cli_logged_out'
    if p.returncode != 0 and not p.stdout:
        return '', 'cli_failed'
    return (p.stdout or ''), ''


def ask_via_cli(prompt):
    text, err = cli_raw(prompt)
    if not text:
        return None, err or 'cli_failed'
    return read_verdict(text)


def api_raw(prompt):
    body = json.dumps({
        'model': MODEL,
        'max_tokens': 4000,
        'messages': [{
            'role': 'user',
            'content': prompt,
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
        return '', 'http_%s' % e.code
    except Exception as e:
        print('  [judge] %s' % e)
        return '', 'unreachable'

    text = ''
    for block in payload.get('content', []):
        if block.get('type') == 'text':
            text += block.get('text', '')
    return text, ''


def ask_via_api(prompt):
    text, err = api_raw(prompt)
    if not text:
        return None, err or 'unreachable'
    return read_verdict(text)


# Identical (word, answer) pairs come round often — ask once.
_verdicts = {}


def ask_claude(item, given):
    """A verdict, or (None, reason) when none could be had.

    The command line goes first: it spends the subscription this machine is
    already paying for. An API key is only used when there is no command
    line. When neither answers, the caller must put the answer aside rather
    than call it wrong.
    """
    cache_key = (item.get('en'), item.get('dir'), given.strip().lower())
    if cache_key in _verdicts:
        return _verdicts[cache_key]

    prompt = judge_prompt(item, given)
    if CLAUDE_CLI and not CLI_NOT_LOGGED_IN:
        ok, note = ask_via_cli(prompt)
        if ok is not None:
            _verdicts[cache_key] = (ok, note)
            return ok, note
        if note != 'cli_logged_out':
            return None, note          # a real failure: do not fall through
    if API_KEY:
        ok, note = ask_via_api(prompt)
        if ok is not None:
            _verdicts[cache_key] = (ok, note)
        return ok, note
    return None, 'nojudge'


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
                                    'claude': bool(CLAUDE_CLI or API_KEY)})

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

        if self.path == '/api/judge-batch':
            items = data.get('items') or []
            if not isinstance(items, list) or not items:
                return self._send(200, {'verdicts': []})
            items = items[:80]
            t0 = time.time()
            got = judge_many(items)
            out = []
            for i, v in enumerate(got):
                if v is None:
                    out.append({'verdict': 'unknown'})
                else:
                    out.append({'verdict': 'ok' if v[0] else 'no', 'note': v[1]})
            done = sum(1 for v in out if v['verdict'] != 'unknown')
            print('  [judge] %d answers in one call, %d decided, %.1fs'
                  % (len(items), done, time.time() - t0))
            return self._send(200, {'verdicts': out})

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
    if CLAUDE_CLI:
        print('  marking         Claude Code  (%s)' % CLAUDE_CLI)
        print('                  no API key needed - it uses your subscription')
    elif API_KEY:
        print('  marking         Anthropic API  (%s)' % MODEL)
    else:
        print('  marking         dictionary only')
        print('                  answers it cannot place are put aside for you,')
        print('                  never marked wrong. See /results.')
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
