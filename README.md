# rent-crawler

for personal usage.

## Notes

* 2025-05-21: 新增信義房屋租屋爬蟲

* 2024-12-20: 修正爬蟲可能被導向空白頁的問題

## required

### packages or libs

* python-dotenv
* selenium
* bs4
* pandas


### chromedriver

[download](https://sites.google.com/chromium.org/driver/downloads)

## how to use

### 信義房屋

1. Create `.env` in root folder.
2. Go to https://www.sinyi.com.tw/rent/list and filter your conditions.
3. Copy url and paste to `.env` like this:

```
SINYI_FILTER_URL=<Your url>
```
4. Open terminal and enter this command:
```
python3 app/sinyi/crawler_selenium.py
```
5. Result will be located at: `static/sinyi.html`

### 591

1. Create `.env` in root folder.
2. Go to [https://rent.591.com.tw/list](https://rent.591.com.tw/list) and filter your conditions.
3. Copy url and paste to `.env` like this:

```
591_FILTER_URL=<Your url>
```

4. Open terminal and enter this command:

```
python3 app/five_nine_one/crawler_selenium.py
```

5. Result file will be located at: `static/591recommend.html` and `static/591normal.html`

### Rakuya (樂屋網)

1. Open [樂屋網租屋](https://rent.rakuya.com.tw/) and set your search filters.
2. Copy the resulting URL into `.env`:

```
RAKUYA_FILTER_URL=<Your filtered Rakuya URL>
RAKUYA_MAX_PAGES=20
RAKUYA_PAGE_DELAY_SECONDS=3
```

`RAKUYA_MAX_PAGES` limits the number of result pages. The crawler waits at least two seconds between pages. If Cloudflare asks for a check, complete it manually in the Chrome window and press Enter in the terminal to continue.

3. Run the crawler:

```
python app/rakuya/crawler_selenium.py
```

4. Results are written to `static/rakuya.html` and `static/rakuya.json`.

### DDROOM

1. Open https://www.dd-room.com/search, set your filters, then copy the resulting URL to `.env` as `DDROOM_FILTER_URL`.
2. Set `DDROOM_MAX_PAGES` to limit the pages to crawl (default 20) and `DDROOM_PAGE_DELAY_SECONDS` for the pause between pages (default 2 seconds).
3. Run `python app/ddroom/crawler_selenium.py`.
4. Results are saved to `static/ddroom.html` and `static/ddroom.json`.

DDROOM supports the website's `city`, comma-separated `area`, `min_rent`, `max_rent`, `bedroom`, and `space` URL filters. The crawler also checks these fields locally, keeps listings at least 10 ping, and, when `DDROOM_REQUIRE_PET=1`, requires at least one explicit tag: 可養貓, 可養狗, or 可養其他寵物. Pet types are saved with each result. Current local filters are 新北市新莊區／板橋區／樹林區, NT$10,000–30,000, exactly two bedrooms, whole-unit rentals, and pets allowed. A confirmed empty result replaces old results; a load failure preserves them.

### Rental dashboard

Double-click `開啟房源看板.bat` (or run `python run_dashboard.py`) to start the dashboard. Open its HTTP address; opening dashboard.html directly is no longer supported. The dashboard now stores data in SQLite at `data/rentals.sqlite3` (override with `DASHBOARD_DB`). Keep the server running. For access through Tailscale, set `DASHBOARD_HOST` in `.env` to this computer's Tailscale IPv4 address, then open `http://<Tailscale-IP>:8765/` from a device connected to the same tailnet.

#### 新版操作

1. **搜尋與爬取**：輸入縣市、行政區（逗號分隔多個）、租金、坪數、房數、房型、寵物和設備。空白表示不限；最低坪數填 10、最高留空就是 10 坪以上。
2. **儲存設定**：命名後儲存，可以載入或更新；不會修改過去執行的條件快照。
3. **查詢已存資料**：只讀取資料庫，不啟動爬蟲。預設包含資訊不完整的物件並標示待確認；取消勾選可只看明確符合的物件。
4. **查看各平台篩選方式**：列出原站支援的條件與本機複核方式，並提供產生的搜尋連結。
5. **開始爬取**：為選取的平台排隊，逐一執行。每平台最多 1–50 頁、15 分鐘。關閉手機頁面不會中斷工作。達上限、失敗、人工驗證、確認零筆分開呈現。
6. **房源資料庫**：預設全部已存資料，可切換某次執行的快照，使用關鍵字、平台、租金等篩選及分頁排序，儲存收藏、隱藏、聯絡狀態和註記。
7. **執行紀錄**：查看逐平台進度和錯誤，載入舊條件再次排程。

第一次啟動會自動匯入 static 的既有 JSON（每檔只匯入一次）。新版爬蟲直接透過獨立工作輸出匯入 SQLite，不覆寫舊 JSON。所有發現的物件會入庫，符合條件的才列入該次結果。同平台以來源物件 ID 去重；變更保留歷史，收藏和註記獨立保存。未再次抓到不等於下架。資料庫、工作檔及密碼不會被網站靜態路由公開。

目前地區轉換：DDROOM 使用縣市與行政區名稱；591／信義／樂屋的直接地區代碼涵蓋新北市新莊、板橋、樹林。新北其他行政區會較廣抓取後複核，受頁數上限影響。其他縣市請先選 DDROOM；不支援的組合會拒絕排程並說明原因。網站未提供的房數、寵物或設備保持未知，不推定符合。平台改版與人工驗證可能讓個別工作失敗，此時保留原有房源。

#### 備份與還原

```sh
python manage_db.py backup data/rentals-backup.sqlite3
# 還原前停止看板服務；原資料庫會另存 before-restore 備份。
python manage_db.py restore data/rentals-backup.sqlite3
```

備份使用 SQLite backup API，能在服務運作時取得一致的副本。搬移主機請攜帶備份和私下保存的 `.env`，不要公開上傳。重啟後排隊工作會繼續；原本執行中的工作標為中斷，需要重新排程。

### Mobile access through Cloudflare Tunnel

1. Install `cloudflared` on Windows: `winget install --id Cloudflare.cloudflared --exact`.
2. Set a random password of at least 20 characters as `DASHBOARD_PASSWORD` in the ignored `.env` file, then double-click `開啟手機外網看板.bat`.
3. Open the generated `https://*.trycloudflare.com` URL on your phone. Log in as `rent` using that password. The launcher checks that anonymous requests are denied and authenticated requests work before opening the tunnel.
4. Keep both the dashboard server and tunnel windows running while using the dashboard. Stop the tunnel with Ctrl+C.

This uses a Quick Tunnel with application password protection. Use its HTTPS URL for remote login. The URL changes each time it starts. The launcher uses `DASHBOARD_HOST` from `.env` (localhost by default); crawler buttons still run on this computer. If the configured address belongs to Tailscale, keep Tailscale running on this computer; the phone only needs a web browser. Quick Tunnel email protection was replaced after session expiry errors matching cloudflare/cloudflared issue #1752. For a stable hostname, set up a named Cloudflare Tunnel and an Access application on a domain you control.
