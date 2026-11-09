"""Stock universe grouped by theme (板塊), plus benchmarks and global context.

Edit THEMES to add or drop names. A ticker Yahoo cannot find is skipped with a
warning, so a typo never stops a run. One stock sits in one theme only, so a
theme's money flow is not counted twice.
"""

THEMES: dict[str, dict[str, str]] = {
    "科網平台": {"0700.HK": "騰訊", "9988.HK": "阿里巴巴", "3690.HK": "美團", "9618.HK": "京東",
               "1024.HK": "快手", "9999.HK": "網易", "9888.HK": "百度", "9961.HK": "攜程"},
    "AI及半導體": {"0981.HK": "中芯國際", "1347.HK": "華虹半導體", "0020.HK": "商湯", "1810.HK": "小米",
                "0992.HK": "聯想", "2382.HK": "舜宇光學", "0285.HK": "比亞迪電子", "6682.HK": "第四範式"},
    "新能源車": {"1211.HK": "比亞迪", "2015.HK": "理想汽車", "9868.HK": "小鵬汽車", "9866.HK": "蔚來",
              "0175.HK": "吉利汽車", "2333.HK": "長城汽車"},
    "機器人及自動化": {"9880.HK": "優必選", "2252.HK": "微創機器人", "0669.HK": "創科實業"},
    "醫藥生科": {"1177.HK": "中國生物製藥", "2269.HK": "藥明生物", "1801.HK": "信達生物", "6160.HK": "百濟神州",
              "2359.HK": "藥明康德", "1093.HK": "石藥集團", "9926.HK": "康方生物"},
    "新消費": {"9992.HK": "泡泡瑪特", "6181.HK": "老鋪黃金", "2020.HK": "安踏", "2331.HK": "李寧",
            "9633.HK": "農夫山泉", "6862.HK": "海底撈", "0291.HK": "華潤啤酒", "1929.HK": "周大福"},
    "黃金及有色": {"2899.HK": "紫金礦業", "1818.HK": "招金礦業", "3993.HK": "洛陽鉬業", "1208.HK": "五礦資源",
              "2600.HK": "中國鋁業", "0358.HK": "江西銅業"},
    "石油煤炭": {"0857.HK": "中石油", "0883.HK": "中海油", "0386.HK": "中石化", "1088.HK": "中國神華",
              "1171.HK": "兗礦能源"},
    "中資金融保險": {"0939.HK": "建設銀行", "1398.HK": "工商銀行", "3988.HK": "中國銀行", "2318.HK": "中國平安",
                "2628.HK": "中國人壽", "1299.HK": "友邦保險", "0388.HK": "港交所"},
    "港銀行": {"0005.HK": "匯豐", "2388.HK": "中銀香港", "2888.HK": "渣打"},
    "電訊公用高息": {"0941.HK": "中國移動", "0728.HK": "中國電信", "0762.HK": "中國聯通", "0002.HK": "中電控股",
                "0003.HK": "香港中華煤氣", "0006.HK": "電能實業"},
    "本地地產": {"0016.HK": "新鴻基地產", "0012.HK": "恒基地產", "0001.HK": "長和", "1113.HK": "長實集團",
              "0823.HK": "領展"},
    "內房": {"0688.HK": "中國海外", "1109.HK": "華潤置地", "0960.HK": "龍湖集團", "2202.HK": "萬科",
           "0817.HK": "中國金茂"},
    "航運物流": {"1919.HK": "中遠海控", "0316.HK": "東方海外", "2618.HK": "京東物流"},
}

