"""Publish the dashboard only after checking its password protection."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
host = os.getenv('DASHBOARD_HOST', '127.0.0.1')
password = os.getenv('DASHBOARD_PASSWORD', '')
if len(password) < 20:
    sys.exit('Set DASHBOARD_PASSWORD in .env to a random password of at least 20 characters.')
cloudflared = shutil.which('cloudflared')
for base in ('ProgramFiles(x86)', 'ProgramFiles'):
    candidate = Path(os.getenv(base, '')) / 'cloudflared' / 'cloudflared.exe'
    if candidate.is_file():
        cloudflared = str(candidate)
if not cloudflared:
    sys.exit('Install cloudflared first.')
origin = f'http://{host}:8765'
client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
auth = 'Basic ' + base64.b64encode(f'rent:{password}'.encode()).decode()

def check():
    try:
        with client.open(origin + '/api/status', timeout=3):
            raise RuntimeError('Existing dashboard has no password protection. Restart it before opening a tunnel.')
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise
    request = urllib.request.Request(origin + '/api/status', headers={'Authorization': auth})
    with client.open(request, timeout=3) as response:
        if 'runs' not in json.load(response):
            raise RuntimeError('Unexpected dashboard response.')

try:
    check()
except urllib.error.HTTPError:
    sys.exit('Dashboard password does not match .env. Restart the dashboard.')
except (urllib.error.URLError, TimeoutError):
    env = {**os.environ, 'DASHBOARD_OPEN_BROWSER': '0', 'PYTHONIOENCODING': 'utf-8'}
    subprocess.Popen([sys.executable, str(ROOT / 'run_dashboard.py')], cwd=ROOT,
                     env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    for attempt in range(15):
        try:
            check()
            break
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.5)
    else:
        sys.exit('Dashboard did not become ready; no tunnel was opened.')

print('Password protection checked. Login username: rent. Password: DASHBOARD_PASSWORD in .env.', flush=True)
subprocess.run([cloudflared, 'tunnel', '--url', origin], check=True)
