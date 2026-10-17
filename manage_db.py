"""Offline backup / restore. Stop the dashboard before restore."""
import argparse
from pathlib import Path
import sqlite3
from rental_store import Store

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['backup','restore']); p.add_argument('file',type=Path)
    p.add_argument('--database',type=Path,default=Path(__file__).parent/'data'/'rentals.sqlite3')
    args=p.parse_args()
    if args.action=='backup':
        if args.file.exists(): p.error('備份檔已存在，請使用新檔名')
        Store(args.database).backup(args.file)
    else:
        if not args.file.is_file(): p.error('找不到備份')
        if args.file.resolve()==args.database.resolve(): p.error('備份與目標不能相同')
        source=sqlite3.connect(f'file:{args.file.resolve().as_posix()}?mode=ro',uri=True)
        try:
            if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok': p.error('備份損壞')
            if not source.execute("SELECT 1 FROM sqlite_master WHERE name='listings'").fetchone(): p.error('不是房源資料庫')
            if args.database.exists():
                import time
                Store(args.database).backup(str(args.database)+f'.before-restore-{time.time_ns()}.bak')
            args.database.parent.mkdir(parents=True,exist_ok=True)
            target=sqlite3.connect(args.database)
            try: source.backup(target)
            finally: target.close()
        finally: source.close()
    print('完成',args.action,args.file)
