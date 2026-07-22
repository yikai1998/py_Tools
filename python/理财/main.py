# coding=gbk
import re
import pandas as pd
import akshare as ak  # 相关数据接口，读基金各种信息
import requests
import demjson3 as dj
import bs4
import io
import sys
import numpy as np
import datetime
import time
import os
import matplotlib.pyplot as plt


HOLD_COLUMNS = ["序号", "股票代码", "股票名称", "占净值比例", "持股数", "持仓市值", "季度"]


def empty_hold_df():
    return pd.DataFrame(columns=HOLD_COLUMNS)


def extract_js_object(txt: str):
    """
    从东方财富返回的 var apidata={...}; 中提取 {...}
    """
    if not txt:
        raise ValueError("东方财富返回空内容")

    left = txt.find("{")
    right = txt.rfind("}")

    if left == -1 or right == -1 or right <= left:
        preview = txt[:500].replace("\n", "\\n")
        raise ValueError(f"返回内容中找不到有效 JSON/JS 对象，前500字符：{preview}")

    return txt[left:right + 1]


def get_fund_code(path: str = "基金代码名单_持仓.txt"):
    """
    功能: 获取准备好的基金名单，每行一个。文件允许有多余空白，会自动去掉；代码空则退出。
    参数: path 文本 文件路径
    返回: list
    """
    with open(file=path, mode="r", encoding="utf-8") as f:
        fund_code_list = [line.strip().split()[0] for line in f if line.strip()]
        print(fund_code_list)

    if len(fund_code_list) == 0:
        print("基金代码名单为空")
        sys.exit(1)

    return fund_code_list


def basic_profile(fund_code: str):
    """
    功能: 用AkShare获取基金的基本信息（如基金名称、类别、成立时间、运作状态、基金经理等），并以dict形式返回，便于展示概览/打印输出。
    参数: fund_code 文本 基金代码，如 000001、161725 等。
    返回: dic, str
    """
    print(f"正在拉取基金 {fund_code} 的简介 ……")
    basic = ak.fund_individual_basic_info_xq(symbol=fund_code)  # 返回 DataFrame
    intro = {str(k): str(v) if pd.notna(v) else "" for k, v in basic.values}
    folder = f"{intro['基金名称']}_基金代码{fund_code}"
    os.makedirs(folder, exist_ok=True)
    filename = os.path.join(folder, "简介.txt")

    with open(filename, "w", encoding="utf-8") as f:
        for k, v in intro.items():
            print(f"{k}\t{v}")
            f.write(f"{k}\t{v}\n")

    return intro, intro["基金名称"]


