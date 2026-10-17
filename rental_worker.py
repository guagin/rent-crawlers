import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from rental_store import dump

class Worker:
    def __init__(self,store,root):
        self.store=store; self.root=Path(root); self.event=threading.Event(); self.stopping=threading.Event()
        self.process=None
        self.store.recover()
        self.thread=threading.Thread(target=self.loop,daemon=True)
    def start(self): self.thread.start()
    def wake(self): self.event.set()
    def stop(self):
        self.stopping.set(); self.wake()
        process=self.process
        if process and process.poll() is None:
            if os.name=='nt': subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
            else: process.terminate()
        self.thread.join(timeout=10)
    def loop(self):
        while not self.stopping.is_set():
            job=self.store.claim()
            if not job:
                self.event.wait(2); self.event.clear(); continue
            self.execute(job)
    def execute(self,job):
        folder=self.store.path.parent/'jobs'/str(job['id']); folder.mkdir(parents=True,exist_ok=True)
        input_file=folder/'input.json'; output_file=folder/'result.json'
        input_file.write_text(dump(job),encoding='utf-8')
        timer=None; process=None; expired=threading.Event()
        try:
            env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUNBUFFERED':'1'}
            # Database password is not needed by the scraping subprocess.
            env.pop('DASHBOARD_PASSWORD',None)
            process=subprocess.Popen([sys.executable,str(self.root/'crawl_job.py'),str(input_file),str(output_file)],cwd=self.root,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',stdin=subprocess.DEVNULL)
            self.process=process
            def timeout():
                expired.set()
                if os.name=='nt': subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
                else: process.kill()
            timer=threading.Timer(900,timeout); timer.daemon=True; timer.start()
            for line in process.stdout: self.store.log(job['id'],line.rstrip())
            code=process.wait()
            if expired.is_set(): raise RuntimeError('執行超過 15 分鐘，已停止；可減少頁數重試')
            if code==3:
                self.store.finish(job['id'],'needs_attention','平台要求人工驗證；未匯入本次結果。'); return
            if code!=0: raise RuntimeError('爬蟲失敗，詳見執行紀錄；原房源已保留')
            payload=json.loads(output_file.read_text(encoding='utf-8'))
            if not isinstance(payload.get('rows'),list): raise ValueError('爬蟲輸出格式錯誤')
            saved=self.store.ingest(job['source'],payload['rows'],job['id'],job['filters'])
            state='completed_limited' if payload.get('limited') else 'completed'
            self.store.log(job['id'],f"完成：發現 {len(payload['rows'])} 筆；{saved} 筆符合條件（含允許的待確認資料）。"+(' 已達頁數上限。' if payload.get('limited') else ''))
            self.store.finish(job['id'],state,found=len(payload['rows']),saved=saved)
        except Exception as error:
            self.store.finish(job['id'],'interrupted' if self.stopping.is_set() else 'failed',str(error))
        finally:
            if timer: timer.cancel()
            self.process=None
