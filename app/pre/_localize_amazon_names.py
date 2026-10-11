#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_localize_amazon_names.py —— Amazon 场景商品名中文化

Amazon US Electronics 元数据只有英文标题，没有中文目录可对译，因此这里用
「领域词典 + 结构化重写」把标题渲染成中文商品名，而不是逐字机器翻译：

    1. 词表替换：品牌保留原文（Apple→苹果、Samsung→三星…），品类/属性按
       消费电子领域惯用译法（HDMI 线、移动电源、内存卡、保护壳…）
    2. 规格归一：容量/长度/接口/速率按中文习惯重排（"64GB"→"64GB"、
       "6 feet"→"6 英尺"、"Male to Male"→"公对公"）
    3. 型号/ASIN 保留在括号里，保证可追溯到原始数据
    4. 没命中任何词条的标题如实回退英文原文（不编造），并计入覆盖率

产物：entities.dat 第 5 列改中文；namecoverage.json 记录覆盖率与回退数。
原始英文标题备份到 downloads/amazon_entities_en.bak。
"""
import html
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "amazon")
# 备份放在工程根的 downloads/ 下（与 _fetch_posters 等的下载目录同源），
# 不随包转发；注意 ROOT 是 app/，故上溯两级才是工程根。
BAK = os.path.join(os.path.dirname(ROOT), "downloads", "amazon_entities_en.bak")

WS = re.compile(r"\s+")
TAG = re.compile(r"<[^>]{1,60}>")
# 末尾的 "(ASIN)" 或裸 ASIN 记法
TRAIL_ASIN = re.compile(r"\s*\(([A-Z0-9]{10})\)\s*$")
BARE_ASIN = re.compile(r"^([A-Z0-9]{10})$")

# ---- 品牌（保留识别度：中外通用的直接给中文/通用叫法） ----
BRANDS = {
    "apple": "苹果", "samsung": "三星", "sandisk": "闪迪", "sony": "索尼",
    "panasonic": "松下", "toshiba": "东芝", "logitech": "罗技", "belkin": "贝尔金",
    "amazonbasics": "AmazonBasics", "kindle": "Kindle", "google": "谷歌",
    "microsoft": "微软", "hp": "惠普", "dell": "戴尔", "lenovo": "联想",
    "asus": "华硕", "acer": "宏碁", "intel": "英特尔", "amd": "AMD",
    "nvidia": "英伟达", "kingston": "金士顿", "crucial": "英睿达",
    "corsair": "海盗船", "seagate": "希捷", "western digital": "西部数据",
    "wd": "西部数据", "transcend": "创见", "lexar": "雷克沙", "pny": "必恩威",
    "verbatim": "威宝", "maxell": "麦克赛尔", "tdk": "TDK", "philips": "飞利浦",
    "anker": "Anker", "belk in": "贝尔金", "jabra": "捷波朗", "plantronics": "缤特力",
    "bose": "Bose", "beats": "Beats", "sennheiser": "森海塞尔", "audio-technica": "铁三角",
    "shure": "舒尔", "garmin": "佳明", "tomtom": "TomTom", "magellan": "麦哲伦",
    "rand mcnally": "Rand McNally", "polaroid": "宝丽来", "canon": "佳能",
    "nikon": "尼康", "olympus": "奥林巴斯", "fujifilm": "富士", "kodak": "柯达",
    "mediabridge": "Mediabridge", "bluerigger": "BlueRigger", "powergen": "PowerGen",
    "snugg": "Snugg", "tech armor": "Tech Armor", "dvi gear": "DVI Gear",
    "startech": "StarTech", "iogear": "IOGEAR", "tripp lite": "Tripp Lite",
    "cables to go": "Cables To Go", "rocketfish": "Rocketfish", "monster": "魔声",
    "norton": "诺顿", "mcafee": "迈克菲", "kaspersky": "卡巴斯基", "adobe": "Adobe",
    "corel": "Corel", "rosetta stone": "Rosetta Stone", "kelby": "Kelby",
    "barnes & noble": "巴诺", "nook": "Nook", "netgear": "网件", "linksys": "领势",
    "d-link": "友讯", "tp-link": "普联", "cisco": "思科", "buffalo": "巴比禄",
    "asus tek": "华硕", "htc": "HTC", "motorola": "摩托罗拉", "lg": "LG",
    "nokia": "诺基亚", "blackberry": "黑莓", "palm": "Palm", "garmin ltd": "佳明",
}

# ---- 品类/属性词条（长词优先，避免 "cable" 抢先吃掉 "hdmi cable"） ----
TERMS = {
    # 线材/接口
    "hdmi cable": "HDMI 线", "hdmi": "HDMI", "ethernet cable": "网线",
    "ethernet": "以太网", "usb cable": "USB 线", "usb": "USB",
    "dvi": "DVI", "vga": "VGA", "displayport": "DisplayPort", "thunderbolt": "雷雳",
    "male to male": "公对公", "male to female": "公对母", "female to female": "母对母",
    "male": "公头", "female": "母头", "adapter": "转接头", "adaptor": "转接头",
    "converter": "转换器", "splitter": "分线器", "extension": "延长线",
    "cable": "线", "cord": "线", "connector": "连接器", "plug": "插头",
    "jack": "插孔", "port": "接口", "hub": "集线器", "dongle": "加密狗",
    # 存储
    "microsdhc": "microSDHC", "microsdxc": "microSDXC", "microsd": "microSD",
    "sdhc": "SDHC", "sdxc": "SDXC", "sd card": "SD 卡", "memory card": "存储卡",
    "flash memory": "闪存", "flash drive": "U 盘", "usb flash drive": "U 盘",
    "memory": "内存", "ram": "内存", "sodimm": "SO-DIMM", "dimm": "DIMM",
    "hard drive": "硬盘", "external hard drive": "移动硬盘", "ssd": "固态硬盘",
    "solid state drive": "固态硬盘", "external": "外置", "internal": "内置",
    "portable": "便携", "drive": "驱动器", "storage": "存储", "capacity": "容量",
    # 影音
    "headphone": "耳机", "headphones": "耳机", "earphone": "耳机",
    "earbuds": "入耳式耳机", "earbud": "入耳式耳机", "headset": "耳麦",
    "speaker": "音箱", "soundbar": "回音壁", "subwoofer": "低音炮",
    "microphone": "麦克风", "mic": "麦克风", "streaming media player": "流媒体播放器",
    "media player": "媒体播放器", "player": "播放器", "mp3 player": "MP3 播放器",
    "amplifier": "功放", "receiver": "接收机", "audio": "音频", "stereo": "立体声",
    "wireless": "无线", "bluetooth": "蓝牙", "noise cancelling": "主动降噪",
    "noise canceling": "主动降噪", "in-line volume": "线控音量", "surround": "环绕",
    # 充电/电源
    "charger": "充电器", "car charger": "车载充电器", "wall charger": "墙充",
    "power adapter": "电源适配器", "power supply": "电源", "battery": "电池",
    "rechargeable": "可充电", "power bank": "移动电源", "power kit": "电源套装",
    "amps": "安培", "amperage": "电流", "watt": "瓦", "voltage": "电压",
    # 显示/图像
    "screen protector": "屏幕保护膜", "screen": "屏幕", "monitor": "显示器",
    "projector": "投影仪", "webcam": "摄像头", "camera": "相机", "lens": "镜头",
    "tripod": "三脚架", "flash": "闪光灯", "privacy": "防窥", "matte": "磨砂",
    "anti-glare": "防眩光", "resolution": "分辨率", "1080p": "1080p", "720p": "720p",
    # 电脑外设
    "keyboard": "键盘", "mouse": "鼠标", "mouse pad": "鼠标垫", "trackpad": "触控板",
    "printer": "打印机", "ink": "墨水", "toner": "碳粉", "scanner": "扫描仪",
    "tablet": "平板", "laptop": "笔记本", "notebook": "笔记本", "desktop": "台式机",
    "case": "保护壳", "cover": "保护套", "sleeve": "内胆包", "bag": "包",
    "stand": "支架", "mount": "支架", "holder": "支架", "dock": "底座",
    "keyboard cover": "键盘保护膜", "stylus": "触控笔", "pen": "笔",
    "remote control": "遥控器", "remote": "遥控器", "gaming": "游戏",
    "gamepad": "游戏手柄", "controller": "手柄", "joystick": "摇杆",
    # 网络
    "router": "路由器", "modem": "调制解调器", "switch": "交换机",
    "access point": "无线接入点", "network": "网络", "wifi": "Wi-Fi",
    "wi-fi": "Wi-Fi", "antenna": "天线", "signal": "信号", "booster": "放大器",
    # 颜色/材质/外观
    "black": "黑色", "white": "白色", "silver": "银色", "gray": "灰色",
    "grey": "灰色", "gold": "金色", "blue": "蓝色", "red": "红色",
    "green": "绿色", "orange": "橙色", "pink": "粉色", "purple": "紫色",
    "carbon": "碳纤维", "leather": "真皮", "silicone": "硅胶", "metal": "金属",
    "aluminum": "铝合金", "aluminium": "铝合金", "plastic": "塑料",
    "stainless steel": "不锈钢", "steel": "钢", "rubber": "橡胶",
    "matte black": "磨砂黑", "glossy": "亮面",
    # 规格度量
    "inch": "英寸", "inches": "英寸", "feet": "英尺", "foot": "英尺",
    "meter": "米", "meters": "米", "metre": "米", "mm": "毫米",
    "cm": "厘米", "gb": "GB", "tb": "TB", "mb": "MB", "kg": "千克",
    "lb": "磅", "lbs": "磅", "oz": "盎司", "high-speed": "高速",
    "high speed": "高速", "speed": "速度", "class": "Class", "ultra": "Ultra",
    # 通用修饰
    "new": "全新", "latest": "最新", "professional": "专业", "pro": "专业版",
    "deluxe": "豪华版", "premium": "尊贵版", "standard": "标准", "basic": "基础",
    "universal": "通用", "compatible": "兼容", "replacement": "替换",
    "with": "含", "and": "和", "for": "适用", "to": "至", "in": "在",
    "supports": "支持", "support": "支持", "designed for": "专为",
    "includes": "含", "including": "含", "built-in": "内置", "built in": "内置",
    "combo": "套装", "kit": "套装", "set": "套装", "pack": "装",
    "packaging": "包装", "retail": "零售版", "lifetime": "终身",
    "guarantee": "质保", "warranty": "保修", "series": "系列",
    "edition": "版", "version": "版", "model": "型号", "type": "型",
    "digital": "数码", "organizer": "收纳", "messenger": "通勤",
    "training": "培训", "crash course": "速成课程", "mastering": "精通",
    "quote": "语录", "truck": "卡车", "gps": "GPS", "navigation": "导航",
    "navigator": "导航仪", "intelliroute": "智能路线", "dvd": "DVD",
    "cd": "CD", "blu-ray": "蓝光", "software": "软件", "antivirus": "杀毒",
    "privacy screen": "防窥膜", "degree": "度", "way": "向",
    "flip": "翻盖", "fold": "折叠", "slim": "超薄", "thin": "薄",
    "mini": "迷你", "micro": "微型", "nano": "微型", "compact": "紧凑",
    "dual": "双", "triple": "三", "quad": "四", "single": "单",
    "dual-band": "双频", "dual band": "双频", "band": "频段",
    "ethernet support": "支持以太网", "3d": "3D", "4k": "4K",
    # 残留高频词补全（词表未覆盖时会以英文原样出现）
    "return": "回传", "audio return": "音频回传", "arc": "ARC",
    "up to": "最高", "up": "最高", "to": "至", "in-ear": "入耳式",
    "in ear": "入耳式", "on-ear": "压耳式", "over-ear": "头戴式",
    "device": "设备", "devices": "设备", "tablet pc": "平板电脑",
    "smartphone": "智能手机", "cell phone": "手机", "phone": "手机",
    "ipad": "iPad", "iphone": "iPhone", "ipod": "iPod", "imac": "iMac",
    "macbook": "MacBook", "android": "安卓", "ios": "iOS", "windows": "Windows",
    "blackberry": "黑莓", "droid": "Droid", "nexus": "Nexus", "galaxy": "Galaxy",
    "towards": "朝向", "rear": "后置", "front": "前置", "side": "侧边",
    "top": "顶部", "bottom": "底部", "left": "左", "right": "右",
    "indoor": "室内", "outdoor": "户外", "waterproof": "防水",
    "water resistant": "防水", "shockproof": "防摔", "durable": "耐用",
    "flexible": "柔性", "adjustable": "可调", "foldable": "可折叠",
    "rotating": "旋转", "swivel": "旋转", "tilt": "俯仰", "angle": "角度",
    "charge": "充电", "charging": "充电", "sync": "同步", "data": "数据",
    "transfer": "传输", "read": "读取", "write": "写入", "rate": "速率",
    "gbps": "Gbps", "mbps": "Mbps", "hz": "Hz", "khz": "kHz", "mhz": "MHz",
    "ghz": "GHz", "volt": "伏", "volts": "伏", "amp": "安", "amps": "安",
    "watts": "瓦", "watt": "瓦", "ampere": "安培", "mah": "mAh",
    "piece": "件", "pieces": "件", "pcs": "件", "count": "支装",
    "pair": "对", "pairs": "对", "each": "每个", "per": "每",
    "free": "免费", "bonus": "附赠", "extra": "额外", "additional": "额外",
    "original": "原装", "oem": "原厂", "genuine": "正品", "authentic": "正品",
    "refurbished": "翻新", "used": "二手", "open box": "开箱",
    "gift": "礼品", "holiday": "节日", "travel": "旅行", "home": "家用",
    "office": "办公", "school": "校园", "business": "商务", "student": "学生",
    "kids": "儿童", "kid": "儿童", "baby": "婴儿", "pet": "宠物",
    "sport": "运动", "fitness": "健身", "music": "音乐", "movie": "电影",
    "video": "视频", "photo": "照片", "picture": "图片", "image": "图像",
    "game": "游戏", "games": "游戏", "gaming": "游戏", "console": "主机",
    "xbox": "Xbox", "playstation": "PlayStation", "wii": "Wii", "nintendo": "任天堂",
    "wired": "有线", "wireless": "无线", "cordless": "无绳", "handsfree": "免提",
    "noise": "噪声", "isolation": "隔音", "bass": "低音",
    "treble": "高音", "volume": "音量", "control": "控制", "controls": "控制",
    "button": "按钮", "buttons": "按钮", "touch": "触控", "touchscreen": "触控屏",
    "led": "LED", "lcd": "LCD", "oled": "OLED", "backlit": "背光",
    "rechargeable battery": "可充电电池", "aa": "AA", "aaa": "AAA",
    "solar": "太阳能", "usb-c": "USB-C", "usb c": "USB-C", "type-c": "Type-C",
    "micro usb": "Micro USB", "mini usb": "Mini USB", "lightning": "Lightning",
    "wall": "墙插", "car": "车载", "vehicle": "车载", "auto": "车载",
    "desk": "桌面", "desktop stand": "桌面支架", "wall mount": "壁挂支架",
    "ceiling": "吸顶", "floor": "落地", "table": "桌面",
    # 连接词/介词（中文里通常省略，避免出现"在黑色""By xxx"这种夹生）
    "by": "", "at": "", "of": "", "on": "", "the": "", "a": "", "an": "",
    "in": "", "as": "", "or": "", "your": "", "you": "", "its": "",
    "fits": "适配", "fit": "适配", "featuring": "配备", "feature": "特性",
    "nylon": "尼龙", "canvas": "帆布", "suede": "绒面", "velvet": "天鹅绒",
    "e-reader": "电纸书", "ereader": "电纸书", "reader": "阅读器",
    "license plate": "车牌", "plate": "牌照", "embossed": "压纹",
    "enamel": "珐琅", "crystal": "水晶", "pearl": "珍珠", "diamond": "钻石",
    "reflective": "反光", "glitter": "闪粉", "patterned": "印花",
    "printed": "印花", "floral": "花卉", "striped": "条纹", "plaid": "格纹",
    "solid": "纯色", "clear": "透明", "transparent": "透明", "opaque": "不透明",
}

# 组合替换：先长后短
_TERM_KEYS = sorted(TERMS.keys(), key=len, reverse=True)
_BRAND_KEYS = sorted(BRANDS.keys(), key=len, reverse=True)


def _word_re(key):
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(key) + r"(?![A-Za-z0-9])", re.I)


# 正则只编译一次：476k 行 × ~700 词条，逐行 re.compile 会慢到分钟级
_BRAND_RE = [(b, _word_re(b), BRANDS[b]) for b in _BRAND_KEYS]
_TERM_RE = [(k, _word_re(k), TERMS[k]) for k in _TERM_KEYS]


def clean(t):
    t = TAG.sub(" ", t or "")
    t = html.unescape(t)
    return WS.sub(" ", t).strip()


def translate(title):
    """英文标题 → 中文商品名；返回 (中文名, 是否命中文档词条)。"""
    t = clean(title)
    if not t:
        return "", False
    hit = False

    # 1) 抽出末尾的 ASIN / 型号，正文单独处理
    trail = ""
    m = TRAIL_ASIN.search(t)
    if m:
        trail = m.group(1)
        t = t[: m.start()].strip()

    # 0) 预筛：只对标题里实际出现首词的词条跑正则（476k 行 × 700 词条的全量
    #    匹配是分钟级；按小写首词建桶后绝大多数行只命中个位数词条）
    low = t.lower()
    tokens = set(re.findall(r"[a-z0-9][a-z0-9\.\-/+]*", low)) if low else set()
    for _b, pat, repl in _BRAND_RE:
        if _b.split()[0] not in tokens and _b not in low:
            continue
        if pat.search(t):
            t = pat.sub(lambda _m, r=repl: "\x00" + r + "\x00", t)
            hit = True

    # 3) 领域词条替换（长词优先；占位符隔离，避免重复命中）
    for _k, pat, repl in _TERM_RE:
        if _k.split()[0] not in tokens:
            continue
        if pat.search(t):
            if repl == "":                       # 虚词/介词：连同边界空格一并抹掉
                t = pat.sub("", t)
            else:
                t = pat.sub(lambda _m, r=repl: "\x00" + r + "\x00", t)
            hit = True

    # 4) 移除占位符。占位符保留了原文的空格边界：中文与英文/数字相邻处补一个
    #    窄空格，避免 "KindleFire"/"闪迪Ultra" 这种粘词。
    def unph(m):
        s = m.group(0)
        lead = " " if s.startswith(" ") else ""
        tail = " " if s.endswith(" ") else ""
        return lead + m.group(1) + tail

    out = re.sub(r" ?\x00([^\x00]*)\x00 ?", unph, t)
    out = out.replace("\x00", "")
    out = WS.sub(" ", out).strip()

    # 5) 标点规整：半角括号→全角（中文语境），去中文字间/中英间多余空格
    out = re.sub(r"\s*\(\s*", "（", out)
    out = re.sub(r"\s*\)", "）", out)
    out = re.sub(r"\s+（", "（", out)
    out = re.sub(r"\s*-\s*$", "", out)
    out = re.sub(r"\s+-\s+", " - ", out)
    out = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", out)      # 中 中
    out = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[0-9A-Za-z])", "", out)          # 中 英
    out = re.sub(r"(?<=[0-9A-Za-z])\s+(?=[\u4e00-\u9fff])", " ", out)         # 英 中
    out = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[（])", "", out)
    out = re.sub(r"\s*[,;]\s*", "，", out)
    out = re.sub(r"，{2,}", "，", out)
    out = re.sub(r"（\s+", "（", out)
    out = re.sub(r"\s+）", "）", out)
    out = re.sub(r"^\s*[，\-—]\s*", "", out).strip()
    out = re.sub(r"\s{2,}", " ", out)

    if trail:
        out = f"{out}（{trail}）" if out else trail
    return out, hit


def localize_entities():
    ent = os.path.join(DATA, "entities.dat")
    if not os.path.exists(ent):
        sys.exit(f"缺少 {ent}")
    os.makedirs(os.path.dirname(BAK), exist_ok=True)
    if not os.path.exists(BAK):
        shutil.copyfile(ent, BAK)
        print("原始英文台账备份 →", os.path.normpath(BAK))

    n = hit = bare = 0
    tmp = ent + ".tmp"
    with open(ent, encoding="utf-8") as f, open(tmp, "w", encoding="utf-8", newline="\n") as out:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 5:
                out.write(line)
                continue
            n += 1
            raw = p[4]
            # 去掉此前 enrich 可能加过的 "(ASIN)" 后缀，拿回纯标题/纯 ASIN
            m = TRAIL_ASIN.search(raw)
            asin = None
            if m:
                asin = m.group(1)
            else:
                b = BARE_ASIN.match(raw.strip())
                if b:
                    asin = b.group(1)
            zh, ok = translate(raw)
            if ok:
                hit += 1
            if asin and (not zh or zh == asin):
                # 没有标题的商品：如实标为「商品 ASIN」，不编造品名
                zh = f"商品 {asin}"
                bare += 1
            p[4] = zh or raw
            out.write("\t".join(p) + "\n")
    os.replace(tmp, ent)
    return n, hit, bare


def main():
    n, hit, bare = localize_entities()
    cov = {
        "entities": n,
        "entities_with_cn_name": hit,
        "entities_bare_asin": bare,
        "coverage": round(hit / n * 100, 1) if n else 0.0,
        "method": "领域词典 + 结构化重写（品牌/品类/规格），未命中词条的回退英文原文",
        "source": "Amazon Electronics 商品元数据（snap.stanford.edu，英文标题）",
        "note": "Amazon US 目录无中文名，中文名由本项目词典渲染；ASIN 一律保留在括号内可溯源",
    }
    with open(os.path.join(DATA, "namecoverage.json"), "w", encoding="utf-8") as f:
        json.dump(cov, f, ensure_ascii=False, indent=1)
    print(f"entities.dat 中文化完成：{hit}/{n} 命中词条（{hit / n * 100:.1f}%），"
          f"无标题 ASIN {bare} 条")
    print("namecoverage.json 已更新")


if __name__ == "__main__":
    main()
