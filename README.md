# 港股AI分析 hkstock-quant

**網站:https://kelvin1997121.github.io/hkstock-quant/**(香港網絡直接用到,手機電腦都得)

每個交易日自動分析**港股同美股**:大市型勢、資金去向、熱炒板塊、短炒同長揸候選、買入區/止蝕/目標價,
加 Claude 每日獨立分析、即時報價、持倉分析同手機提醒。全部喺 GitHub 同 Claude 雲端運行,屋企電腦唔使開。

## 網頁有咩

| 部分 | 內容 |
|---|---|
| 即時報價 | 頁頂指數、每張候選卡、各板塊表同持倉,每 30 秒由騰訊報價更新(美股即時、港股即時;比特幣跟分析更新) |
| 當日型勢 | 港股 8 項證據(恒指趨勢、市寬、動力、波幅、隔晚美股、VIX、人民幣、南向資金);美股 8 項(標普趨勢、市寬、動力、波幅、VIX、10年債息、美匯、高息債對投資級債)。列出反方證據同矛盾訊號 |
| Claude 每日分析 | 港股開市前 08:30 同收市後 17:30(美股喺 08:30 一齊做):讀晒數據、上網查原因、寫自己嘅判斷(可以同系統唔同意),講明未來 5 日大市方向、信心同最多 3 隻睇好股,5 個交易日後自動計分 |
| 資金去向 | 板塊成交放大、資金流、跑贏大市;港股南向資金(每日淨買賣、淨買入最多);美股板塊 ETF 估算資金流同公司高層用自己錢買入(Form 4) |
| 熱炒板塊 | 新聞熱度對比平時 + 資金流:「新聞+資金共振」「新聞熱但資金冇跟」「資金先行」「資金流出」 |
| 短炒 / 長揸候選 | 各 10 隻,附理由、PE/息率、業績日期提示 |
| 買賣位 | 買入區、止蝕、目標 1/2、回報風險比,按交易所價位表向下執位;即時顯示「喺買入區內 / 高於買入區唔追 / 已跌穿止蝕 / 已到目標」。規則:`hkstock/levels.py` |
| 注碼計算 | 輸入組合總值同每筆最多蝕幾多 %,每張短炒卡顯示最多買幾多股 |
| 各板塊最強 | 每個板塊短炒分最高 2 隻,弱勢板塊都睇到入面最強嘅 |
| 未來 7 日業績 | 業績前後波幅大,候選同持倉都會提示 |
| 我的持倉 | 輸入「代號 股數 成本」:盈虧、佔比、趨勢、支持/阻力、止蝕參考、集中風險。**只存喺你部機嘅瀏覽器,唔會上傳** |
| 準唔準 | 回測結果、實戰紀錄(系統同 Claude 各自計分)同每日表現圖 |
| 延遲警告 | 交易時段數據超過 45 分鐘冇更新,頁頂出黃色警告(假期唔會) |
| 模擬倉 | 10 萬港元模擬資金,Claude 每個交易日 08:30 同 17:30 自己落單買賣港美股,寫策略筆記。單喺下一個開市時段先成交(真實開市價/高/低,富途收費估算),冇得事後揀價;同「一半恒指一半標普」比較。每單平倉自動計 R、最高曾賺/最多曾蝕、平倉後走勢,按買入類型/型勢/分數等分組統計(少過 10 單當樣本唔夠);Claude 逐單寫檢討,整理成教訓(試行 → 有 5 單證據先可以採用 → 推翻就放棄),每次落單前要跟。規則:`hkstock/paper.py`、`hkstock/paper_learn.py` |

## 準確度(老實講)

所有規則都係人手定、冇用數據 fit 過;合格標準喺測試之前寫低並推上 GitHub(`research/`)。到 2026-11-10 為止:

| 測試 | 結果 |
|---|---|
| 原本短炒分、長揸分、大市型勢(2021–2026,兩個市場) | 全部唔合格 |
| 買賣位「先到目標定先到止蝕」 | 美股合格(t 約 2.5,有倖存者偏差要打折),港股唔合格 |
| 7 個價量指標(反彈、回調、52週高、板塊動量、放量漂移、MACD、布林帶) | 開發期全部唔合格,鎖定期冇打開 |
| 業績超預期漂移、分析師評級(2014–2026) | 美股業績漂移喺 2014–2021 合格,但 2021–2026 鎖定期唔合格;其餘開發期唔合格 |

