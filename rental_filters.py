"""Validated common filters, source adapters and conservative result matching."""
import math
import re
from pet_policy import pet_match
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit, parse_qsl

SOURCES = {'591': '591', 'sinyi': '信義房屋', 'rakuya': '樂屋網', 'ddroom': 'DDROOM'}
ZIPS = {'新莊區': '242', '板橋區': '220', '樹林區': '238'}
SECTIONS = {'新莊區': '44', '板橋區': '26', '樹林區': '41'}
BASES = {'591': 'https://rent.591.com.tw', 'sinyi': 'https://www.sinyi.com.tw', 'rakuya': 'https://rent.rakuya.com.tw', 'ddroom': 'https://www.dd-room.com'}
DEFAULT = dict(city='新北市', districts=list(ZIPS), min_price=10000, max_price=30000, min_area=10,
               max_area=None, min_rooms=2, max_rooms=2, kind='whole', pet='any', include_unknown=True,
               elevator=False, ac=False, exclude_rooftop=False, max_pages=20)

def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError('條件必須是物件')
    out = {}
    out['city'] = str(raw.get('city', '')).strip().replace('台', '臺')
    if len(out['city']) > 20:
        raise ValueError('縣市名稱過長')
    districts = raw.get('districts', [])
    if not isinstance(districts, list) or len(districts) > 50 or any(not isinstance(x, str) or len(x)>20 for x in districts):
        raise ValueError('行政區格式錯誤')
    out['districts'] = list(dict.fromkeys(x.strip() for x in districts if x.strip()))
    for key in ('min_price','max_price','min_area','max_area','min_rooms','max_rooms'):
        value = raw.get(key)
        if value in (None, ''):
            out[key] = None
            continue
        if isinstance(value, bool):
            raise ValueError('數字格式錯誤')
        value = float(value)
        if not math.isfinite(value) or value < 0 or value > 10000000:
            raise ValueError('條件數值超出範圍')
        if 'rooms' in key and (value != int(value) or value > 100):
            raise ValueError('房數需為 0–100 的整數')
        out[key] = value
    for field in ('price','area','rooms'):
        lo, hi = out['min_'+field], out['max_'+field]
        if lo is not None and hi is not None and lo > hi:
            raise ValueError('下限不能大於上限')
    for key, values in [('kind', ('', 'whole','studio','share_studio','room')), ('pet', ('', 'any','cat','dog','other'))]:
        out[key] = raw.get(key, '')
        if out[key] not in values:
            raise ValueError('不支援的房型或寵物條件')
    for key in ('include_unknown','ac','elevator','exclude_rooftop'):
        value = raw.get(key, key=='include_unknown')
        if not isinstance(value, bool):
            raise ValueError('勾選條件格式錯誤')
        out[key] = value
    pages = raw.get('max_pages', 20)
    if isinstance(pages, bool) or float(pages) != int(pages) or not 1 <= int(pages) <= 50:
        raise ValueError('每平台頁數需為 1–50')
    out['max_pages'] = int(pages)
    return out

def adapter(source, f):
    if source not in SOURCES:
        raise ValueError('未知平台')
    native = []
    city, districts = f['city'], f['districts']
    warnings = ['寵物、設備及未提供欄位由抓取結果複核；未知值依「納入待確認」設定處理。']
    if source != 'ddroom' and city not in ('', '新北市'):
        raise ValueError(f'{SOURCES[source]} 的縣市轉換目前支援新北市或不限；其他縣市請選 DDROOM。')
    zipcodes = [ZIPS[x] for x in districts if x in ZIPS]
    mapped = city == '新北市' and districts and len(zipcodes) == len(districts)
    if districts and not mapped and source != 'ddroom':
        warnings.append('此行政區尚無網站代碼：會抓取較廣範圍後複核，頁數上限可能造成遺漏。')
    if source == 'ddroom':
        q = dict(category='house', order='recommend', sort='desc', page=1)
        for key,value in [('city',city),('area',','.join(districts)),('space',f['kind']),('min_rent',f['min_price']),('max_rent',f['max_price'])]:
            if value not in ('',None): q[key] = value
        if f['min_rooms'] is not None and f['min_rooms']==f['max_rooms']:
            q['bedroom'] = int(f['min_rooms'])
        native = ['縣市','行政區','租金','房型','指定房數']
        url = BASES[source]+'/search?'+urlencode(q)
    elif source == '591':
        q = {}
        if city: q['region'] = '3'; native.append('縣市')
        if mapped: q['section'] = ','.join(SECTIONS[x] for x in districts); native.append('行政區')
        if f['kind']: q['kind'] = {'whole':'1','studio':'2','share_studio':'3','room':'4'}[f['kind']]; native.append('房型')
        # Exact rent/area/room ranges are checked locally, avoiding undocumented URL encodings.
        url = BASES[source]+'/list?'+urlencode(q)
    elif source == 'rakuya':
        q = {}
        if mapped: q['zipcode'] = ','.join(zipcodes); native.append('行政區')
        if f['kind']=='whole': q['usecode']='7'; native.append('整層')
        if f['min_price'] is not None and f['max_price'] is not None:
            q['price']=f"{f['min_price']:g}~{f['max_price']:g}"; native.append('租金')
        if f['min_rooms'] is not None and f['min_rooms']==f['max_rooms']:
            q['room']=int(f['min_rooms']); native.append('指定房數')
        url=BASES[source]+'/result?'+urlencode(q)
    else:
        parts = []
        if city: parts.append('NewTaipei-city'); native.append('縣市')
        if mapped: parts.append('-'.join(zipcodes)+'-zip'); native.append('行政區')
        if f['min_price'] is not None and f['max_price'] is not None:
            parts.append(f"{f['min_price']:g}-{f['max_price']:g}-price"); native.append('租金')
        if f['min_area'] is not None: parts.append(f"{f['min_area']:g}-up-area"); native.append('最低坪數')
        if f['kind']=='whole': parts.append('house-use'); native.append('整層')
        url=BASES[source]+'/rent/list/'+('/'.join(parts)+'/' if parts else '')+'1.html'
    warnings.append('未列為網站篩選的條件在本機複核；每次只抓設定頁數，不代表全站完整搜尋。')
    return dict(source=source, label=SOURCES[source], url=url, native=native, warnings=warnings)

