# 資金登月圖 · Capital Transfer Orbit

台股與美股的產業資金流球面軌道圖。球面上的**光點是真資料**（每個產業的淨流入／流出），
**弧線是推估的**（配對演算法生出來的），兩者分開看是理解這張圖的關鍵。

| 市場 | 更新頻率 | 資金流定義 | 資料源 |
|---|---|---|---|
| 台股 上市 | 每交易日收盤後 | 外資＋投信買賣超股數 × 當日均價 | 證交所 T86 + STOCK_DAY_ALL |
| 美股 S&P 500 | 每 15 分鐘 | Chaikin 錢流乘數 × 成交額 | Alpaca 15 分 K 線 + 維基 GICS 分類 |

---

## 為什麼要有配對演算法

市場資料只給得出「每個產業淨流入多少」，**沒有任何資料源會告訴你錢從哪個產業搬到哪個產業**。
所以弧線必須自己配對出來。可配對總量 = `min(總流出, 總流入)`，多出來的那一側就是「無法配對」的部分。

頁面提供三種配對規則可以即時切換：

| 演算法 | 規則 | 畫面特徵 |
|---|---|---|
| 貪婪 | 兩邊各自由大到小排序，最大配最大 | 弧線最少最粗 |
| 重力 | `flow(i→j) ∝ 流出ᵢ × 流入ⱼ` | 弧線最密最細 |
| 運輸 | 最小成本法，同族群成本 0.25、跨族群 1.0 | 先在族群內短跳 |

切換演算法時排行榜完全不變，只有弧線連法改變——因為演算法決定的是「誰配給誰」，
不是「每個產業拿到多少」。後者是真資料，前者是推估。

---

## 架設步驟

### 1. 建 repo 並開啟 Pages

把這個資料夾推到一個新的 GitHub repo，然後：

`Settings → Pages → Source` 選 **Deploy from a branch**，分支 `main`、資料夾 `/ (root)`。

過幾分鐘後 `https://<你的帳號>.github.io/<repo 名>/` 就會是這張圖。

### 2. 開啟 Actions 的寫入權限

`Settings → Actions → General → Workflow permissions` 選 **Read and write permissions**，
否則排程跑完沒辦法把資料 commit 回來。

### 3. 申請 Alpaca 免費金鑰（美股才需要）