# News keywords per theme (traditional + simplified). A headline can count for more than one theme.
THEME_KEYWORDS: dict[str, list[str]] = {
    "科網平台": ["科網", "科网", "騰訊", "腾讯", "阿里", "美團", "美团", "京東", "京东", "快手", "網易", "网易",
               "百度", "攜程", "携程", "電商", "电商", "外賣", "外卖", "平台經濟", "平台经济", "遊戲版號", "游戏版号"],
    "AI及半導體": ["AI", "人工智能", "晶片", "芯片", "半導體", "半导体", "中芯", "華虹", "华虹", "算力", "大模型",
                "DeepSeek", "輝達", "英伟达", "英偉達", "Nvidia", "NVIDIA", "小米", "商湯", "商汤", "數據中心", "数据中心"],
    "新能源車": ["電動車", "电动车", "新能源車", "新能源车", "比亞迪", "比亚迪", "理想", "小鵬", "小鹏", "蔚來",
              "蔚来", "吉利", "Tesla", "特斯拉", "車企", "车企", "電池", "电池"],
    "機器人及自動化": ["機器人", "机器人", "人形", "優必選", "优必选", "具身", "自動化", "自动化"],
    "醫藥生科": ["藥", "药", "醫藥", "医药", "生物科技", "創新藥", "创新药", "藥明", "药明", "百濟", "百济",
              "授權", "授权", "臨床", "临床", "FDA"],
    "新消費": ["消費", "消费", "泡泡瑪特", "泡泡玛特", "Labubu", "老鋪", "老铺", "零售", "餐飲", "餐饮", "運動服",
            "运动服", "安踏", "李寧", "李宁", "金飾", "金饰"],
    "黃金及有色": ["黃金", "黄金", "金價", "金价", "銅", "铜", "鋁", "铝", "有色", "紫金", "稀土", "鋰", "锂", "白銀", "白银"],
    "石油煤炭": ["油價", "油价", "石油", "原油", "OPEC", "煤", "天然氣", "天然气", "中海油", "中石油"],
    "中資金融保險": ["內銀", "内银", "保險", "保险", "券商", "港交所", "降準", "降准", "降息", "LPR", "人行", "平安",
                "友邦", "國壽", "国寿"],
    "港銀行": ["匯豐", "汇丰", "恒生銀行", "中銀香港", "渣打", "HIBOR", "港元拆息", "銀行股", "银行股"],
    "電訊公用高息": ["高息", "派息", "股息", "電訊", "电讯", "中移動", "中国移动", "中電", "煤氣", "公用"],
    "本地地產": ["樓市", "楼市", "樓價", "楼价", "新地", "恒地", "長實", "长实", "領展", "領展", "按揭", "賣地", "卖地"],
    "內房": ["內房", "内房", "房地產", "房地产", "碧桂園", "碧桂园", "萬科", "万科", "保交樓", "保交楼", "收儲", "收储"],
    "航運物流": ["航運", "航运", "運費", "运费", "集裝箱", "集装箱", "中遠", "中远", "物流", "關稅", "关税"],
}

BENCHMARKS = {"^HSI": "恒生指數", "^HSCE": "國企指數", "3033.HK": "恒生科技(3033 ETF)"}

GLOBAL = {
    "^GSPC": "標普500", "^IXIC": "納斯達克", "^SOX": "費城半導體", "^VIX": "VIX恐慌指數",
    "CNY=X": "美元兌人民幣", "^TNX": "美國10年債息", "GC=F": "金價", "CL=F": "紐約期油",
    "000300.SS": "滬深300",
}




# ---------------------------------------------------------------- US market
US_THEMES: dict[str, dict[str, str]] = {
    "AI晶片": {"NVDA": "輝達", "AMD": "AMD", "AVGO": "博通", "TSM": "台積電", "ARM": "Arm",
             "MRVL": "Marvell", "QCOM": "高通", "INTC": "英特爾", "MU": "美光"},
    "半導體設備": {"ASML": "ASML", "AMAT": "應用材料", "LRCX": "科林研發", "KLAC": "KLA"},
    "大型科技": {"AAPL": "蘋果", "MSFT": "微軟", "GOOGL": "Alphabet", "AMZN": "亞馬遜", "META": "Meta",
              "NFLX": "Netflix"},
    "AI軟件及雲": {"PLTR": "Palantir", "ORCL": "甲骨文", "CRM": "Salesforce", "NOW": "ServiceNow",
               "SNOW": "Snowflake", "CRWD": "CrowdStrike", "PANW": "Palo Alto"},
    "電動車及自動駕駛": {"TSLA": "特斯拉", "RIVN": "Rivian", "UBER": "Uber"},
    "加密及金融科技": {"COIN": "Coinbase", "MSTR": "Strategy", "HOOD": "Robinhood", "PYPL": "PayPal", "XYZ": "Block"},
    "中概股": {"BABA": "阿里巴巴", "PDD": "拼多多", "JD": "京東", "BIDU": "百度", "NIO": "蔚來", "TME": "騰訊音樂"},
    "銀行金融": {"JPM": "摩根大通", "BAC": "美國銀行", "GS": "高盛", "MS": "摩根士丹利", "WFC": "富國銀行",
              "V": "Visa", "MA": "Mastercard", "BRK-B": "巴郡"},
    "醫藥": {"LLY": "禮來", "NVO": "諾和諾德", "UNH": "聯合健康", "JNJ": "強生", "MRK": "默沙東",
           "ABBV": "艾伯維", "PFE": "輝瑞"},
    "能源": {"XOM": "埃克森美孚", "CVX": "雪佛龍", "OXY": "西方石油", "COP": "康菲石油", "SLB": "斯倫貝謝"},
    "核電及電力": {"VST": "Vistra", "CEG": "Constellation", "OKLO": "Oklo", "SMR": "NuScale",
              "GEV": "GE Vernova", "NEE": "NextEra"},
    "量子計算": {"IONQ": "IonQ", "RGTI": "Rigetti", "QBTS": "D-Wave"},
    "消費零售": {"WMT": "沃爾瑪", "COST": "Costco", "MCD": "麥當勞", "NKE": "Nike", "SBUX": "星巴克",
              "HD": "家得寶"},
    "國防航天": {"LMT": "洛克希德", "RTX": "RTX", "NOC": "諾斯洛普", "RKLB": "Rocket Lab", "SPCX": "SpaceX"},
    "AI網絡及光通訊": {"NOK": "諾基亞", "CIEN": "Ciena", "ANET": "Arista", "CSCO": "思科", "COHR": "Coherent"},
    "煙草高息": {"PM": "菲利普莫里斯", "BTI": "英美煙草", "MO": "Altria"},
    "黃金礦業": {"NEM": "紐蒙特", "AEM": "Agnico Eagle", "B": "巴里克", "WPM": "Wheaton"},
}