即係話:用免費嘅價格、業績、分析師數據,喺呢啲大型股度搵唔到近幾年仲穩定有效嘅預測訊號。網頁嘅用處係**整理資訊同管理風險**,唔係預測機器。
真正嘅準確度睇實戰紀錄:系統每日收市後判斷記入 `data/signals_log*.json`,Claude 判斷記入 `data/claude_log.json`(git 紀錄證明幾時寫低,冇得事後改),5 個交易日後自動計分。

## 自動運行

| 項目 | 點運作 |
|---|---|
| 分析更新 | `.github/workflows/ticker.yml` 喺交易時段(港股 08:30–16:45 HKT、美股 08:30–16:50 紐約時間)每 15 分鐘觸發 `update.yml`;GitHub 自己嘅定時只係後備 |
| 假期 | `hkstock/market_days.py` 收錄 2026–2027 港美股假期同半日市,假期唔分析、唔記錄(2028 前要更新,測試會提醒) |
| 自我檢查 | 每次更新先跑 `tests/`(58 個測試),唔過就唔發佈 |
| 數據把關 | `hkstock/quality.py`:指數冇價、股票少咗兩成、指數單日 ±30%、數據日期倒退 → 該市場今次唔更新,保留舊數據 |
| 失敗通知 | 更新失敗會開 GitHub issue(GitHub email 通知),恢復後自動關閉 |
| 套件升級 | `.github/dependabot.yml` 每星期六檢查新版本,以 PR 形式出現,`tests.yml` 自動測試,**要人手合併**;`requirements.txt` 有大版本上限 |
| Claude 分析 | claude.ai 雲端定時任務(用 Claude 訂閱,唔使 API key),寫入 `docs/ai.json` 同 `data/claude_log.json`;需要 Claude GitHub App 有呢個 repo 嘅權限 |

## 手機提醒(Telegram,可選)

`hkstock/alerts.py`:每次更新後檢查你嘅持倉,跌穿/重上 50 或 200 天線、單日 ±5%、RSI 過熱、虧損每跌多 10%、業績前 3 日、大市型勢轉變、孖展流動性估算偏低就推送;每個交易日朝早發一份持倉摘要。
持倉只存喺 GitHub 加密 secret,唔會出現喺公開 repo 或者網頁;提醒狀態存喺 Actions cache,唔會 commit。設定:

1. Telegram 搵 @BotFather 開 bot,攞 token;打開個 bot 撳 Start 再 send 一句。
2. `gh secret set TELEGRAM_BOT_TOKEN -R kelvin1997121/hkstock-quant`
3. `python -m hkstock.alerts chatid`(攞 chat id),再 `gh secret set TELEGRAM_CHAT_ID -R kelvin1997121/hkstock-quant --body <chat id>`
4. 持倉(每行「代號 股數 成本」,可加 `MARGIN 代號,代號 loan_usd=… maint=… fx=…`)放喺 `private/holdings.txt`(唔會上傳),然後
   `Get-Content private\holdings.txt -Raw | gh secret set HOLDINGS -R kelvin1997121/hkstock-quant`

## 本機用法

```bash
pip install -r requirements.txt
python -m hkstock                  # 港股同美股各跑一次,寫 docs/data.json 同 docs/us.json
python -m hkstock --market us      # 只跑美股(hk / us / all / auto)
python -m hkstock --no-ai          # 唔叫 Claude API
python -m pytest -q                # 測試
python -m hkstock.ai_tools brief --market hk   # Claude 分析用嘅數據摘要
```

改股票池:`hkstock/universe.py` 嘅 `THEMES`(港股)或 `US_THEMES`(美股);ETF/加密貨幣放 `US_EXTRAS`(只用嚟分析持倉,唔參與排名)。

## 檔案

| 位置 | 內容 |
|---|---|
| `hkstock/` | 分析程式:數據、指標、型勢、資金、新聞、評分、買賣位、回測、實戰紀錄、提醒、假期、數據把關 |
| `docs/` | 網頁(`index.html`)同數據(`data.json` 港股、`us.json` 美股、`ai.json` Claude 分析) |
| `data/` | 實戰紀錄、新聞基準、業績日曆、內部人士數據快取 |
| `research/` | 預先登記嘅測試計劃、測試程式同結果 |
| `tests/` | 自動測試 |
| `BACKLOG.md` | 改善清單同進度紀錄 |

## 數據來源

Yahoo Finance(日線、業績、分析師;港股延遲約 15 分鐘)、騰訊報價(網頁即時價)、東方財富(南向資金)、openinsider.com(美國公司內部人士買賣,每日最多攞一次)、RTHK / 經濟通 / Google News RSS。

## 免責

只係數據分析工具,唔係投資建議。系統唔會自動落盤買賣。投資有風險,自己決定自己負責。