到 [alpaca.markets](https://alpaca.markets) 註冊，拿 **Paper Trading** 的 API key
（不用入金、不用綁卡）。回到 repo：

`Settings → Secrets and variables → Actions → New repository secret`

新增兩個：`ALPACA_KEY` 和 `ALPACA_SECRET`。

沒有設定金鑰時，workflow 會**整個跳過美股那一步**（靠 `HAS_ALPACA` 判斷），
不會每 15 分鐘空轉一次白燒 Actions 額度。台股照常運作。

### 4. 手動跑一次確認

`Actions → 更新資金流資料 → Run workflow`，下拉選 **check** 先測資料源通不通（不寫檔，3 分鐘內結束）。
確認 Alpaca 通了再選 **both** 跑真正的抓取。手動預設值就是 `check`，不會誤觸抓取。

正常的一輪應該在 **1 分鐘內**結束。如果超過 5 分鐘，一定有問題——去看 log 的熔斷訊息。

### 5. 排程

`.github/workflows/update.yml` 已經設好兩班：

```
*/15 13-21 * * 1-5    # 美股，涵蓋夏令時與冬令時的盤中
10 8 * * 1-5          # 台股，台灣時間 16:10
```

GitHub 的排程在尖峰時段可能延遲幾分鐘，這對 15 分鐘級的資料沒什麼影響。
休市時腳本自己會判斷沒資料就跳過，不會寫壞檔案。

---

## 本機執行

```bash
pip install -r requirements.txt
python scripts/fetch_tw.py              # 台股，最新交易日
python scripts/fetch_us.py              # 美股，最新一根完整的 15 分 K
python scripts/fetch_us.py --check      # 只測資料源通不通
python scripts/fetch_us.py --provider yahoo   # 不用金鑰的退路（雲端上多半會被擋）
```

產生的 JSON 也可以直接在頁面上用「說明 → 載入 JSON 檔」餵進去。

---

## 已知的坑

**證交所會擋自訂 User-Agent。** 送 `User-Agent` header 會拿到一頁
「因為安全性考量，您所執行的頁面無法呈現」的 HTML，但**狀態碼還是 200**。
所以 `fetch_tw.py` 刻意不設 UA，用 requests 的預設值，並檢查 content-type 是不是 JSON。

**Yahoo 擋雲端 IP，不能當主要資料源。** GitHub Actions 的 runner IP 會被 Yahoo 回 429，
第一次上線時因此跑了 2 小時 2 分才拿到 0/503 檔。已改用 Alpaca 批次抓取。

**三個真實踩過的坑，都寫成了回歸測試。**

| 坑 | 症狀 | 修法 |
|---|---|---|
| 沒有整輪時限 + 全域節流旗標不會清 | 一輪跑 7324 秒 | `DEADLINE_SECONDS` + 熔斷器，同情境現在 7 秒 |
| 股號格式：維基給 `BRK.B`，被轉成 Yahoo 的 `BRK-B` 再送給 Alpaca | **整批 HTTP 400**，第一批 180 檔全滅 | 以維基寫法為準，按資料源轉換；Alpaca 用點號、Yahoo 用連字號 |
| 把 Alpaca 的 `limit` 當成「每檔筆數」 | 實際是**整批總筆數**，後兩批只剩 15、12 檔 | 改用時間窗 `start` + `next_page_token` 分頁，不靠 limit 截斷 |
| `check` 只要拿到一檔就算成功 | 覆蓋率 5% 卻是綠燈的**假成功** | `verdict()` 強制 60% 門檻，盤中未達就回非零結束碼 |

`verdict()` 會分辨三種狀態，不會把休市誤判成故障：

| 情況 | 結果 |
|---|---|
| 覆蓋率 ≥ 60% | 綠燈 |
| 覆蓋率不足，且 Alpaca clock 說休市 | 綠燈（本來就不會有新 K 線） |
| 覆蓋率不足，盤中 | **紅燈**（真的壞了） |
| 覆蓋率不足，且問不到開收盤狀態 | **紅燈**（保守處理） |
| 金鑰錯或回應結構不對 | **紅燈** |

另外最新 K 線超過 60 分鐘就不發布，避免把收盤資料標成「即時」。

**證交所三支 API 只要一支被 WAF 擋就整輪白跑。** 公司產業別一週才變一次，
已改成快取（`data/_twse_industry.json`），平常只打兩支，降低被擋的機率。

**Alpaca 免費層是 IEX 報價來源。** 成交量只涵蓋全市場的一小部分，
但各產業被抽樣的比例一致，看「相對」資金流沒問題；絕對金額會偏小，不要拿去跟別的來源對數字。

**覆蓋率保護。** 抓不到 60% 以上的成分股就拒絕寫檔，也不會動到既有的 json。
半殘的資料會讓產業加總嚴重失真，寧可不更新也不要發布錯的數字。

**殘差有兩個方向。** 流入大於流出時，多的是「找不到來源的流入」；
流出大於流入時，多的是「找不到去處的流出」。台股大跌日常常是後者。

## 檔案

```
index.html                    整張圖，單一檔案，沒有外部相依
scripts/fetch_tw.py           台股抓取
scripts/fetch_us.py           美股抓取
scripts/gics_zh.py            GICS 子產業中文對照（127 個，涵蓋 S&P 500 全部）
scripts/test_us.py            45 項單元測試（熔斷、時限、股號、K 線挑選、判定）
scripts/test_alpaca_e2e.py    模擬 Alpaca 伺服器的端到端測試（分頁、股號格式）
scripts/compare_algos.py      三種配對演算法的量化比較
scripts/verify_tw.py          把某產業的數字拆回個股，產生可人工複查的稽核表
scripts/concentration.py      算各產業的集中度，分辨「產業輪動」與「個股事件」
scripts/collect_history.py    回補歷史資料，每個交易日一個檔，存在 data/hist/
scripts/build_history.py      把 hist/ 壓成 data/history_tw.json，給頁面算多日累積
scripts/backtest.py           實測預測力：Rank IC、分位數組合、扣除交易成本
.github/workflows/update.yml  兩班排程
data/tw.json, data/us.json    產出，頁面直接讀這兩個檔
data/_sp500_v2.json           S&P 500 成分股快取，一週自動更新
data/_twse_industry.json      證交所產業別快取，一週自動更新
data/hist/YYYY-MM-DD.json     每日產業資金流，由 fetch_tw.py 自動累積
data/history_tw.json          最近 30 個交易日的壓縮版，頁面用來算多日累積
```

### 研究用的兩支腳本

```bash
python scripts/test_us.py                      # 45 項單元測試，不連網路
python scripts/test_alpaca_e2e.py              # 用模擬伺服器做端到端測試，不用金鑰
python scripts/compare_algos.py data/tw.json   # 三種演算法差在哪
python scripts/verify_tw.py 電腦及週邊          # 把產業數字拆回個股，逐檔對帳
python scripts/concentration.py                # 哪些產業其實只是幾檔股票的事
```

`compare_algos.py` 的輸出會告訴你：三種演算法的**配對總量與每個產業拿到的金額完全相同**，
差別只在弧線數（貪婪 31 條 vs 重力 200 條）、集中度（前 5 條佔 85% vs 63%）、
以及同族群配對的比例。也就是說，演算法改變的是**敘事**，不是**數字**。

## 多日累積、持續性、族群強弱

頁面上切到「5 日」或「10 日」後，球面與左右榜單顯示的是該期間的**累積**淨流入，
下方「趨勢洞察」會出現兩份排行：

| 指標 | 算法 | 怎麼讀 |
|---|---|---|
| 持續同向 | 視窗內與累積方向一致的天數 | 10 天 9 天同向的 +50 億，比 1 天暴衝的 +50 億有意義得多 |
| 族群相對強弱 | `Σ淨流入 ÷ Σ\|淨流入\|`，範圍 −1 ~ +1 | 除以絕對值總和，讓大小族群可比；看的是方向一致性不是金額 |

⚠️ 只有一個產業的族群（運輸、醫療、服務、公用）分數必然是 ±1.00，
所以每一列都標了該族群包含幾個產業，不要跟電子那種 9 個產業的 +0.8 相提並論。

歷史由 `fetch_tw.py` 每天自動累積到 `data/hist/`，再由 `build_history.py`
壓成頁面讀的 `data/history_tw.json`。要一次回補過去幾個月：

```bash
python scripts/collect_history.py --days 120
python scripts/build_history.py --days 30
```

## 這個訊號到底有沒有預測力

用 61 個交易日（2026-06-04 ~ 09-04）實測的結果：

| 期間 | Rank IC | t 值 | 判讀 |
|---|---|---|---|
| 隔 1 日 | 0.068 | 2.01 | 統計顯著，但很薄 |
| 隔 3 日 | 0.000 | 0.01 | 歸零 |
| 隔 5 日 | −0.012 | −0.29 | 歸零 |
| 隔 10 日 | 0.053 | 1.39 | 不顯著 |

分位數組合（最高流入組 − 最低流入組）隔日價差 **+0.572%／日，t = 2.43**。
但台股來回交易成本是 0.585%（全額手續費），**訊號幾乎剛好被成本吃掉**。

同期相關（今天資金流 vs 今天漲跌）Rank IC 高達 **0.59、t = 21.3**——
這個數字沒有任何預測價值，買盤推升價格是同時發生的。
很多「資金流分析」秀的就是這個同期相關，看到相關性沒標明時間差就要懷疑。

```bash
python scripts/collect_history.py --days 120   # 先累積歷史
python scripts/backtest.py                     # 重現上面所有數字
```

## 怎麼驗證數字是對的

不要相信任何自己算不出來的數字。`verify_tw.py` 會把產業層級的金額拆回個股，
印出「法人買賣超股數 × 當日均價 = 金額」的完整稽核表，並和 `data/tw.json` 對帳。

最容易踩的一個坑：**要用均價，不是收盤價**。
均價 = 成交金額 ÷ 成交股數，反映法人真正成交的價位；用收盤價會系統性偏差。
以 2026-09-04 的 3231 緯創為例，兩者差 0.8%，在產業層級加總後就是好幾億的落差。

## 資料格式

```json
{
  "meta": {"market":"台股 上市","unit":"億元","snapshot":"2026-09-03",
           "freq":"收盤後更新","source":"..."},
  "industries": [
    {"name":"半導體業","sector":"電子","net":-269.42,"chg":-4.10}
  ]
}
```

`net` 是淨流入（正流入負流出），`chg` 是產業漲跌幅（%），
`sector` 是上層族群，決定球面上的聚落位置與運輸演算法的配對成本。

---

⚠️ 這是研究與視覺化用途，不構成投資建議。弧線是推估的，不要當成真實的資金移動路徑。