# English keywords match as whole words, any case; Chinese ones as substrings.
US_THEME_KEYWORDS: dict[str, list[str]] = {
    "AI晶片": ["輝達", "黃仁勳", "黄仁勋", "英偉達", "英伟达", "Nvidia", "AMD", "博通", "Broadcom", "台積電", "台积电", "TSMC", "晶片",
             "芯片", "GPU", "Marvell", "高通", "Qualcomm", "英特爾", "英特尔", "Intel", "美光", "Micron", "HBM", "chip", "chips"],
    "半導體設備": ["ASML", "應用材料", "Applied Materials", "Lam Research", "KLA", "光刻", "半導體設備", "chipmaking equipment"],
    "大型科技": ["蘋果", "苹果", "Apple", "iPhone", "微軟", "微软", "Microsoft", "谷歌", "Google", "Alphabet", "亞馬遜",
              "亚马逊", "Amazon", "Meta", "Netflix", "七巨頭", "七巨头", "Magnificent Seven", "Big Tech"],
    "AI軟件及雲": ["Palantir", "甲骨文", "Oracle", "Salesforce", "ServiceNow", "Snowflake", "CrowdStrike", "雲端",
               "云端", "cloud", "software", "軟件", "OpenAI", "ChatGPT", "AI agent", "人工智能", "AI"],
    "電動車及自動駕駛": ["Tesla", "特斯拉", "馬斯克", "马斯克", "Musk", "Rivian", "Uber", "自動駕駛", "自动驾驶",
                  "Robotaxi", "電動車", "电动车", "EV"],
    "加密及金融科技": ["比特幣", "比特币", "Bitcoin", "加密", "crypto", "Coinbase", "MicroStrategy", "Robinhood",
                "PayPal", "穩定幣", "稳定币", "stablecoin", "Ethereum", "以太坊"],
    "中概股": ["中概", "阿里", "Alibaba", "拼多多", "PDD", "Temu", "京東", "京东", "百度", "Baidu", "蔚來", "蔚来",
            "ADR", "China stocks", "Chinese stocks"],
    "銀行金融": ["摩根大通", "JPMorgan", "美銀", "美银", "Bank of America", "高盛", "Goldman", "摩根士丹利",
              "Morgan Stanley", "富國", "Wells Fargo", "Visa", "Mastercard", "巴菲特", "Buffett", "Berkshire",
              "銀行", "银行", "banks"],
    "醫藥": ["禮來", "礼来", "Eli Lilly", "Lilly", "諾和諾德", "诺和诺德", "Novo Nordisk", "減肥藥", "减肥药", "GLP-1",
           "Wegovy", "Ozempic", "UnitedHealth", "聯合健康", "輝瑞", "辉瑞", "Pfizer", "默沙東", "Merck", "FDA", "drugmaker"],
    "能源": ["油價", "油价", "原油", "oil", "OPEC", "埃克森", "Exxon", "雪佛龍", "Chevron", "天然氣", "天然气", "crude"],
    "核電及電力": ["核電", "核电", "nuclear", "Vistra", "Constellation", "Oklo", "NuScale", "電力", "电力", "power grid",
              "GE Vernova", "電網", "电网", "utilities", "electricity"],
    "量子計算": ["量子", "quantum", "IonQ", "Rigetti", "D-Wave"],
    "消費零售": ["沃爾瑪", "沃尔玛", "Walmart", "Costco", "麥當勞", "麦当劳", "McDonald's", "Nike", "星巴克",
              "Starbucks", "Home Depot", "零售", "retail", "consumer", "消費", "消费"],
    "國防航天": ["國防", "国防", "defense", "Lockheed", "Raytheon", "RTX", "Northrop", "Rocket Lab", "SpaceX", "太空", "軍工", "军工"],
    "黃金礦業": ["黃金", "黄金", "金價", "金价", "gold", "Newmont", "Barrick", "白銀", "白银", "silver"],
    "AI網絡及光通訊": ["諾基亞", "诺基亚", "Nokia", "Ciena", "Arista", "思科", "Cisco", "Coherent", "光通訊", "光通讯",
                  "光模塊", "光模块", "optical", "networking", "交換機", "交换机", "電訊設備", "电讯设备"],
    "煙草高息": ["煙草", "烟草", "tobacco", "Philip Morris", "菲利普莫里斯", "British American Tobacco", "英美煙草",
              "Altria", "IQOS", "Zyn", "尼古丁", "nicotine"],
}