def hold_base(fund_code: str, name: str):
    """
    功能: 获取基金最近两个年度已披露的最新季度股票持仓数据。
    """
    print(f"正在拉取基金 {fund_code} 近两年度持仓 ……")

    fund_code = str(fund_code).zfill(6)
    now = datetime.datetime.now()
    years = [str(now.year - 1), str(now.year)]

    big = pd.DataFrame()

    for y in years:
        r = pd.DataFrame()

        try:
            r = ak.fund_portfolio_hold_em(symbol=fund_code, date=y)
        except Exception as e:
            print(f"[akshare 接口失败] {fund_code} {y}: {e}，尝试自解析……")

            try:
                r = manual_parse(fund_code, y)
            except Exception as e2:
                print(f"[自解析也失败] {fund_code} {y}: {e2}")
                r = empty_hold_df()

        if r is None or r.empty:
            print(f"代码 {fund_code} 在 {y} 年暂未披露持仓")
            continue

        big = pd.concat([big, r], ignore_index=True)

        time.sleep(0.8)

    if big.empty:
        return pd.DataFrame(columns=["股票代码", "股票名称", "占净值比例", "持股数", "持仓市值", "季度"])

    # 提取标准季度
    q_extract = big["季度"].astype(str).str.extract(r"(\d{4})[年Qq]?第?([1234])?[季度]?")

    # 上面的正则可能对某些格式不稳，所以再补一版
    bad = q_extract[0].isna() | q_extract[1].isna()
    if bad.any():
        q_extract2 = big.loc[bad, "季度"].astype(str).str.extract(r"(\d{4}).*?([1234])")
        q_extract.loc[bad, 0] = q_extract2[0]
        q_extract.loc[bad, 1] = q_extract2[1]

    big["std_q_text"] = q_extract[0] + "Q" + q_extract[1]
    big["std_q"] = big["std_q_text"].apply(
        lambda x: pd.Period(x, freq="Q") if isinstance(x, str) and re.match(r"\d{4}Q[1-4]", x) else pd.NaT
    )

    big = big.dropna(subset=["std_q"])

    if big.empty:
        print(f"代码 {fund_code} 未能识别有效季度")
        return pd.DataFrame(columns=["股票代码", "股票名称", "占净值比例", "持股数", "持仓市值", "季度"])

    latest_q = big["std_q"].max()
    big = big[big["std_q"] == latest_q].drop(columns=["std_q", "std_q_text"])

    big["占净值比例"] = pd.to_numeric(big["占净值比例"], errors="coerce")
    big.sort_values(by="占净值比例", inplace=True, ascending=False)
    big.reset_index(drop=True, inplace=True)

    folder = f"{name}_基金代码{fund_code}"
    os.makedirs(folder, exist_ok=True)

    filename = os.path.join(folder, "最新持仓.csv")
    big.to_csv(path_or_buf=filename, encoding="utf-8", sep="\t", index=False)

    return big


def manual_parse(symbol: str, year: str):
    """
    功能: 用于从东方财富F10直接解析某基金某年度的全部历史持仓信息。
    参数: symbol 基金代码，如 000001、161725 等。year 年份字符串，如 2023。
    返回: DataFrame
    """
    symbol = str(symbol).zfill(6)
    year = str(year)

    url = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"

    params = {
        "type": "jjcc",
        "code": symbol,
        "topline": "10000",
        "year": year,
        "month": "",
        "rt": str(time.time()),
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36"
        ),
        "Referer": f"https://fundf10.eastmoney.com/ccmx_{symbol}.html",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    resp = requests.get(url=url, params=params, headers=headers, timeout=15)

    if resp.status_code != 200:
        raise RuntimeError(f"HTTP状态异常：{resp.status_code}，url={resp.url}")

    txt = resp.text.strip()

    # 调试用，出问题时很有用
    # print("URL:", resp.url)
    # print("TEXT_HEAD:", txt[:500])

    js_text = extract_js_object(txt)

    try:
        data = dj.decode(js_text)
    except Exception as e:
        preview = js_text[:500].replace("\n", "\\n")
        raise ValueError(f"demjson3解析失败：{e}；待解析内容前500字符：{preview}") from e

    content = data.get("content", "")

    if not content or not str(content).strip():
        return empty_hold_df()

    soup = bs4.BeautifulSoup(content, "lxml")

    heads = []
    for h4 in soup.find_all("h4", class_="t"):
        text = h4.get_text(strip=True)
        if "\xa0\xa0" in text:
            heads.append(text.split("\xa0\xa0")[-1])
        else:
            heads.append(text)

    try:
        tables = pd.read_html(io.StringIO(content), converters={"股票代码": str})
    except ValueError:
        return empty_hold_df()

    if not tables:
        return empty_hold_df()

    result_list = []

    for idx, html_table in enumerate(tables):
        html_table = html_table.copy()

        # 统一列名：去掉空格、百分号、括号等
        html_table.columns = [
            re.sub(r"[ %　()（）]", "", str(c).strip())
            for c in html_table.columns
        ]

        # 删除无用列
        for col in ["相关资讯"]:
            if col in html_table.columns:
                html_table.drop(columns=[col], inplace=True)

        # 模糊寻找各列
        code_col = next((c for c in html_table.columns if "股票代码" in c), None)
        name_col = next((c for c in html_table.columns if "股票名称" in c), None)
        ratio_col = next((c for c in html_table.columns if "占净值" in c), None)
        shares_col = next((c for c in html_table.columns if "持股数" in c), None)
        value_col = next((c for c in html_table.columns if "持仓市值" in c), None)

        # 不是持仓表就跳过
        if code_col is None or name_col is None:
            continue

        sub = pd.DataFrame()

        sub["股票代码"] = html_table[code_col].astype(str).str.zfill(6)
        sub["股票名称"] = html_table[name_col].astype(str)

        if ratio_col is not None:
            sub["占净值比例"] = (
                html_table[ratio_col]
                .astype(str)
                .str.replace("%", "", regex=False)
                .str.strip()
            )
        else:
            sub["占净值比例"] = pd.NA

        if shares_col is not None:
            sub["持股数"] = html_table[shares_col]
        else:
            sub["持股数"] = pd.NA

        if value_col is not None:
            sub["持仓市值"] = html_table[value_col]
        else:
            sub["持仓市值"] = pd.NA

        quarter = heads[idx] if idx < len(heads) else f"{year}年未知季度"
        sub["季度"] = quarter

        result_list.append(sub)

    if not result_list:
        return empty_hold_df()

    big = pd.concat(result_list, ignore_index=True)

    big["占净值比例"] = pd.to_numeric(big["占净值比例"], errors="coerce")
    big["持股数"] = pd.to_numeric(big["持股数"], errors="coerce")
    big["持仓市值"] = pd.to_numeric(big["持仓市值"], errors="coerce")

    big.insert(0, "序号", range(1, len(big) + 1))

    return big[HOLD_COLUMNS]


