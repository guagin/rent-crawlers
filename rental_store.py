"""SQLite repository. Connections and transactions are owned by each operation."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from rental_filters import SOURCES, normalize, matches, validate, source_identity

def now(): return datetime.now(timezone.utc).isoformat()
def dump(value): return json.dumps(value,ensure_ascii=False,sort_keys=True)

class Store:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS search_profiles(id INTEGER PRIMARY KEY,name TEXT NOT NULL,filters TEXT NOT NULL,sources TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS crawl_runs(id INTEGER PRIMARY KEY,filters TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS crawl_run_sources(id INTEGER PRIMARY KEY,run_id INTEGER NOT NULL REFERENCES crawl_runs(id),source TEXT NOT NULL,status TEXT NOT NULL,plan TEXT NOT NULL,started_at TEXT,finished_at TEXT,found INTEGER DEFAULT 0,saved INTEGER DEFAULT 0,log TEXT DEFAULT '',error TEXT DEFAULT '',UNIQUE(run_id,source));
            CREATE TABLE IF NOT EXISTS listings(id INTEGER PRIMARY KEY,source TEXT NOT NULL,source_id TEXT NOT NULL,title TEXT NOT NULL,address TEXT NOT NULL,data TEXT NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(source,source_id));
            CREATE TABLE IF NOT EXISTS listing_history(id INTEGER PRIMARY KEY,listing_id INTEGER NOT NULL REFERENCES listings(id),observed_at TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS crawl_run_listings(job_id INTEGER NOT NULL REFERENCES crawl_run_sources(id),listing_id INTEGER NOT NULL REFERENCES listings(id),unknown TEXT NOT NULL,snapshot TEXT,PRIMARY KEY(job_id,listing_id));
            CREATE TABLE IF NOT EXISTS listing_annotations(listing_id INTEGER PRIMARY KEY REFERENCES listings(id),favorite INTEGER NOT NULL DEFAULT 0,hidden INTEGER NOT NULL DEFAULT 0,contact TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '');
            CREATE INDEX IF NOT EXISTS listings_source ON listings(source,last_seen);
            CREATE INDEX IF NOT EXISTS jobs_status ON crawl_run_sources(status,id);
            CREATE INDEX IF NOT EXISTS history_listing ON listing_history(listing_id,id);
            ''')
            if 'snapshot' not in [r['name'] for r in db.execute('PRAGMA table_info(crawl_run_listings)')]:
                db.execute('ALTER TABLE crawl_run_listings ADD COLUMN snapshot TEXT')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=20)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db: yield db
        finally: db.close()

    def profiles(self):
        with self.connect() as db:
            return [{**dict(r),'filters':json.loads(r['filters']),'sources':json.loads(r['sources'])} for r in db.execute('SELECT * FROM search_profiles ORDER BY updated_at DESC')]

    def save_profile(self,data):
        name=str(data.get('name','')).strip()
        if not name or len(name)>80: raise ValueError('設定名稱需為 1–80 字')
        filters=validate(data.get('filters',{})); sources=data.get('sources',[])
        if not isinstance(sources,list) or not sources or any(s not in SOURCES for s in sources): raise ValueError('請選擇平台')
        with self.connect() as db:
            if data.get('id'):
                ident=int(data['id'])
                if not db.execute('UPDATE search_profiles SET name=?,filters=?,sources=?,updated_at=? WHERE id=?',(name,dump(filters),dump(sources),now(),ident)).rowcount: raise ValueError('設定不存在')
            else:
                ident=db.execute('INSERT INTO search_profiles(name,filters,sources,updated_at) VALUES(?,?,?,?)',(name,dump(filters),dump(sources),now())).lastrowid
        return {'id':ident}

    def enqueue(self,filters,plans):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT count(*) FROM crawl_run_sources WHERE status IN ('queued','running')").fetchone()[0]+len(plans)>40:
                raise ValueError('排隊工作已滿，請等待完成')
            ident=db.execute('INSERT INTO crawl_runs(filters,created_at) VALUES(?,?)',(dump(filters),now())).lastrowid
            for plan in plans:
                db.execute('INSERT INTO crawl_run_sources(run_id,source,status,plan) VALUES(?,?,?,?)',(ident,plan['source'],'queued',dump(plan)))
        return ident

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE crawl_run_sources SET status='interrupted',finished_at=?,error='服務在上次執行中停止；請重新排程' WHERE status='running'",(now(),))

    def claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT j.*,r.filters FROM crawl_run_sources j JOIN crawl_runs r ON r.id=j.run_id WHERE status='queued' ORDER BY j.id LIMIT 1").fetchone()
            if not row: return None
            db.execute("UPDATE crawl_run_sources SET status='running',started_at=? WHERE id=?",(now(),row['id']))
            return {**dict(row),'filters':json.loads(row['filters']),'plan':json.loads(row['plan'])}

    def log(self,job,line):
        with self.connect() as db:
            db.execute('UPDATE crawl_run_sources SET log=substr(log||?, -16000) WHERE id=?',(line+'\n',job))

    def finish(self,job,status,error='',found=0,saved=0):
        with self.connect() as db:
            db.execute('UPDATE crawl_run_sources SET status=?,finished_at=?,error=?,found=?,saved=? WHERE id=?',(status,now(),error[:2000],found,saved,job))

    def runs(self):
        with self.connect() as db:
            result=[]
            for run in db.execute('SELECT * FROM crawl_runs ORDER BY id DESC LIMIT 100'):
                result.append({**dict(run),'filters':json.loads(run['filters']),'jobs':[{**dict(j),'plan':json.loads(j['plan'])} for j in db.execute('SELECT * FROM crawl_run_sources WHERE run_id=? ORDER BY id',(run['id'],))]})
            return result

    def ingest(self,source,rows,job=None,filters=None,observed_at=None):
        saved=0
        with self.connect() as db:
            for raw in rows:
                row=normalize(source,raw)
                accepted,unknown=matches(row,filters) if filters else (True,[])
                # Store discoveries even if they miss this run's conditions; only matches link to the run.
                identity=source_identity(source,row['link'])
                timestamp=observed_at or now(); encoded=dump(row)
                old=db.execute('SELECT * FROM listings WHERE source=? AND source_id=?',(source,identity)).fetchone()
                if old:
                    ident=old['id']
                    if old['data']!=encoded:
                        db.execute('INSERT INTO listing_history(listing_id,observed_at,data) VALUES(?,?,?)',(ident,timestamp,encoded))
                    db.execute('UPDATE listings SET title=?,address=?,data=?,last_seen=?,updated_at=? WHERE id=?',(row['title'],row['address'],encoded,timestamp,timestamp if old['data']!=encoded else old['updated_at'],ident))
                else:
                    ident=db.execute('INSERT INTO listings(source,source_id,title,address,data,first_seen,last_seen,updated_at) VALUES(?,?,?,?,?,?,?,?)',(source,identity,row['title'],row['address'],encoded,timestamp,timestamp,timestamp)).lastrowid
                    db.execute('INSERT INTO listing_history(listing_id,observed_at,data) VALUES(?,?,?)',(ident,timestamp,encoded))
                if accepted:
                    saved+=1
                    if job: db.execute('INSERT OR REPLACE INTO crawl_run_listings(job_id,listing_id,unknown,snapshot) VALUES(?,?,?,?)',(job,ident,dump(unknown),encoded))
        return saved

    def import_legacy(self,root):
        files={'591':['591normal.json','591recommend.json'],'sinyi':['sinyi.json'],'rakuya':['rakuya.json'],'ddroom':['ddroom.json']}
        for source,names in files.items():
            for name in names:
                file=Path(root)/name
                if not file.exists(): continue
                key='import:'+name
                with self.connect() as db:
                    if db.execute('SELECT 1 FROM meta WHERE key=?',(key,)).fetchone(): continue
                rows=json.loads(file.read_text(encoding='utf-8'))
                if not isinstance(rows,list): raise ValueError(f'{name} 不是房源列表')
                timestamp=datetime.fromtimestamp(file.stat().st_mtime,timezone.utc).isoformat()
                self.ingest(source,rows,observed_at=timestamp)
                with self.connect() as db: db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',(key,now()))

    def query(self,body):
        filters=validate(body.get('filters',{})); clauses=[]; args=[]
        source=body.get('source','')
        if source:
            if source not in SOURCES: raise ValueError('未知平台')
            clauses.append('l.source=?'); args.append(source)
        q=str(body.get('q',''))[:200]
        if q: clauses.append('(l.title LIKE ? OR l.address LIKE ?)'); args.extend(['%'+q+'%']*2)
        if body.get('run_id'):
            clauses.append('EXISTS(SELECT 1 FROM crawl_run_listings m JOIN crawl_run_sources j ON j.id=m.job_id WHERE m.listing_id=l.id AND j.run_id=?)'); args.append(int(body['run_id']))
        if body.get('favorites'): clauses.append('a.favorite=1')
        if not body.get('show_hidden'): clauses.append('coalesce(a.hidden,0)=0')
        sql='SELECT l.*,coalesce(a.favorite,0) favorite,coalesce(a.hidden,0) hidden,coalesce(a.contact,\'\') contact,coalesce(a.note,\'\') note FROM listings l LEFT JOIN listing_annotations a ON a.listing_id=l.id'
        if clauses: sql+=' WHERE '+' AND '.join(clauses)
        snapshots={}
        with self.connect() as db:
            rows=db.execute(sql,args).fetchall()
            if body.get('run_id'):
                snapshots={r['listing_id']:r for r in db.execute('SELECT m.* FROM crawl_run_listings m JOIN crawl_run_sources j ON j.id=m.job_id WHERE j.run_id=?',(int(body['run_id']),))}
        result=[]
        for raw in rows:
            snapshot=snapshots.get(raw['id'])
            item={**json.loads(snapshot['snapshot'] if snapshot and snapshot['snapshot'] else raw['data']),**{k:raw[k] for k in ('id','first_seen','last_seen','updated_at','favorite','hidden','contact','note')}}
            accepted,unknown=matches(item,filters)
            if snapshot: unknown=list(dict.fromkeys(unknown+json.loads(snapshot['unknown'])))
            if accepted: result.append({**item,'unknown':unknown})
        sort=body.get('sort','last_seen')
        if sort not in ('last_seen','updated_at','rent_asc','rent_desc','area_desc'): raise ValueError('排序不支援')
        if sort in ('last_seen','updated_at'): result.sort(key=lambda x:x[sort],reverse=True)
        else:
            key='ping' if sort=='area_desc' else 'rent'
            result.sort(key=lambda x:(x.get(key) is None,(x.get(key) or 0)*(1 if sort=='rent_asc' else -1)))
        page=max(1,int(body.get('page',1))); size=min(60,max(1,int(body.get('page_size',24))))
        return {'total':len(result),'page':page,'page_size':size,'items':result[(page-1)*size:page*size]}

    def annotate(self,ident,data):
        contact=data.get('contact','')
        if contact not in ('','contacted','viewed'): raise ValueError('聯絡狀態錯誤')
        note=str(data.get('note',''))
        if len(note)>4000: raise ValueError('註記最多 4000 字')
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM listings WHERE id=?',(ident,)).fetchone(): raise ValueError('房源不存在')
            db.execute('INSERT INTO listing_annotations VALUES(?,?,?,?,?) ON CONFLICT(listing_id) DO UPDATE SET favorite=excluded.favorite,hidden=excluded.hidden,contact=excluded.contact,note=excluded.note',(ident,int(bool(data.get('favorite'))),int(bool(data.get('hidden'))),contact,note))

    def detail(self,ident):
        with self.connect() as db:
            row=db.execute('SELECT * FROM listings WHERE id=?',(ident,)).fetchone()
            if not row: raise ValueError('房源不存在')
            history=[{**dict(x),'data':json.loads(x['data'])} for x in db.execute('SELECT * FROM listing_history WHERE listing_id=? ORDER BY id DESC LIMIT 100',(ident,))]
            similar=[dict(x) for x in db.execute('SELECT id,source,title,address FROM listings WHERE id!=? AND source!=? AND title=?',(ident,row['source'],row['title']))]
            return {'history':history,'possible_duplicates':similar}

    def backup(self,destination):
        with self.connect() as db:
            target=sqlite3.connect(destination)
            try: db.backup(target)
            finally: target.close()