# Holdings that are not single stocks: priced and given levels for the holdings tab,
# but kept out of the stock ranking, theme flows and backtests.
US_EXTRAS = {"VOO": "Vanguard 標普500 ETF", "BTC-USD": "比特幣"}

US_BENCHMARKS = {"^GSPC": "標普500", "^IXIC": "納斯達克", "^DJI": "道瓊斯", "^RUT": "羅素2000"}

US_GLOBAL = {
    "^VIX": "VIX恐慌指數", "^TNX": "美國10年債息", "DX-Y.NYB": "美匯指數", "HYG": "高息債ETF", "LQD": "投資級債ETF",
    "GC=F": "金價", "CL=F": "紐約期油", "BTC-USD": "比特幣", "^HSI": "恒生指數",
}


# ---------------------------------------------------------------- market settings
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Market:
    key: str
    name: str
    themes: dict
    keywords: dict
    benchmarks: dict
    context: dict
    index: str            # main index ticker
    index_name: str
    tech: str             # tech gauge compared with the index (risk appetite)
    tech_name: str
    tz: str
    cost: float           # round-trip trading cost used in the backtest
    feeds: dict = field(default_factory=dict)
    google: tuple = ()    # (query, language) pairs for Google News RSS
    out: str = "data.json"
    suffix: str = ""
    extras: dict = field(default_factory=dict)

    def names(self) -> dict[str, str]:
        return {t: n for group in self.themes.values() for t, n in group.items()}

    def theme_of(self) -> dict[str, str]:
        return {t: th for th, group in self.themes.items() for t in group}


HK = Market(
    key="hk", name="港股", themes=THEMES, keywords=THEME_KEYWORDS, benchmarks=BENCHMARKS, context=GLOBAL,
    index="^HSI", index_name="恒指", tech="3033.HK", tech_name="恒生科技", tz="Asia/Hong_Kong", cost=0.003,
    feeds={"RTHK財經": "https://rthk.hk/rthk/news/rss/c_expressnews_cfinance.xml",
           "經濟通": "https://www.etnet.com.hk/www/tc/news/rss.php?section=editor"},
    google=(("港股", "zh"), ("恒指", "zh"), ("南向資金", "zh"), ("港股 板塊", "zh"), ("美股 收市", "zh")),
    out="data.json", suffix="",
)

US = Market(
    key="us", name="美股", themes=US_THEMES, keywords=US_THEME_KEYWORDS, benchmarks=US_BENCHMARKS, context=US_GLOBAL,
    index="^GSPC", index_name="標普500", tech="^IXIC", tech_name="納指", tz="America/New_York", cost=0.001,
    google=(("美股", "zh"), ("華爾街 股市", "zh"), ("聯儲局 減息", "zh"), ("納指 科技股", "zh"),
            ("stock market today", "en"), ("Wall Street stocks", "en"), ("stocks rally OR selloff", "en")),
    out="us.json", suffix="_us", extras=US_EXTRAS,
)

MARKETS = {"hk": HK, "us": US}