def fetch_all_nav(fund_code: str):
    """
    功能: 根据基金代码拉取全部历史净值。
    参数: fund_code 文本 基金代码，如 000001、161725 等。
    返回: dataframe
    介绍:
        1. 数据获取
            利用 AkShare 接口，获取指定基金的历史日净值数据，包含净值日期、单位净值、日增长率等基础字段。
        2. 人工预测数据补充（可选）
            拉取历史数据后，支持手工输入“今日预测日增长率”。系统会自动根据上一日单位净值和输入的增长率推算出今日单位净值，补全其它缺省字段，
            并将预测行追加到数据表最后，并统一按“净值日期”升序排序。这样可纳入后续指标和信号分析，实现自动加人工混合推算。
        3. 指标构建与动量特征
            - 计算20日均线（MA20）、120日均线（MA120），用于判断中长期趋势。
            - 计算近10日“日增长率10日滑动均值”，反映短期动能变化。
            - 计算季度和年度的最大回撤，用于风险评估。
            - 统计近阶段“连续上涨天数”“连续下跌天数”及标签，有助于刻画超买超卖及惯性走势。
            - 探测短期内“连跌恐慌”现象（如数日内多次大跌）
        4. 历史分位与动态估值
            - 计算“低于历史价值百分比”：反映当前净值在全部历史中的相对估值水平（百分比分位）。
            - 计算“低于过去60日的价值百分比”：近两月的分位，减少历史极端值干扰，突出近期估值。
            - 动态设定高估/低估分位阈值，支持全局及窗口化判断。
    """
    print(f"正在拉取基金 {fund_code} 的全部历史净值 ……")
    try:
        df_unit = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        df_cum = ak.fund_open_fund_info_em(symbol=fund_code, indicator="累计净值走势")  # 单位净值在每次分红后会大幅回落
        df = df_unit.merge(df_cum[["净值日期", "累计净值"]], on="净值日期", how="left")
    except Exception as e:
        print(f"[错误] 拉取数据失败，原因：{e}")
        return pd.DataFrame()

    df["净值日期"] = pd.to_datetime(df["净值日期"], errors="coerce")
    df["单位净值"] = pd.to_numeric(df["单位净值"], errors="coerce")
    df["累计净值"] = pd.to_numeric(df["累计净值"], errors="coerce")
    df["日增长率"] = pd.to_numeric(df["日增长率"], errors="coerce")
    df = df.dropna(subset=["净值日期", "单位净值", "累计净值"])
    df["净值日期"] = pd.to_datetime(df["净值日期"])
    latest_date = df["净值日期"].max()
    print(f"数据已拉取，最新净值日期：{latest_date.date()}")
    df_sorted = df.sort_values("净值日期")
    last_nav = df_sorted["单位净值"].iloc[-1]  # 获取上一日的单位净值
    last_cum_nav = df_sorted["累计净值"].iloc[-1]
    if latest_date.date() >= datetime.datetime.now().date():
        print("数据已包含今天，无需预测。")
    else:
        while True:
            predict = input("请输入今天的预测日增长率(如-1=跌1%)，q键跳过：").strip()
            if predict == "q":
                print("选择不添加今日预测")
                break
            try:
                predict = float(predict)
                new_row = {
                    "净值日期": pd.to_datetime(datetime.datetime.now().date()),
                    "单位净值": round(last_nav * (1 + predict / 100), 4),
                    "累计净值": round(last_cum_nav * (1 + predict / 100), 4),
                    "日增长率": predict,
                }
                for col in df.columns:
                    if col not in new_row:
                        new_row[col] = np.nan
                df.loc[len(df)] = new_row
                break
            except Exception as error:
                print(error)
                print("请重新输入！")

    df["基金代码"] = fund_code
    df.sort_values(by="净值日期", inplace=True)
    df.reset_index(drop=True, inplace=True)
    df["MA20"] = df["累计净值"].rolling(20).mean()
    df["MA120"] = df["累计净值"].rolling(120).mean()
    pct_list_all = [np.nan]
    for i in range(1, len(df)):
        history = df["累计净值"].iloc[:i]
        pct = (history > df["累计净值"].iloc[i]).mean()
        pct_list_all.append(pct)

    df["低于历史价值百分比"] = pct_list_all
    # 动态阈值：随过去xx天滚动，默认从低到高排(从高估到低估)，低于历史价值百分比的第xx%分位值
    n = min(750, len(df)-2)  # 最多看 750 条；不够 750 时，有多少看多少；但少于 250 条时不给结果。一旦超过 250 条，会使用当时窗口里的全部有效数据。
    df["高估边界"] = df["低于历史价值百分比"].rolling(n, min_periods=250).quantile(0.1).shift(1)
    df["低估边界"] = df["低于历史价值百分比"].rolling(n, min_periods=250).quantile(0.9).shift(1)

    pct_list_60 = [None] * 60
    for i in range(60, len(df)):
        history = df["累计净值"].iloc[(i - 60):i]
        pct = (history > df["累计净值"].iloc[i]).mean()
        pct_list_60.append(pct)

    df["低于过去60日的价值百分比"] = pct_list_60
    df["日增长率"] = round(df["日增长率"] / 100, 4)
    df["日增长率10日滑动均值"] = df["日增长率"].rolling(10).mean()
    df["日增长率250日极端涨幅"] = df["日增长率"].rolling(250).quantile(0.98).shift(1)
    df["日增长率250日极端跌幅"] = df["日增长率"].rolling(250).quantile(0.01).shift(1)
    df["季度最大回撤"] = (df["累计净值"] - df["累计净值"].rolling(90).max()) / df["累计净值"].rolling(90).max()
    df["年最大回撤"] = (df["累计净值"] - df["累计净值"].rolling(260).max()) / df["累计净值"].rolling(260).max()
    df["连跌恐慌"] = (df["日增长率"] < -0.01).rolling(5).sum() >= 3

    up_days = []  # 连续上涨天数
    cnt = 0
    for rate in df["日增长率"]:
        if rate >= 0:
            cnt += 1
        else:
            cnt = 0
        up_days.append(cnt)
    df["连续上涨天数"] = up_days
    down_days = []  # 连续下跌天数
    cnt = 0
    for rate in df["日增长率"]:
        if rate < 0:
            cnt += 1
        else:
            cnt = 0
        down_days.append(cnt)
    df["连续下跌天数"] = down_days
    df["连涨连跌标签"] = df.apply(
        lambda row: f"连续上涨{row['连续上涨天数']}天" if row['连续上涨天数'] > 0 else f"连续下跌{row['连续下跌天数']}天",
        axis=1
    )

    return df


