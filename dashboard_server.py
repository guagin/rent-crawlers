"""Authenticated dashboard API, SQLite persistence and a durable serial queue."""
import base64
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit
import webbrowser
from dotenv import load_dotenv
from rental_filters import DEFAULT,SOURCES,adapter,validate
from rental_store import Store
from rental_worker import Worker

ROOT=Path(__file__).resolve().parent
load_dotenv(ROOT/'.env')

def make_server(store,host,port,password,worker=None):
    class Handler(BaseHTTPRequestHandler):
        def send(self,status,value,content_type='application/json; charset=utf-8'):
            body=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type',content_type); self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.end_headers(); self.wfile.write(body)
        def authorized(self):
            if not password: return True
            expected='Basic '+base64.b64encode(('rent:'+password).encode()).decode()
            if hmac.compare_digest(self.headers.get('Authorization','').encode(),expected.encode()): return True
            body=b'{"error":"Login required"}'
            self.send_response(401); self.send_header('WWW-Authenticate','Basic realm="Rental dashboard", charset="UTF-8"')
            self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store')
            self.end_headers(); self.wfile.write(body); return False
        def do_GET(self):
            if not self.authorized(): return
            path=urlsplit(self.path).path
            try:
                if path in ('/','/dashboard.html','/dashboard.js','/dashboard.css'):
                    name='dashboard.html' if path=='/' else path[1:]
                    mime={'html':'text/html','js':'text/javascript','css':'text/css'}[name.split('.')[-1]]
                    return self.send(200,(ROOT/name).read_bytes(),mime+'; charset=utf-8')
                if path=='/api/config': return self.send(200,{'sources':SOURCES,'defaults':DEFAULT})
                if path=='/api/profiles': return self.send(200,store.profiles())
                if path=='/api/runs': return self.send(200,store.runs())
                if path=='/api/status':
                    runs=store.runs(); active=next((j['source'] for r in runs for j in r['jobs'] if j['status']=='running'),None)
                    return self.send(200,{'active_source':active,'runs':{},'queue':sum(j['status']=='queued' for r in runs for j in r['jobs'])})
                if path.startswith('/api/listings/') and path.rsplit('/',1)[1].isdigit():
                    return self.send(200,store.detail(int(path.rsplit('/',1)[1])))
                self.send(404,{'error':'找不到此頁面'})
            except ValueError as error: self.send(400,{'error':str(error)})
            except Exception: self.send(500,{'error':'讀取失敗，請稍後再試'})
        def do_POST(self):
            if not self.authorized(): return
            origin=urlsplit(self.headers.get('Origin',''))
            if origin.scheme not in ('http','https') or origin.netloc.lower()!=self.headers.get('Host','').lower():
                return self.send(403,{'error':'僅允許同網站操作'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<1 or length>65536: raise ValueError('請求大小錯誤')
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict): raise ValueError('請求格式錯誤')
                path=urlsplit(self.path).path
                if path=='/api/query': return self.send(200,store.query(data))
                if path=='/api/profiles': return self.send(200,store.save_profile(data))
                if path.startswith('/api/annotations/'):
                    store.annotate(int(path.rsplit('/',1)[1]),data); return self.send(200,{'ok':True})
                if path in ('/api/plan','/api/runs'):
                    filters=validate(data.get('filters',{})); selected=data.get('sources',[])
                    if not isinstance(selected,list) or not selected or len(selected)>4 or any(s not in SOURCES for s in selected): raise ValueError('請選擇有效平台')
                    plans=[adapter(s,filters) for s in dict.fromkeys(selected)]
                    if path=='/api/plan': return self.send(200,{'filters':filters,'plans':plans})
                    ident=store.enqueue(filters,plans)
                    if self.server.worker: self.server.worker.wake()
                    return self.send(202,{'id':ident})
                self.send(404,{'error':'找不到此操作'})
            except (ValueError,TypeError,KeyError,OverflowError) as error: self.send(400,{'error':str(error)})
            except Exception: self.send(500,{'error':'儲存或執行失敗，請稍後再試'})
        def log_message(self,*args): pass
    server=ThreadingHTTPServer((host,port),Handler)
    server.worker=worker
    return server

def main():
    host=os.getenv('DASHBOARD_HOST','127.0.0.1'); password=os.getenv('DASHBOARD_PASSWORD','')
    if host not in ('127.0.0.1','localhost','::1') and len(password)<20:
        raise RuntimeError('外網服務需要至少 20 字元的 DASHBOARD_PASSWORD')
    store=Store(os.getenv('DASHBOARD_DB',str(ROOT/'data'/'rentals.sqlite3')))
    # Bind before queue recovery: a duplicate launcher must not interrupt active jobs.
    server=make_server(store,host,int(os.getenv('DASHBOARD_PORT','8765')),password)
    store.import_legacy(ROOT/'static')
    worker=Worker(store,ROOT)
    server.worker=worker
    worker.start()
    print(f'租屋看板：http://{host}:{server.server_port}/',flush=True)
    if os.getenv('DASHBOARD_OPEN_BROWSER','1')=='1': threading.Thread(target=webbrowser.open,args=(f'http://{host}:{server.server_port}/',),daemon=True).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: worker.stop(); server.server_close()

if __name__=='__main__': main()
