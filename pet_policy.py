"""Conservative, evidence-bearing pet restrictions; explicit prohibitions win."""
import re
import unicodedata

VERSION=1
def policy(texts):
    result={'cat':'unknown','dog':'unknown','other':'unknown','any':'unknown','evidence':[],'version':VERSION}
    allowed=set();denied=set();conditional=set();general=False
    for raw in texts:
        raw=unicodedata.normalize('NFKC',str(raw)).translate(str.maketrans({'宠':'寵','猫':'貓','养':'養','仅':'僅','许':'許'}))
        for clause in re.split(r'[\n。；;！!？?]',raw):
            text=re.sub(r'\s+','',clause)
            if not re.search('寵|貓|狗|犬|動物',text):continue
            if re.search(r'不(?:禁|禁止|限制)|不是不|並非不|可否|是否',text):continue
            evidence=False
            negative=r'(?:不可|不能|不得|不准|不允許|不接受|不接|不收|謝絕|禁止|禁|勿|無法|拒絕|不行|不可以|不給)'
            uncertain=bool(re.search(r'(?:需|須).{0,8}(?:同意|討論|確認|商量)|可議|另議|看情況',text))
            if uncertain and '寵物' in text:conditional.update(('cat','dog','other'));evidence=True
            if re.search(negative+r'(?:飼養|養|養殖|帶|攜帶|有)?(?:任何|所有|一切)?(?:寵物|寵|動物)',text) or re.search(r'(?:飼養)?寵物[:：]?(?:不可|不可以|禁止|禁養|不允許|不能|不行|否)(?!議)',text):
                denied.update(('cat','dog','other'));evidence=True
            for key,animal in [('cat','貓'),('dog','(?:狗|犬)'),('other','其他寵物')]:
                if uncertain and re.search(animal,text):conditional.add(key);evidence=True
                if re.search(r'(?:大型|小型|限一隻|限1隻)'+animal,text):conditional.add(key);evidence=True
                if re.search(negative+r'(?:飼養|養|帶)?'+animal,text) or re.search(animal+r'[:：]?(?:不可|禁止|不行|不可以)(?:$|[,，、])',text):
                    denied.add(key);evidence=True
                if re.search(negative+r'(?:飼養|養)?(?:貓[、/與和及]?狗|狗[、/與和及]?貓)',text):denied.update(('cat','dog'));evidence=True
                if re.search(r'(?:可|可以|允許)(?:飼養|養|帶)?'+animal,text) and not re.search(r'可議|另議|需|須|同意|看情況|限量|限一|限1|小型|大型',text):
                    allowed.add(key);evidence=True
            if re.search(r'(?<!不)(?:僅|只|限)(?:能|可|允許|接受)?(?:飼養|養)?貓(?![或及和、/])',text):
                allowed.add('cat');denied.update(('dog','other'));evidence=True
            if re.search(r'(?<!不)(?:僅|只|限)(?:能|可|允許|接受)?(?:飼養|養)?(?:狗|犬)(?![或及和、/])',text):
                allowed.add('dog');denied.update(('cat','other'));evidence=True
            if re.search(r'(?:可|可以|允許)(?:飼養|養)?寵物|可寵|寵物[:：]?可(?:以)?$',text) and not re.search(r'不可|不能|不得|可議|另議|需|須|同意|限|小型|大型',text):
                general=True;evidence=True
            if evidence:
                if len(clause)<150:result['evidence'].append(clause.strip())
                else:
                    for hit in re.finditer(r'寵物|寵|貓|狗|犬',clause):
                        result['evidence'].append(clause[max(0,hit.start()-30):hit.end()+40].strip())
    for key in ('cat','dog','other'):
        result[key]='denied' if key in denied else 'allowed' if key in allowed and key not in conditional else 'unknown'
    result['any']='denied' if len(denied)==3 else 'allowed' if allowed-denied-conditional or general and not denied and not conditional else 'unknown'
    result['evidence']=list(dict.fromkeys(result['evidence']))[:20]
    return result

def row_policy(row):
    texts=[row.get('title',''),*row.get('tags',[]),*row.get('pets',[])]
    detail=row.get('detail',{})
    if detail.get('status')=='ok':texts.append(detail.get('text',''))
    result=policy(texts)
    if detail.get('status')!='ok':
        for key in ('cat','dog','other','any'):
            if result[key]=='allowed':result[key]='unknown'
    return result

def pet_match(row,choice):
    p=row_policy(row)
    return p.get(choice,'unknown'),p