def nav_signal_analysis(df, fund_code):
    """
    1. 多元量化投资信号输出
        - 信号判据融合了价格与均线关系、分位估值、短期/长期动量、回撤风险、连涨连跌状态等多维因子。
        - 逐日自动打标以下场景（结合信号周期与累计涨跌）
        - “连续上涨/下跌天数”与信号配合，可以辅助规避追涨杀跌情绪、改进定投策略。
    2. 其它
        - 支持信号分段、阶段天数等统计，利于后续分析各信号状态持续时间及周期节奏。
        - 最终输出含历史全部关键指标与信号的DataFrame，为量化择时、估值分析以及定投、左侧配置等实战场景提供参考依据。
        - 保存CSV或HTML
    """
    df = df.copy()
    tag = []

    for i in range(len(df)):
        need_cols = [
            "日增长率250日极端涨幅",
            "日增长率250日极端跌幅",
            "MA20",
            "MA120",
            "高估边界",
            "低估边界",
            "年最大回撤",
        ]
        if df.loc[i, need_cols].isna().any():
            tag.append("数据不足")
            continue

        rate = df.loc[i, "日增长率"]
        up_extreme = df.loc[i, "日增长率250日极端涨幅"]
        down_extreme = df.loc[i, "日增长率250日极端跌幅"]
        price = df.loc[i, "累计净值"]
        ma20 = df.loc[i, "MA20"]
        ma120 = df.loc[i, "MA120"]
        p_under_total = df.loc[i, "低于历史价值百分比"]
        p_under_60d = df.loc[i, "低于过去60日的价值百分比"]
        mean_growth_10d = df.loc[i, "日增长率10日滑动均值"]
        qdraw = df.loc[i, "季度最大回撤"]
        hydraw = df.loc[i, "年最大回撤"]
        p_under_360d_high, p_under_360d_low = df.loc[i, "高估边界"], df.loc[i, "低估边界"]
        panic = df.loc[i, "连跌恐慌"]
        up_days = df.loc[i, "连续上涨天数"]
        down_days = df.loc[i, "连续下跌天数"]

        if (rate >= up_extreme) and (p_under_total <= p_under_360d_high) and (up_days >= 2):
            tag.append("极端大涨 且 高估")  # 建议落袋
        elif (rate <= down_extreme) and (p_under_total >= p_under_360d_low) and (down_days >= 2):
            tag.append("极端大跌 且 低估")  # 可分批加仓
        elif rate >= up_extreme:
            tag.append("极端大涨（未高估）")  # 警惕追高
        elif rate <= down_extreme:
            tag.append("极端大跌（未低估）")  # 耐心观望
        elif (price > ma20) and (ma20 > ma120) and (mean_growth_10d > 0.001) and (qdraw >= -0.05) and (p_under_total > p_under_360d_high):
            tag.append("良性上涨")  # 可适量增持
        elif (price > ma20) and (price > ma120) and (ma20 > ma120) and (mean_growth_10d < 0.001) and (qdraw < -0.05):
            tag.append("上涨尾声")  # 分批落袋
        elif (price > ma20) and (price > ma120) and (p_under_total <= p_under_360d_high):
            tag.append("高估")  # 卖
        elif (price < ma20) and (price < ma120) and (p_under_total <= p_under_360d_high):
            tag.append("动力不强, 但仍高估")  # 观望/减仓
        elif (price < ma20) and (price < ma120) and (p_under_total >= p_under_360d_low):
            tag.append("动力不强, 但低估")  # 不动
        elif (price > ma120) and (price < ma20):
            if p_under_total >= p_under_360d_low:
                tag.append("短期调整，长期低估")  # 反弹初期可关注
            else:
                tag.append("短期调整，估值中性")  # 观望
        elif (p_under_total >= p_under_360d_low) and (hydraw < -0.2) and (p_under_60d >= 0.85) and (down_days >= 3):
            tag.append("低估")  # 不动
        elif panic and (p_under_total >= p_under_360d_low):
            tag.append("连跌加速")  # 可关注/耐心等待

        else:
            tag.append("合理区间波动")

    df["信号"] = tag
    df["低于过去60日的价值百分比"] = pd.to_numeric(df["低于过去60日的价值百分比"], errors="coerce")
    df["信号标记"] = (df["信号"] != df["信号"].shift()).cumsum()  # 列整体向下移动一行, 判断变化, 标记同一信号连续出现的段落
    df["信号连续天数"] = df.groupby("信号标记").cumcount() + 1  # 统计同一段的第几天
    df = df.drop(columns=["信号标记"])
    df.sort_values(by="净值日期", ascending=False, inplace=True)
    df.reset_index(drop=True, inplace=True)
    folder = f"{df['基金名称'][0]}_基金代码{fund_code}"
    os.makedirs(folder, exist_ok=True)
    p1 = f"{df['基金名称'][0]}_基金代码{fund_code}/历史净值.csv"
    df.to_csv(path_or_buf=p1, encoding="utf-8", sep="\t", index=False, float_format="%.5f")
    p2 = f"{df['基金名称'][0]}_基金代码{fund_code}/历史净值.html"

    table_html = df.to_html(index=False, float_format=lambda x: f"{x:.5f}")
    full_html = f"""<!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>基金净值</title>
        <style>
            table {{
                border-collapse: collapse; /* 合并边框，确保表格美观 */
                width: 100%; /* 设置表格宽度 */
            }}
            th, td {{
            border: 1px solid #ddd; /* 添加边框 */
            padding: 8px; /* 设置内边距 */
            text-align: left; /* 文本左对齐 */
            white-space: nowrap; /* 防止文本换行 */
            }}
        </style>
    </head>
    <body>
    {table_html}
    </body>
    </html>"""

    with open(p2, encoding="utf-8", mode="w") as f:
        f.write(full_html)

    return df


