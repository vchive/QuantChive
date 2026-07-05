"""百度 fundflow 破解验证：一次开浏览器，page.evaluate 内 fetch 多股（自动带 Acs-Token）。"""
from playwright.sync_api import sync_playwright
import json

CODES = ['600519', '000001', '000725', '600150']

JS = """async (codes) => {
  const out = {};
  for (const code of codes) {
    try {
      const u = 'https://finance.pae.baidu.com/vapi/v1/fundflow?finance_type=stock&type=stock&market=ab&code='
        + code + '&belongs=stocklevelone&finClientType=pc';
      const r = await fetch(u, {headers: {'Referer': 'https://finance.baidu.com/'}});
      out[code] = {status: r.status, body: r.status === 200 ? await r.text() : ''};
    } catch (e) { out[code] = {err: String(e)}; }
    await new Promise(s => setTimeout(s, 700));
  }
  return out;
}"""

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_context(
        user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122 Safari/537.36'
    ).new_page()
    try:
        page.goto('https://gushitong.baidu.com/stock/ab-600519',
                  wait_until='domcontentloaded', timeout=40000)
    except Exception as e:
        print('goto:', type(e).__name__)
    page.wait_for_timeout(3000)
    res = page.evaluate(JS, CODES)
    b.close()

print('一次浏览器 + page.evaluate 内连续 fetch 多股:')
ok = 0
for code, r in res.items():
    st = r.get('status')
    if st == 200 and r.get('body'):
        try:
            sp = json.loads(r['body'])['Result']['content']['fundFlowSpread']['result']
            sg = sp['superGrp']
            print(f'  {code}: ✓ 超大单 流入={sg["turnoverIn"]} 流出={sg["turnoverOut"]} 净={sg["netTurnover"]}')
            ok += 1
        except Exception as e:
            print(f'  {code}: 200但解析失败 {e}')
    else:
        print(f'  {code}: status={st} {r}')
print(f'\n=> {ok}/{len(CODES)} 成功。一次浏览器能连续取多股 = {"可行" if ok >= 3 else "需再调"}')
