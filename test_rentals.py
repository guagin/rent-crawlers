import tempfile
import unittest
from pathlib import Path
from rental_filters import DEFAULT, validate, normalize, matches, adapter
from rental_store import Store
from bs4 import BeautifulSoup
from crawl_job import parse,confirmed_end

class RentalTests(unittest.TestCase):
    def setUp(self):
        directory=Path(__file__).parent/'data'/'tests'
        directory.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'test.sqlite3')
        self.row=dict(link='https://www.dd-room.com/object/test1',title='測試兩房',price='20,000 元/月',area='10 坪',address='新北市板橋區文化路',metadata='整層住家 2房',tags=['可養貓'],images=[],detail={'status':'ok','text':''})
        self.filters=validate(DEFAULT)
    def test_filters_boundaries(self):
        row=normalize('ddroom',self.row)
        self.assertEqual(matches(row,self.filters),(True,[]))
        for field,value in [('rent',9999),('rent',30001),('ping',9.9),('rooms',3),('address','臺北市中山區')]:
            self.assertFalse(matches({**row,field:value},self.filters)[0])
        self.assertTrue(matches({**row,'ping':100},self.filters)[0])
        for price in (10000,30000): self.assertTrue(matches({**row,'rent':price},self.filters)[0])
    def test_unknown_pet(self):
        row=normalize('ddroom',{**self.row,'tags':[]})
        self.assertEqual(matches(row,self.filters),(True,['寵物']))
        self.assertFalse(matches(row,{**self.filters,'include_unknown':False})[0])
        self.assertFalse(matches(normalize('ddroom',{**self.row,'tags':['不可養寵物']}),self.filters)[0])
        self.assertIn('寵物',matches(normalize('ddroom',self.row),{**self.filters,'pet':'dog'})[1])
    def test_validation(self):
        for bad in ({'min_price':20,'max_price':10},{'max_pages':0},{'min_area':float('nan')},{'min_rooms':1.5}):
            with self.assertRaises(ValueError): validate(bad)
    def test_pagination_end(self):
        soup=BeautifulSoup('<div id="buy_container">共有 29 間房屋</div>','html.parser')
        self.assertFalse(confirmed_end('sinyi',soup,20,1,20))
        self.assertTrue(confirmed_end('sinyi',soup,29,2,9))
        soup=BeautifulSoup('<div>前往 / 共2頁</div>','html.parser')
        self.assertFalse(confirmed_end('rakuya',soup,19,1,19))
        self.assertTrue(confirmed_end('rakuya',soup,25,2,6))
    def test_sinyi_base_and_current_price(self):
        soup=BeautifulSoup('''<base href="https://www.sinyi.com.tw/rent/"><div id="buy_container"><div class="ddhouse"><a href="houseno/C123"><span class="item_title">兩房</span><div class="detail_line2">20.35 坪 12/12樓 2房1廳1衛</div><div class="detail_line2">新北市樹林區保安街</div><div class="detail_price"><del>20000</del><div class="price_new">18500元/月</div></div><div class="detail_tag">可養寵物</div></a></div></div>''','html.parser')
        row=parse('sinyi',soup,'https://www.sinyi.com.tw/rent/list/1.html')[0]
        self.assertEqual(row['link'],'https://www.sinyi.com.tw/rent/houseno/C123')
        self.assertEqual(normalize('sinyi',row)['rent'],18500)
        self.assertEqual(normalize('sinyi',row)['pets'],['可養寵物'])
    def test_rakuya_layout_does_not_use_title(self):
        soup=BeautifulSoup('''<section class="grid-item search-obj"><a href="https://rent.rakuya.com.tw/item/abc"><div class="card__head"><h2>原3房改兩房</h2></div><div class="info__detail-info">整層住家 2房2廳1衛</div><span class="info__geo--area">板橋區</span><span class="info__geo--road">三民路</span><span class="info__price--total">21000元</span><span class="info__space--main">主建26.06坪</span></a></section>''','html.parser')
        row=parse('rakuya',soup,'https://rent.rakuya.com.tw/result')[0]
        self.assertEqual(normalize('rakuya',row)['rooms'],2)
        self.assertIn('三民路',row['address'])
    def test_database_history_snapshots_and_annotations(self):
        run=self.store.enqueue(self.filters,[adapter('ddroom',self.filters)])
        job=self.store.claim()
        self.store.ingest('ddroom',[self.row],job['id'],self.filters)
        ident=self.store.query({})['items'][0]['id']
        self.store.annotate(ident,dict(favorite=True,note='保留',contact='contacted'))
        self.store.ingest('ddroom',[self.row])
        self.assertEqual(self.store.query({})['total'],1)
        self.assertEqual(len(self.store.detail(ident)['history']),1)
        self.store.ingest('ddroom',[{**self.row,'price':'25,000 元/月'}])
        self.assertEqual(len(self.store.detail(ident)['history']),2)
        item=Store(self.store.path).query({})['items'][0]
        self.assertEqual((item['rent'],item['note'],item['favorite']),(25000,'保留',1))
        self.assertEqual(self.store.query({'run_id':run})['items'][0]['rent'],20000)
        self.store.ingest('ddroom',[])
        self.assertEqual(self.store.query({})['total'],1)
        self.store.recover()
        self.assertEqual(self.store.runs()[0]['jobs'][0]['status'],'interrupted')
        target=Path(self.temp.name)/'backup.sqlite3'
        self.store.backup(target)
        self.assertEqual(Store(target).query({})['items'][0]['note'],'保留')
    def test_profiles_query_and_rollback(self):
        result=self.store.save_profile(dict(name='條件',filters=self.filters,sources=['ddroom']))
        self.assertEqual(self.store.profiles()[0]['id'],result['id'])
        self.store.ingest('ddroom',[self.row,{**self.row,'link':'/object/test2','price':'15000'}])
        self.assertEqual(self.store.query({'page_size':1,'sort':'rent_asc'})['items'][0]['rent'],15000)
        self.assertEqual(self.store.query({'filters':{'min_price':19000}})['total'],1)
        with self.assertRaises(ValueError): self.store.ingest('ddroom',[{**self.row,'link':'/object/test3'},{**self.row,'link':'https://evil.example/x'}])
        self.assertEqual(self.store.query({})['total'],2)

if __name__=='__main__': unittest.main()
