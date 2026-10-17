import unittest
from pet_policy import policy
from rental_filters import matches,normalize
from rental_details import extract
from test_rentals import RentalTests

class PolicyTests(unittest.TestCase):
    def test_rules(self):
        cases=[('禁養寵物','any','denied'),('寵物：不可','cat','denied'),('不可養寵物','dog','denied'),('謝絕寵物','any','denied'),('禁狗','dog','denied'),('禁狗','cat','unknown'),('限貓','cat','allowed'),('限貓','dog','denied'),('限貓','other','denied'),('可貓不可狗','cat','allowed'),('可貓不可狗','dog','denied'),('不可養貓狗','cat','denied'),('可養寵物','any','allowed'),('可養寵物','dog','unknown'),('寵物可議','any','unknown'),('不禁養寵物','any','unknown'),('需屋主同意才可養貓','cat','unknown'),('限小型犬','dog','unknown')]
        for text,kind,expected in cases:
            with self.subTest(text=text,kind=kind):self.assertEqual(policy([text])[kind],expected)
    def test_denial_wins_over_card(self):
        row=normalize('ddroom',{'link':'/object/a','tags':['可養貓'],'detail':{'status':'ok','text':'禁止養寵物'}})
        for kind in ('any','cat','dog','other'):self.assertFalse(matches(row,{'pet':kind,'include_unknown':True})[0])
    def test_description_scope(self):
        fixtures={
            '591':'<div class="house-condition-content">限貓</div><section class="service"><div class="desc-item">養寵物 不可養寵物</div></section><aside>可養狗</aside>',
            'sinyi':'<ul class="features-description"><li>禁狗</li></ul><ul class="information-content"><li>寵物：不可</li></ul><aside>可養貓</aside>',
            'ddroom':'<article class="e-object-content">禁養寵物</article><aside>可養貓</aside>',
            'rakuya':'<div class="main-content"><li><span class="list__label">飼養寵物</span> 不可</li><div class="block__push">可養貓</div></div>'}
        for source,html in fixtures.items():
            body=extract(source,html)
            self.assertNotIn('可養貓',body);self.assertNotIn('可養狗',body)
            self.assertEqual(policy([body])['any'],'denied')

class DetailStoreTests(RentalTests):
    def test_detail_survives_refresh_and_filters_old_runs(self):
        run=self.store.enqueue(self.filters,[{'source':'ddroom'}]);job=self.store.claim()
        self.store.ingest('ddroom',[self.row],job['id'],self.filters)
        ident=self.store.query({})['items'][0]['id']
        self.store.annotate(ident,{'note':'保留'})
        self.store.save_detail(ident,dict(status='ok',text='限貓，禁狗',checked_at='2026-10-18'))
        self.assertEqual(self.store.query({'filters':{'pet':'dog'}})['total'],0)
        self.assertEqual(self.store.query({'filters':{'pet':'cat'}})['total'],1)
        self.store.save_detail(ident,dict(status='ok',text='不可養寵物',checked_at='2026-10-18'))
        self.store.ingest('ddroom',[{k:v for k,v in self.row.items() if k!='detail'}])
        self.assertEqual(self.store.query({'filters':{'pet':'any'}})['total'],0)
        self.assertEqual(self.store.query({'run_id':run})['total'],0)
        self.assertEqual(self.store.query({})['items'][0]['note'],'保留')
        self.store.save_detail(ident,dict(status='blocked',text='',checked_at='2026-10-18'))
        self.assertEqual(self.store.query({'filters':{'pet':'any'}})['total'],0)

if __name__=='__main__':unittest.main()
