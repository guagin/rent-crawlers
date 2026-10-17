"""Resumable four-source recheck of stored listings; preserves annotations/history."""
import json,time,threading,argparse,os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from rental_store import Store,dump
from rental_details import DetailFetcher
from pet_policy import row_policy
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parent
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',choices=['591','sinyi','rakuya','ddroom'])
    parser.add_argument('--retry-failed',action='store_true')
    parser.add_argument('--retry-blocked',action='store_true',help='Only after manually resolving the platform verification')
    args=parser.parse_args()
    load_dotenv(ROOT/'.env')
    store=Store(os.getenv('DASHBOARD_DB',str(ROOT/'data/rentals.sqlite3')))
    backup=store.path.with_name('before-pet-details.sqlite3')
    if not backup.exists():store.backup(backup)
    with store.connect() as db:
        clauses=['d.listing_id IS NULL']
        if args.retry_failed:clauses.append("json_extract(d.data,'$.status')='failed'")
        if args.retry_blocked:clauses.append("json_extract(d.data,'$.status')='blocked'")
        sql='SELECT l.id,l.source,l.data FROM listings l LEFT JOIN listing_details d ON d.listing_id=l.id WHERE ('+' OR '.join(clauses)+')'
        params=[]
        if args.source:sql+=' AND l.source=?';params.append(args.source)
        rows=[dict(r) for r in db.execute(sql+' ORDER BY l.id',params)]
    progress={};lock=threading.Lock()
    def source_run(source):
        selected=[r for r in rows if r['source']==source];fetcher=DetailFetcher()
        counts=dict(total=len(selected),done=0,ok=0,failed=0,blocked=0,denied=0)
        rate_lock=threading.Lock();next_request=[0.0]
        def one(r):
                with rate_lock:
                    pause=max(0,next_request[0]-time.monotonic())
                    if pause:time.sleep(pause)
                    next_request[0]=time.monotonic()+1.0
                row=json.loads(r['data']);detail=fetcher.fetch(source,row)
                store.save_detail(r['id'],detail)
                with lock:
                    counts['done']+=1;counts[detail['status']]+=1
                    if row_policy({**row,'detail':detail})['any']=='denied':counts['denied']+=1
                    progress[source]=dict(counts)
                    store.path.with_name('detail-progress.json').write_text(dump(progress),encoding='utf-8')
                    if counts['done']%20==0 or counts['done']==counts['total']:print(source,dump(counts),flush=True)
        try:
            if source=='591':
                with ThreadPoolExecutor(max_workers=2) as workers:list(workers.map(one,selected))
            else:
                for r in selected:one(r)
        finally:fetcher.close()
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(source_run,[args.source] if args.source else ['591','sinyi','rakuya','ddroom']))
    print('DONE',dump(progress),flush=True)
if __name__=='__main__':main()
