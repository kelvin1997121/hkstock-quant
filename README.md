# 港股AI分析 hkstock-quant

每個交易日自動分析港股:大市型勢、資金去向、熱炒板塊、短炒同長揸候選股。
喺 GitHub 伺服器上運行,結果放上 GitHub Pages,香港網絡直接睇到,屋企部電腦唔使開住。

## 做咩

| 部分 | 點計 |
|---|---|
| 當日型勢 | 8 項證據加權:恒指趨勢、市寬、5日動力、波幅、隔晚美股、VIX、人民幣、南向資金。會列出反方證據同矛盾訊號(例如指數升但多數股票未企穩) |
| 資金去向 | 每個板塊嘅成交放大倍數、Chaikin 資金流、5日跑贏恒指幾多;南向資金每日淨買賣同淨買入最多嘅股票 |
| 熱炒板塊 | 新聞提及次數對比過去平均 + 資金流。分「新聞+資金共振」「新聞熱但資金冇跟」「資金先行」「資金流出」 |
| 短炒(1-2星期) | 一個月強度、資金流入、成交放大、均線排列、突破、未過熱;連 ATR 止蝕位同目標位 |
| 長揸(3-12個月) | 12-1 個月動力、200天線、低波幅、貼近52週高位;附 PE / 息率 / PB |
| AI 分析 | 有設定 `ANTHROPIC_API_KEY` 就由 Claude 讀晒數據寫分析;冇就用系統規則寫 |

## 準確度(老實講)

所有規則嘅權重都係人手定、冇用數據 fit 過。合格標準喺第一次回測之前寫低咗(見 `hkstock/backtest.py`),頁面會顯示結果。
2026-11-09 第一次回測(2021-2026):短炒分數、長揸分數同型勢判斷**全部未達標**,即係未有證據話佢哋預測到之後嘅升跌。
頁面嘅用處係將大市、資金、新聞整理成一頁,唔係預測機器。

每日收市後嘅判斷會記入 `data/signals_log.json`(git 紀錄證明幾時寫低),5 個交易日後自動計分,呢個實戰紀錄先係真正嘅準確度。

## 用法

```bash
pip install -r requirements.txt
python -m hkstock            # 跑一次,寫 docs/data.json
python -m hkstock --no-ai    # 唔叫 Claude
python -m hkstock --open     # 跑完開頁面(本機要用 python -m http.server -d docs 睇)
```

改股票池:編輯 `hkstock/universe.py` 嘅 `THEMES`(Yahoo 代號,例如 `0700.HK`)。

## 自動運行

`.github/workflows/update.yml` 喺星期一至五 08:45、10:15、12:10、14:30、16:35(香港時間)運行。
加 Claude 分析:Repo → Settings → Secrets and variables → Actions → New secret,名 `ANTHROPIC_API_KEY`。

## 數據來源

Yahoo Finance(港股延遲約 15 分鐘)、東方財富(南向資金)、RTHK、經濟通、Google News RSS。

## 免責

只係數據分析工具,唔係投資建議。唔會自動落盤買賣。投資有風險,自己決定自己負責。