def number(value):
    m=re.search(r'[\d]+(?:\.\d+)?',str(value or '').replace(',',''))
    return float(m.group()) if m else None

def source_identity(source, link):
    parts=urlsplit(link)
    patterns={'591':r'/(\d+)(?:\.html)?/?$', 'ddroom':r'/object/([^/]+)',
              'sinyi':r'/houseno/([^/]+)', 'rakuya':r'/(?:item|rent)/([^/]+)'}
    found=re.search(patterns[source],parts.path)
    if found: return found.group(1)
    query=[(k,v) for k,v in parse_qsl(parts.query) if not k.startswith('utm_') and k not in ('from','fbclid','gclid')]
    return urlunsplit(('https',parts.hostname,parts.path,urlencode(sorted(query)),''))

def normalize(source, row):
    row=dict(row)
    link=urljoin(BASES[source],str(row.get('link','')))
    parts=urlsplit(link)
    domain={'591':'591.com.tw','rakuya':'rakuya.com.tw','sinyi':'sinyi.com.tw','ddroom':'dd-room.com'}[source]
    if parts.scheme not in ('http','https') or not parts.hostname or not (parts.hostname==domain or parts.hostname.endswith('.'+domain)):
        raise ValueError('房源連結來源錯誤')
    row['link']=urlunsplit((parts.scheme,parts.netloc,parts.path,parts.query,''))
    row['source']=source
    row['title']=str(row.get('title',''))
    row['address']=str(row.get('address','')).replace('台','臺')
    row['rent']=number(row.get('price'))
    if row['rent'] is not None and '萬' in str(row.get('price','')): row['rent']*=10000
    row['ping']=number(row.get('area'))
    text=str(row.get('metadata',row.get('floor','')))
    m=re.search(r'(\d+)\s*房',text)
    row['rooms']=int(m.group(1)) if m else None
    row['kind']=next((v for k,v in [('整層','whole'),('獨立套房','studio'),('分租套房','share_studio'),('雅房','room')] if k in text),None)
    tags=row.get('tags',[])
    pets=row.get('pets',[])
    if not isinstance(tags,list): tags=[]
    if not isinstance(pets,list): pets=[]
    row['pets']=list(dict.fromkeys(pets+[t for t in tags if t in ('可養貓','可養狗','可養其他寵物','可養寵物')]))
    row['pet_denied']=any(t in ('不可養寵物','禁止養寵物','不可養貓狗') for t in tags)
    row['elevator']=True if '有電梯' in tags else False if '無電梯' in tags else None
    row['ac']=True if any(t in tags for t in ('冷氣','有冷氣')) else None
    row['rooftop']=True if '頂樓加蓋' in text else None
    row['images']=[x for x in row.get('images',[]) if isinstance(x,str) and x.startswith(('https://','http://'))] if isinstance(row.get('images'),list) else []
    return row

def matches(row, f):
    unknown=[]
    for field,lo,hi,label in [('rent','min_price','max_price','租金'),('ping','min_area','max_area','坪數'),('rooms','min_rooms','max_rooms','房數')]:
        val=row.get(field)
        if f.get(lo) is None and f.get(hi) is None: continue
        if val is None: unknown.append(label); continue
        if f.get(lo) is not None and val < f[lo] or f.get(hi) is not None and val > f[hi]: return False,[]
    address=row.get('address','')
    if f.get('city'):
        city=re.match(r'^(.{2,3}[市縣])',address)
        if city and city.group(1)!=f['city']: return False,[]
        if not city: unknown.append('縣市')
    if f.get('districts'):
        if not any(x in address for x in f['districts']):
            if re.search(r'[區鄉鎮]',address): return False,[]
            unknown.append('行政區')
    if f.get('kind'):
        if row.get('kind') and row['kind']!=f['kind']: return False,[]
        if not row.get('kind'): unknown.append('房型')
    pet=f.get('pet')
    if pet:
        decision,_=pet_match(row,pet)
        if decision=='denied': return False,[]
        if decision=='unknown': unknown.append('寵物')
    for field,label in [('elevator','電梯'),('ac','冷氣')]:
        if f.get(field):
            if row.get(field) is False: return False,[]
            if row.get(field) is None: unknown.append(label)
    if f.get('exclude_rooftop'):
        if row.get('rooftop') is True: return False,[]
        if row.get('rooftop') is None: unknown.append('是否頂加')
    return not unknown or f.get('include_unknown',True), unknown
