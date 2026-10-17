"""Fetch only the listing's own description and rental rules, never recommendations."""
from datetime import datetime,timezone
import re,time,urllib.request,urllib.error
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from rental_filters import normalize,source_identity

def extract(source,html):
    soup=BeautifulSoup(html,'html.parser')
    for n in soup.select('script,style,noscript,nav,footer,form'):n.decompose()
    if source=='591': nodes=soup.select('.house-condition-content,.service .desc-item')
    elif source=='sinyi': nodes=soup.select('.features-description,.information-content li,.information-content-1 li')
    elif source=='ddroom':nodes=soup.select('article.e-object-content')
    else:
        for n in soup.select('.block__push,.block__remark,.tab__content'):n.decompose()
        if not soup.select_one('.main-content .list__label'):raise ValueError('房源租屋規則尚未載入')
        nodes=soup.select('.main-content')
    if not nodes:raise ValueError('找不到房源詳細內文，可能已下架或頁面改版')
    parts=list(dict.fromkeys(n.get_text(' ',strip=True) for n in nodes if n.get_text(strip=True)))
    if not parts:raise ValueError('詳細內文尚未載入')
    return '\n'.join(parts)[:100000]

class DetailFetcher:
    def __init__(self):self.driver=None;self.blocked=set()
    def close(self):
        if self.driver:self.driver.quit();self.driver=None
    def fetch(self,source,row):
        row=normalize(source,row);url=row['link']
        if source=='sinyi':url='https://www.sinyi.com.tw/rent/houseno/'+source_identity(source,url)
        stamp=datetime.now(timezone.utc).isoformat()
        result=dict(url=url,checked_at=stamp,status='failed',text='')
        if source in self.blocked:return {**result,'status':'blocked','error':'此平台本輪已遇驗證，停止後續請求'}
        try:
            if source=='rakuya':
                if self.driver is None:
                    from app.libs.utils import use_selenium
                    self.driver=use_selenium();self.driver.implicitly_wait(0);self.driver.set_page_load_timeout(30)
                self.driver.get(url)
                for attempt in range(12):
                    html=self.driver.page_source
                    if not any(x in self.driver.title.lower() for x in ('請稍候','just a moment')):
                        try:body=extract(source,html);break
                        except ValueError:pass
                    time.sleep(1)
                else:
                    if any(x in self.driver.title.lower() for x in ('請稍候','just a moment')):
                        self.blocked.add(source);return {**result,'status':'blocked','error':'平台要求人工驗證'}
                    raise ValueError('詳細頁未載入')
            else:
                req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
                with urllib.request.urlopen(req,timeout=20) as response:html=response.read(4000000).decode('utf-8','replace')
                body=extract(source,html)
            return {**result,'status':'ok','text':body}
        except urllib.error.HTTPError as error:
            if error.code in (403,429):self.blocked.add(source)
            return {**result,'status':'blocked' if error.code in (403,429) else 'failed','error':f'HTTP {error.code}'}
        except Exception as error:return {**result,'error':str(error)[:300]}