def plot_fund_dashboard(df, fund_code):
    """
    自动可视化仪表盘，分为4块：
        1. 全历史净值趋势
        2. 全历史分位+高估/低估线
        3. 近250天净值趋势
        4. 近250天分位+高低估线
    """
    df_plot = df.sort_values("净值日期")
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(nrows=2, ncols=2, figsize=(22, 8), constrained_layout=True)
    # 历史: 累计净值
    ax1.plot(df_plot["净值日期"], df_plot["累计净值"], label="Unit Net Value", color="blue", linewidth=2)
    ax1.set_xlabel("Date")
    ax1.set_ylabel("Unit Net Value")
    ax1.set_title("Full History Trend of Unit Net Value")
    ax1.tick_params(axis="x", rotation=40)
    ax1.margins(y=0.1)
    ax1.legend(loc="upper left")
    # 历史: 低于历史价值百分比+高估低估边界
    ax2.plot(df_plot["净值日期"], df_plot["低于历史价值百分比"], label="Percentage below historical value", color="blue", linewidth=2)
    ax2.plot(df_plot["净值日期"], df_plot["高估边界"], label="Overvaluation Boundary", color="red", linestyle="--", linewidth=1.8)
    ax2.plot(df_plot["净值日期"], df_plot["低估边界"], label="Undervaluation Boundary", color="green", linestyle="--", linewidth=1.8)
    ax2.set_xlabel("Date")
    ax2.set_ylabel("Percentile")
    ax2.set_title("Over/Under-Value Boundary")
    ax2.tick_params(axis="x", rotation=40)
    ax2.margins(y=0.1)
    ax2.legend(loc="lower right", fontsize=8, framealpha=0.8)
    ax2.invert_yaxis()
    # 过去250天: 累计净值
    df_plot = df_plot.tail(250)
    ax3.plot(df_plot["净值日期"], df_plot["累计净值"], label="Unit Net Value", color="blue", linewidth=2)
    ax3.set_xlabel("Date")
    ax3.set_ylabel("Unit Net Value")
    ax3.set_title("Past 250 Days Trend of Unit Net Value")
    ax3.tick_params(axis="x", rotation=40)
    ax3.margins(y=0.1)
    ax3.legend(loc="upper left")
    # 过去250天: 低于历史价值百分比+高估低估边界
    ax4.plot(df_plot["净值日期"], df_plot["低于历史价值百分比"], label="Percentage below historical value", color="blue", linewidth=2)
    ax4.plot(df_plot["净值日期"], df_plot["高估边界"], label="Overvaluation Boundary", color="red", linestyle="--", linewidth=1.8)
    ax4.plot(df_plot["净值日期"], df_plot["低估边界"], label="Undervaluation Boundary", color="green", linestyle="--", linewidth=1.8)
    ax4.set_xlabel("Date")
    ax4.set_ylabel("Percentile")
    ax4.set_title("Over/Under-Value Boundary")
    ax4.tick_params(axis="x", rotation=40)
    ax4.margins(y=0.1)
    # ax4.legend(loc="lower right", fontsize=8, framealpha=0.8)
    ax4.invert_yaxis()
    fund_name = df["基金名称"].iloc[0]
    folder = f"{fund_name}_基金代码{fund_code}"
    p1 = os.path.join(folder, "历史趋势.png")
    plt.savefig(p1, dpi=200)
    plt.show()


