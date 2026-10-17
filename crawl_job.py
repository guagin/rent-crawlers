"""Isolated crawler entry point: JSON input, explicit success/empty/error output."""
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, urljoin
from bs4 import BeautifulSoup
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
from rental_details import DetailFetcher

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'app'))
from libs.utils import use_selenium

SELECTORS={'591':'.list-wrapper .item','sinyi':'#buy_container .ddhouse','rakuya':'section.grid-item.search-obj','ddroom':'div.md\\:flex.md\\:py-6'}
EMPTY=('這些條件目前沒有符合的物件','沒有符合條件的物件','查無符合','找不到符合','目前無符合','沒有找到符合','查無物件')
CHALLENGE=('verify you are human','just a moment','驗證您是真人','人機驗證','安全驗證','checking your browser')

def text(node): return node.get_text(' ',strip=True) if node else ''
def pick(card,selector): return text(card.select_one(selector))

def parse(source,soup,url):
    rows=[]
    base=soup.select_one('base[href]')
    base_url=urljoin(url,base['href']) if base else url
    for card in soup.select(SELECTORS[source]):
        if source=='ddroom':
            anchor=card.select_one('h3 a[href*="/object/"]')
            metadata=pick(card,'div.mt-2.text-gray-500')
            area=re.search(r'[\d.]+\s*坪',metadata)
            row=dict(price=pick(card,'span.text-xl.font-semibold'),address=pick(card,'.flex.items-center.text-gray-500'),area=area.group() if area else '',floor=metadata,metadata=metadata)
            row['tags']=[text(x) for x in card.select('span.bg-gray-100')]
        elif source=='rakuya':
            anchor=card.select_one('a[href]')
            row=dict(title=pick(card,'.card__head h2'),price=pick(card,'.info__price--total'),address=' '.join(text(x) for x in card.select('.info__geo--area,.info__geo--road,.info__geo--community')),area=pick(card,'.info__space--main'),floor=pick(card,'.info__floor'),metadata=pick(card,'.info__detail-info'))
            row['tags']=[text(x) for x in card.select('span,li') if len(text(x))<12]
        elif source=='sinyi':
            anchor=card.select_one('a[href]'); lines=card.select('.detail_line2')
            metadata=' '.join(text(x) for x in lines)
            area=re.search(r'[\d.]+\s*坪',metadata)
            row=dict(title=pick(card,'.item_title'),price=pick(card,'.price_new') or pick(card,'.detail_price'),address=text(lines[1]) if len(lines)>1 else '',area=area.group() if area else '',floor=text(lines[0]) if lines else '',metadata=metadata)
            row['tags']=[text(x) for x in card.select('span,li,.detail_tag') if len(text(x))<12]
        else:
            anchor=card.select_one('a.link'); lines=card.select('.item-info-txt')
            metadata=text(lines[0]) if lines else ''
            area=re.search(r'[\d.]+\s*坪',metadata)
            row=dict(price=pick(card,'.item-info-price'),address=text(lines[1]) if len(lines)>1 else '',area=area.group() if area else '',floor=metadata,metadata=metadata)
            row['tags']=[text(x) for x in card.select('span') if len(text(x))<12]
        if not anchor: continue
        row.setdefault('title',text(anchor)); row['link']=urljoin(base_url,anchor.get('href',''))
        row['images']=[]
        for img in card.select('img'):
            src=img.get('data-src') or img.get('src','')
            if src.startswith('http') and not src.endswith('.svg'): row['images'].append(src)
        rows.append(row)
    return rows

def page_url(source,url,page):
    if source=='sinyi': return re.sub(r'/\d+\.html$',f'/{page}.html',url)
    parts=urlsplit(url); q=[(k,v) for k,v in parse_qsl(parts.query) if k!='page']; q.append(('page',str(page)))
    return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(q),''))

def confirmed_end(source,soup,count,page,page_size):
    if source=='sinyi':
        total=re.search(r'共有\s*(\d+)\s*間房屋',text(soup.select_one('#buy_container')))
        return bool(total and count>=int(total.group(1)))
    if source=='rakuya':
        total=re.search(r'共\s*(\d+)\s*頁',text(soup))
        return bool(total and page>=int(total.group(1)))
    return source=='ddroom' and page_size<20

def run(job,output):
    source=job['source']; url=job['plan']['url']; limit=job['filters']['max_pages']
    driver=use_selenium(); driver.implicitly_wait(0); driver.set_page_load_timeout(45)
    rows=[]; seen=set(); complete=False
    try:
        for page in range(1,limit+1):
            print(f'第 {page}/{limit} 頁：載入 {source}',flush=True)
            driver.get(page_url(source,url,page))
            challenge_seen=False
            def ready(browser):
                nonlocal challenge_seen
                soup=BeautifulSoup(browser.page_source,'html.parser')
                body=text(soup).lower()
                if any(x in body for x in CHALLENGE):
                    challenge_seen=True
                    return False
                return soup if soup.select(SELECTORS[source]) or any(x in body for x in EMPTY) or source=='591' and soup.select_one('.empty') else False
            try: soup=WebDriverWait(driver,25,poll_frequency=1).until(ready)
            except Exception as error:
                (output.parent/'diagnostic.html').write_text(driver.page_source,encoding='utf-8')
                if isinstance(error,TimeoutException) and challenge_seen: raise RuntimeError('NEEDS_ATTENTION: 平台驗證未完成，請在本機處理後重試') from error
                if isinstance(error,TimeoutException): raise RuntimeError('無法辨識房源或零筆訊息；保留資料庫原資料') from error
                raise
            if page==1: (output.parent/'first-page.html').write_text(str(soup),encoding='utf-8')
            page_rows=parse(source,soup,url)
            if not page_rows:
                if soup.select(SELECTORS[source]): raise RuntimeError('頁面有房源但解析失敗，請更新擷取規則')
                complete=True; print('網站確認沒有更多符合房源。',flush=True); break
            new=[x for x in page_rows if x['link'] not in seen]
            if not new: raise RuntimeError('網站重複回傳同一頁，未完成分頁；保留原資料')
            rows.extend(new); seen.update(x['link'] for x in new)
            print(f'讀取 {len(page_rows)} 筆，本次累計 {len(rows)} 筆；完成後依條件複核。',flush=True)
            if confirmed_end(source,soup,len(rows),page,len(page_rows)):
                complete=True; break
            if page<limit: time.sleep(2)
        # Bound detail work so a large search cannot overrun the worker watchdog.
        # Unread details remain unknown; a failed fetch never becomes permission.
        fetcher=DetailFetcher();deadline=time.monotonic()+600;pending=0
        try:
            for index,row in enumerate(rows):
                if time.monotonic()>deadline:
                    pending=len(rows)-index;break
                row['detail']=fetcher.fetch(source,row)
                print(f'詳細頁 {index+1}/{len(rows)}：{row["detail"]["status"]}',flush=True)
                if source not in fetcher.blocked:time.sleep(0.75)
        finally:fetcher.close()
        result={'rows':rows,'complete':complete,'limited':not complete,'pages':page,'details_pending':pending}
        output.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    finally: driver.quit()

if __name__=='__main__':
    try: run(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')),Path(sys.argv[2]))
    except Exception as error:
        print(str(error),flush=True)
        sys.exit(3 if 'NEEDS_ATTENTION:' in str(error) else 1)