if __name__ == "__main__":
    pd.set_option("display.max_columns", None)
    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", 1000)

    file_map = {
        "1": "基金代码名单_持仓.txt",
        "2": "基金代码名单_临时.txt",
        "3": "基金代码名单_备选.txt",
        "4": "基金代码名单_wjx.txt",
        "5": "基金代码名单_行业.txt",
    }

    for k, v in file_map.items():
        print(f"{k}. {v.replace('.txt', '')}")

    while True:
        choice = input(">> 请输入序号 (1~5)：").strip()
        if choice in file_map:
            path = file_map[choice]
            break
        print("输入有误！")

    code_list = get_fund_code(path=path)
    for fund_code in code_list:
        try:
            (basic_intro, fund_name) = basic_profile(fund_code)
            print(f"\n准备处理：{fund_name}（{fund_code}）")
        except Exception as error:
            print(error)
            print("基金代码错误, 已自动跳过！")
            time.sleep(3)
            continue

        skip = False

        while True:
            go = input("继续？c键=继续   q键=跳过\n输入：").strip().lower()
            if go == "q":
                print("已跳过！")
                skip = True
                break
            elif go == "c":
                break
            else:
                print("输入有误，重新输入！")
        if skip:
            continue

        holding = hold_base(fund_code, fund_name)
        print(holding[:10][["季度", "股票代码", "股票名称", "占净值比例"]])

        nav_df = fetch_all_nav(fund_code)
        nav_df["基金名称"] = basic_intro["基金名称"]
        nav_df_ana = nav_signal_analysis(df=nav_df, fund_code=fund_code)
        print(nav_df_ana[["基金名称", "基金代码", "净值日期", "单位净值", "日增长率", "低于历史价值百分比", "信号", "信号连续天数", "连涨连跌标签"]].sort_values(ascending=False, by="净值日期").head(n=21).reset_index(drop=True))

        plot_fund_dashboard(df=nav_df, fund_code=fund_code)

        time.sleep(2)
