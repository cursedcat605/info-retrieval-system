"""选调生去向字段抽取。

从海报 OCR 文本中抽取下列字段（用户指定的 7 个字段 + 2 个辅助字段）：

===============  =========================================================
``name``         选调生姓名
``cohort``       选调生届别（如 ``2021届``）
``college``      所属学院
``major``        专业
``destination_org``  选调去的单位
``city``         城市
``position``     岗位
``province``     省份（单位所在省，由去向文本得出）
``degree``       学历（附加）
===============  =========================================================

抽取原则
--------
1. **只做抽取，不做编造**：任一字段无法从文本中可靠得到时保留 ``None``，
   绝不用"推测值"填充。宁可字段为空，也不产生错误数据。
2. **白名单优先**：学院、专业先与 :mod:`src.nlp.gazetteer` 的已知词表精确匹配，
   再退化到正则，兼顾精度与召回。
3. **证据留痕**：每条记录都带 ``evidence``（命中的原始文本），
   便于人工复核与检索结果高亮。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Iterable, List, Optional, Sequence, Tuple

from ..preprocess.cleaner import clean_lines, is_footer_line, is_noise_line, normalize_text
from .gazetteer import (
    CITY_WHITELIST,
    COLLEGE_SUFFIXES,
    DEGREE_KEYWORDS,
    DESTINATION_LEADS,
    KNOWN_COLLEGES,
    MAJOR_WHITELIST,
    MUNICIPALITIES,
    NAME_LABELS,
    NAME_STOPWORDS,
    ORG_SUFFIXES,
    POSITION_KEYWORDS,
    PROVINCE_ALIASES,
    PROVINCE_FULL,
    SURNAMES,
    SURNAMES_TWO_CHAR,
)

# --------------------------------------------------------------------------- #
# 预编译正则
# --------------------------------------------------------------------------- #
_LEADS_SORTED = tuple(sorted(DESTINATION_LEADS, key=len, reverse=True))
_LABELS_SORTED = tuple(sorted(NAME_LABELS, key=len, reverse=True))
_ORG_SUFFIX_SORTED = tuple(sorted(ORG_SUFFIXES, key=len, reverse=True))
_COLLEGE_SUFFIX_SORTED = tuple(sorted((s for s in COLLEGE_SUFFIXES if s != "系"), key=len, reverse=True))

RE_NAME_LABEL = re.compile(
    r"(?:" + "|".join(_LABELS_SORTED) + r")\s*[:：]?\s*([\u4e00-\u9fa5]{2,4})"
)
RE_NAME_LINE = re.compile(r"^[\u4e00-\u9fa5]{2,4}$")
RE_NAME_INLINE = re.compile(
    r"^([\u4e00-\u9fa5]{2,4})(?=[\s,，:：]|(?:学院|学部|书院|大学|专业|届|级|就职|任职|考入|考取))"
)
RE_NAME_TITLE = re.compile(r"([\u4e00-\u9fa5]{2,4})(?:同学|校友|师姐|师兄|学姐|学长)")

RE_COLLEGE = re.compile(r"([\u4e00-\u9fa5]{2,10}(?:" + "|".join(_COLLEGE_SUFFIX_SORTED) + r"))")
RE_DEPARTMENT = re.compile(r"([\u4e00-\u9fa5]{2,6}系)")
RE_MAJOR_EXPLICIT = re.compile(r"([\u4e00-\u9fa5]{2,12}?)专业")

#: 届别：``(正则, 单位)``。单位用于区分"2024届"（毕业届）与"2021级"（入学年级）。
RE_COHORT_DEFS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(20\d{2}|19\d{2})\s*届"), "届"),
    (re.compile(r"(20\d{2}|19\d{2})\s*年\s*毕业"), "届"),
    (re.compile(r"(20\d{2}|19\d{2})\s*级"), "级"),
    (re.compile(r"(20\d{2}|19\d{2})\s*年\s*入学"), "级"),
)

RE_DESTINATION = re.compile(
    r"(?:" + "|".join(re.escape(lead) for lead in _LEADS_SORTED) + r")\s*[:：]?\s*"
    r"([^\s,;:、。；：\\|]{2,40})"
)
RE_ORG_TAIL = re.compile(r"([\u4e00-\u9fa5]{2,20}(?:" + "|".join(_ORG_SUFFIX_SORTED) + r"))")
RE_CITY = re.compile(r"([\u4e00-\u9fa5]{2,4}?)(市|自治州|地区|盟)")
#: 省份名用白名单拼接，避免"…业硕士研究生湖南省…"这类越界匹配
_PROVINCE_SORTED = tuple(sorted(PROVINCE_FULL, key=len, reverse=True))
RE_PROVINCE = re.compile(r"(" + "|".join(re.escape(p) for p in _PROVINCE_SORTED) + r")")
RE_POSITION = re.compile(
    r"(?:任|担任|岗位\s*[:：]?|职务\s*[:：]?|职位\s*[:：]?)\s*([\u4e00-\u9fa5A-Za-z0-9]{2,20})"
)
#: "XX书记助理（不定职级）"这类基层职务，没有"任/担任"引导词
RE_POSITION_ASSIST = re.compile(
    r"([\u4e00-\u9fa5]{0,12}(?:书记助理|主任助理|镇长助理|乡长助理|县长助理|"
    r"驻村工作队员|驻村队员|工作队员|主任科员|科员))"
)

#: 人名里绝不可能出现的语素（用于过滤"纪检监察""招生就业"这类误判）
_NAME_FORBIDDEN: Tuple[str, ...] = (
    "学院", "学部", "书院", "大学", "学校", "专业", "研究", "中心", "工程", "技术",
    "就业", "招生", "招聘", "工作", "委员", "纪委", "监委", "监察", "办公", "管理",
    "组织", "宣传", "统战", "政法", "部门", "单位", "考试", "公告", "通知", "分享",
    "经验", "交流", "沙龙", "讲座", "论坛", "就业处", "培训", "服务", "发展", "建设",
)
#: 紧跟在这些后缀之前的，是机构名 / 专业名的“开头部分”，不是人名：
#: ``经济`` + ``学院`` → 经济不是人名；``金融`` + ``专业`` → 金融不是人名。
#: （“山”“宁”“甘”“广”“云”“经”“金”“计”都是姓氏，光靠姓氏判断会漏拦）
_NAME_ORG_FOLLOWERS: Tuple[str, ...] = (
    "学院", "学部", "书院", "大学", "学校", "专业", "系", "中学", "小学",
)
#: 紧跟行政区划后缀的是地名而非人名：``汤原`` + ``县`` → 汤原不是人名
_NAME_ADMIN_FOLLOWERS: str = "省市区县旗镇乡村街"
#: 视作"身份"而非"岗位"的词，不单独作为岗位输出
_POSITION_WEAK = {"选调生", "定向选调生", "公务员", "参公", "参照公务员法管理"}

#: 不可能出现在学院名里的词
_COLLEGE_BANNED = (
    "分享会", "交流会", "经验分享", "沙龙", "座谈会", "讲座", "论坛", "大会",
    "公告", "通知", "海报", "活动", "报告", "招聘", "宣讲", "研究生招生", "就业指导",
)
#: 城市名的黑名单（避免 "市区" 之类）
_CITY_BANNED = {"城市", "全市", "市区", "附近", "某市", "本市", "该市", "州市", "地市", "都市", "州市区"}
#: 正则从词中间起匹配时会被带进城市名的"前缀碎片"（前面那个字段的尾巴）
#: 例：``研究生重庆市`` → ``究生``、``四级主任科员崇左市`` → ``科员``
_CITY_PREFIX_NOISE: Tuple[str, ...] = (
    "研究生", "究生", "本科生", "科员", "主任", "硕士", "学士", "博士", "专业",
    "届", "级", "生", "员", "干", "部",
)


def _normalize_city_base(base: str, suffix: str) -> Optional[str]:
    """把 ``RE_CITY`` 抓到的城市名基干修干净；修不干净则返回 ``None``。

    两种污染：① 前面字段的尾巴被带进来（``博孜``+``天津``、``究生``+``重庆``）；
    ② 前缀是噪声词片段（``科员``+``崇左``）。
    """
    for noise in _CITY_PREFIX_NOISE:
        if base.startswith(noise) and len(base) > len(noise):
            base = base[len(noise):]
            break
    # 逐步裁掉前缀，直到"基干 + 后缀"是一个已知城市 / 直辖市
    for cut in range(0, max(len(base) - 1, 0)):
        trimmed = base[cut:]
        if len(trimmed) < 2:
            break
        if trimmed + suffix in MUNICIPALITIES or trimmed in CITY_WHITELIST:
            return trimmed
    return base if (base in CITY_WHITELIST or len(base) >= 3) else None

#: 行政区划标记（用于定位"去向行"）
_LOCATION_MARKERS: Tuple[str, ...] = (
    "省", "自治区", "特别行政区", "市", "自治州", "地区", "盟",
    "县", "自治县", "区", "旗", "镇", "乡", "街道", "村", "社区",
)
#: 机构标记（用于定位"去向行"）。注意不能放入互相包含的词（如同时放"总支"和"党总支"），
#: 否则同一处会被重复计分。
_ORG_MARKERS: Tuple[str, ...] = tuple(ORG_SUFFIXES) + (
    "组", "支部", "总支", "党委", "队", "站", "所", "科", "室", "校",
)
#: 即使没有"任/担任"也算作完整职务词的结尾
_POSITION_TAILS: Tuple[str, ...] = ("助理", "队员", "科员", "主任", "书记", "职员", "干事", "专干")

#: 从"去处行"里切掉职务部分："…马杨村党总支书记助理（不定职级）" → "…马杨村"
_ADMIN_TAIL_CUT = re.compile(
    r"(?:党总支|党支部|党组|党委|村委会|居委会)?(?:常务|副)?"
    r"(?:书记|主任|助理|科员|队员|干事|专干|镇长|乡长|巡视员|调研员|专员|管培生)"
)

#: 结尾即代表"已写完"的字符，用于判断去向行是否需要拼接下一行
_COMPLETE_TAILS: Tuple[str, ...] = tuple(
    dict.fromkeys(list(_LOCATION_MARKERS) + list(ORG_SUFFIXES) + ["组", "总支", "支部", "党委"])
)

#: 不可能出现在"去向单位"里的行（院系 / 活动信息）
_DESTINATION_CELL_BANNED: Tuple[str, ...] = (
    "学院", "学部", "书院", "大学", "系", "分享会", "交流会", "经验分享", "沙龙", "讲座", "论坛",
)

#: "去向行"评分：行政区划标记权重 2、机构标记权重 1
_DEST_LOCATION_WEIGHT = 2
_DEST_ORG_WEIGHT = 1
_DEST_MIN_SCORE = 3


def _score_destination_cell(cell: str) -> int:
    """给一行文本打"像不像去向单位"的分。"""
    if len(cell) < 4 or any(banned in cell for banned in _DESTINATION_CELL_BANNED):
        return 0
    score = _DEST_LOCATION_WEIGHT * sum(1 for m in _LOCATION_MARKERS if m in cell)
    score += _DEST_ORG_WEIGHT * sum(1 for m in _ORG_MARKERS if m in cell)
    return score


# --------------------------------------------------------------------------- #
# 结果结构
# --------------------------------------------------------------------------- #
@dataclass
class ExtractedRecord:
    """一位选调生的结构化去向记录。"""

    name: Optional[str] = None
    cohort: Optional[str] = None
    college: Optional[str] = None
    major: Optional[str] = None
    destination_org: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    position: Optional[str] = None
    degree: Optional[str] = None
    evidence: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    #: 用户关注的 7 个字段
    CORE_FIELDS: ClassVar[Tuple[str, ...]] = (
        "name", "cohort", "college", "major", "destination_org", "city", "position",
    )

    @property
    def field_count(self) -> int:
        return sum(1 for f in self.CORE_FIELDS if getattr(self, f))

    @property
    def confidence(self) -> float:
        """0~1 的启发式置信度：命中字段数 + 是否有姓名。"""
        score = self.field_count / len(self.CORE_FIELDS)
        if self.name:
            score = min(1.0, score + 0.15)
        return round(score, 3)

    @property
    def is_meaningful(self) -> bool:
        """至少命中 1 个字段才算有效记录。"""
        return self.field_count >= 1

    def as_dict(self) -> Dict[str, Any]:
        data = {
            "name": self.name,
            "cohort": self.cohort,
            "college": self.college,
            "major": self.major,
            "destination_org": self.destination_org,
            "city": self.city,
            "province": self.province,
            "position": self.position,
            "degree": self.degree,
            "field_count": self.field_count,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }
        data.update(self.extra)
        return data


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _spans(text: str, needles: Iterable[str]) -> List[Tuple[int, int, str]]:
    """返回 ``needle`` 在 ``text`` 中的所有出现位置（长词优先，不重叠）。"""
    found: List[Tuple[int, int, str]] = []
    taken: List[Tuple[int, int]] = []
    for needle in sorted(set(needles), key=len, reverse=True):
        if not needle:
            continue
        start = 0
        while True:
            idx = text.find(needle, start)
            if idx < 0:
                break
            end = idx + len(needle)
            if not any(idx < e and s < end for s, e in taken):
                found.append((idx, end, needle))
                taken.append((idx, end))
            start = idx + 1
    return found


def _overlaps(span: Tuple[int, int], others: Sequence[Tuple[int, int]]) -> bool:
    s, e = span
    return any(s < oe and os_ < e for os_, oe in others)


def _looks_like_name(token: str, follower: str = "") -> bool:
    """判断一个短中文串是否像人名。

    :param follower: 该候选串**紧后面**的原文。用于排除“机构名/专业名的开头部分”
        与地名——这两类恰好也常以姓氏字开头（经济学院、金融专业、汤原县）。
    """
    if not token or not (2 <= len(token) <= 4):
        return False
    if not all("\u4e00" <= ch <= "\u9fa5" for ch in token):
        return False
    if token in NAME_STOPWORDS:
        return False
    if token in PROVINCE_ALIASES:
        return False
    # 含机构 / 学科 / 时间等语素的一律排除
    if re.search(r"[学院会部系局委办处中心学专业届级室馆厂所队组科司厅办]", token):
        return False
    if any(banned in token for banned in _NAME_FORBIDDEN):
        return False
    # 省名打头的是活动名而非人名：山西定向、宁夏定购、甘肃定向…
    if token[:2] in PROVINCE_ALIASES:
        return False
    # 直接拼在“学院/专业”前面的，是机构或专业的开头部分
    if follower and follower.startswith(_NAME_ORG_FOLLOWERS):
        return False
    # 紧跟行政区划后缀的是地名：汤原县 → “汤原”
    if follower and follower[0] in _NAME_ADMIN_FOLLOWERS:
        return False
    # 是某个专业名的前缀：经济 → 经济学，金融 → 金融学，计算机 → 计算机科学与技术
    if any(major.startswith(token) for major in MAJOR_WHITELIST):
        return False
    if len(token) >= 2 and token[:2] in SURNAMES_TWO_CHAR:
        return True
    return token[0] in SURNAMES


def normalize_cohort(text: str) -> Optional[str]:
    """把多种写法归一为 ``YYYY届`` / ``YYYY级``（保留原始"届/级"语义）。"""
    value, _ = normalize_cohort_detail(text)
    return value


def normalize_cohort_detail(text: str) -> Tuple[Optional[str], Optional[int]]:
    """返回 ``(归一化届别, 届别年份)``。

    优先 ``20XX届``（毕业届别），其次 ``20XX年毕业``；都没有时退化为
    ``20XX级``（入学年级）。不把"级"伪造成"届"。
    """
    for pattern, unit in RE_COHORT_DEFS:
        m = pattern.search(text)
        if m:
            year = int(m.group(1))
            return f"{year}{unit}", year
    return None, None


# --------------------------------------------------------------------------- #
# 各字段抽取
# --------------------------------------------------------------------------- #
def find_name(cells: Sequence[str]) -> Optional[str]:
    """在若干文本单元中寻找人名：标签 → 独立成行 → 行首 → 称谓。"""
    # 1) 显式标签：主讲人：张三
    for cell in cells:
        m = RE_NAME_LABEL.search(cell)
        if m and _looks_like_name(m.group(1), cell[m.end():]):
            return m.group(1)
    # 2) 独立成行（“紧后面”看下一个文本单元的首字，用于排除地名行）
    for i, cell in enumerate(cells):
        token = cell.strip()
        follower = cells[i + 1] if i + 1 < len(cells) else ""
        if RE_NAME_LINE.match(token) and _looks_like_name(token, follower):
            return token
    # 3) 行首 + 后接机构/届别/去向（如 "张三  民族学与社会学学院"）
    for cell in cells:
        raw = cell.strip()
        m = RE_NAME_INLINE.match(raw)
        if m and _looks_like_name(m.group(1), raw[m.end():]):
            return m.group(1)
    # 4) 称谓：张三学长
    for cell in cells:
        m = RE_NAME_TITLE.search(cell)
        if m and _looks_like_name(m.group(1), cell[m.end():]):
            return m.group(1)
    return None


def find_college(text: str) -> Tuple[Optional[str], Optional[Tuple[int, int]]]:
    """返回 ``(学院名, 位置区间)``。优先命中已知院系白名单。"""
    known = _spans(text, KNOWN_COLLEGES)
    if known:
        idx, end, value = max(known, key=lambda t: len(t[2]))
        return value, (idx, end)

    candidates: List[Tuple[int, int, str]] = []
    for m in RE_COLLEGE.finditer(text):
        value = m.group(1)
        if any(banned in value for banned in _COLLEGE_BANNED):
            continue
        if value in {"大学", "学院", "研究院"}:
            continue
        candidates.append((m.start(), m.end(), value))
    for m in RE_DEPARTMENT.finditer(text):
        candidates.append((m.start(), m.end(), m.group(1)))

    if not candidates:
        return None, None
    # 优先非"大学"结尾、且更长的匹配
    candidates.sort(key=lambda t: (t[2].endswith("大学"), -len(t[2])))
    idx, end, value = candidates[0]
    return value, (idx, end)


def find_major(text: str, college_span: Optional[Tuple[int, int]] = None) -> Optional[str]:
    """抽取专业。先白名单精确匹配，再退化为 ``XX专业``。"""
    blocked = [college_span] if college_span else []
    hits = [span for span in _spans(text, MAJOR_WHITELIST) if not _overlaps(span[:2], blocked)]
    if hits:
        # 同时出现多个专业名时，取最长的（白名单里长名更具体）
        return max(hits, key=lambda t: len(t[2]))[2]

    for m in RE_MAJOR_EXPLICIT.finditer(text):
        value = m.group(1).strip()
        if college_span and _overlaps((m.start(), m.end()), blocked):
            continue
        # 去掉被顺带匹配进来的年级前缀："…2021级文物与博物馆专业" → "文物与博物馆"
        value = re.sub(r"^(?:19|20)\d{2}\s*[级年届]?", "", value)
        value = re.sub(r"^[级年届]", "", value)
        value = re.sub(r"^(?:博士|硕士)?研究生", "", value)
        value = value.strip(" ,;:、。；：-|")
        if len(value) >= 2 and not any(banned in value for banned in _COLLEGE_BANNED):
            return value
    return None


def _strip_leading_noise(value: str) -> str:
    """去掉片段开头的年份、"于/在/到"、引导词等残留（只动开头，不碰结尾）。"""
    prev = None
    while prev != value:
        prev = value
        for lead in _LEADS_SORTED:
            if value.startswith(lead):
                value = value[len(lead):]
        value = re.sub(r"^(?:19|20)\d{2}\s*年?", "", value)
        value = re.sub(r"^[于在到赴去、,，:：\s]+", "", value)
    return value.lstrip(" ,;:、。；：-|（）()").strip()


def _strip_trailing_identity(value: str) -> str:
    """去掉片段结尾的身份词/括注，如"…选调生（不定职级）"。"""
    for _ in range(3):
        value = re.sub(r"[（(][^（()）]{0,12}[)）]\s*$", "", value).strip()
        for tail in ("定向选调生", "选调生", "公务员", "参公人员", "参公", "事业编", "选调"):
            if value.endswith(tail) and len(value) > len(tail):
                value = value[: -len(tail)].strip()
                break
    return value.strip(" ,;:、。；：-|")


def _ends_complete(cell: str) -> bool:
    """判断该行是否已经把单位名写完整了。"""
    return cell.endswith(_COMPLETE_TAILS)


def pick_destination_cell(
    cells: Sequence[str],
    context: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """在若干文本单元中找出"去向行"，必要时拼接下一行。

    海报里"去向"通常单独成行，形态为 ``省+市+县+镇+村`` 或 ``省市+机构名``。
    本函数按"行政区划标记 + 机构标记"计分挑出得分最高的行，完全不依赖固定措辞。
    """
    best_idx, best_score = -1, 0
    for i, cell in enumerate(cells):
        score = _score_destination_cell(cell)
        if score > best_score:
            best_idx, best_score = i, score
    if best_idx < 0 or best_score < _DEST_MIN_SCORE:
        return None

    value = cells[best_idx]
    # 单位名没写完（如"…纪委监委派驻"）时，拼接下一行
    if not _ends_complete(value):
        nxt = cells[best_idx + 1] if best_idx + 1 < len(cells) else None
        if nxt is None and context is not None:
            # 目标行恰好是本区块最后一行：从完整列表里找它的下一行
            ctx = list(context)
            try:
                gi = ctx.index(cells[best_idx])
            except ValueError:
                gi = -1
            if 0 <= gi < len(ctx) - 1:
                nxt = ctx[gi + 1]
        if (
            nxt
            and len(nxt) <= 20
            and not any(ch.isdigit() for ch in nxt)
            and nxt not in _POSITION_WEAK
            and any(marker in nxt for marker in _ORG_MARKERS)
        ):
            value = value + nxt
    # 切掉顺带带进来的职务描述（单位名到"村委会/书记/助理"为止）
    cut = _ADMIN_TAIL_CUT.split(value)[0]
    if len(cut.strip(" ,;:、。；：-|（）()")) >= 3:
        value = cut
    return value


def find_destination(
    text: str,
    cells: Optional[Sequence[str]] = None,
    context: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """抽取去向单位。

    顺序：① 显式引导词（"现就职于…"）→ ② 按行定位的"去向行" → ③ 以机构后缀结尾的最长片段。
    全程只做裁剪，不做补全。
    """
    # ① 引导词
    fragments: List[str] = []
    for m in RE_DESTINATION.finditer(text):
        fragments.append(m.group(1))
    cleaned: List[str] = []
    for frag in fragments:
        frag = _strip_trailing_identity(_strip_leading_noise(frag))
        if len(frag) >= 2 and frag not in _POSITION_WEAK:
            cleaned.append(frag)
    if cleaned:
        return max(cleaned, key=len)

    # ② 按行定位
    if cells:
        candidate = pick_destination_cell(cells, context)
        if candidate:
            candidate = _strip_trailing_identity(_strip_leading_noise(candidate))
            if len(candidate) >= 3:
                return candidate

    # ③ 兜底：在**单个**文本单元内找以机构后缀结尾的最长片段。
    #    刻意不跨单元匹配，否则会把"届湖南选调生中国少数民族语言文学学院"这类
    #    跨行拼接的字符串误当成单位名。
    tails: List[str] = []
    for cell in (cells if cells else (text,)):
        if any(banned in cell for banned in _DESTINATION_CELL_BANNED):
            continue
        for t in RE_ORG_TAIL.findall(cell):
            if len(t) >= 3 and t not in _POSITION_WEAK:
                tails.append(t)
    if tails:
        value = _strip_trailing_identity(_strip_leading_noise(max(tails, key=len)))
        if len(value) >= 3:
            return value
    return None


def find_region(text: str, college_span: Optional[Tuple[int, int]] = None) -> Tuple[Optional[str], Optional[str]]:
    """抽取 ``(省份, 城市)``。均做白名单/黑名单校验，避免误报。"""
    blocked = [college_span] if college_span else []
    province: Optional[str] = None
    city: Optional[str] = None

    # 省份：先全称，再简称
    for m in RE_PROVINCE.finditer(text):
        if _overlaps((m.start(), m.end()), blocked):
            continue
        value = m.group(1)
        province = value if value.endswith(("省", "自治区", "特别行政区")) else value
        break
    if province is None:
        alias_hits = [span for span in _spans(text, PROVINCE_ALIASES.keys()) if not _overlaps(span[:2], blocked)]
        if alias_hits:
            value = max(alias_hits, key=lambda t: len(t[2]))[2]
            province = PROVINCE_ALIASES[value]
    if province is None:
        for full in PROVINCE_FULL:
            if full in text:
                province = full
                break
    if province is None:
        m = RE_PROVINCE.search(text)
        if m:
            province = m.group(1)

    # 城市：白名单优先，否则要求带"市/州/地区/盟"后缀
    whitelist_hits = [span for span in _spans(text, CITY_WHITELIST) if not _overlaps(span[:2], blocked)]
    for _s, _e, base in sorted(whitelist_hits, key=lambda t: -len(t[2])):
        m = re.search(
            re.escape(base) + r"[\u4e00-\u9fa5]{0,6}?(?:市|自治州|地区|盟|区|县)",
            text,
        )
        if m:
            city = m.group(0)
            break
        if base in text and base not in _CITY_BANNED:
            city = base
            break
    if city is None:
        for m in RE_CITY.finditer(text):
            base, suffix = m.group(1), m.group(2)
            if _overlaps((m.start(), m.end()), blocked):
                continue
            # 去掉被贪婪匹配进来的省份前缀（如"南省株洲"）
            for prov in sorted(PROVINCE_FULL, key=len, reverse=True):
                if base.startswith(prov):
                    base = base[len(prov):]
                    break
            else:
                for alias in sorted(PROVINCE_ALIASES, key=len, reverse=True):
                    if base.startswith(alias) and len(base) > len(alias):
                        base = base[len(alias):]
                        break
            if len(base) < 2 or base in _CITY_BANNED:
                continue
            if re.search(r"[省市县区州盟]", base):
                continue
            base = _normalize_city_base(base, suffix)
            if not base:
                continue
            city = base + suffix
            break

    # 直辖市：省即市
    if city is None and province in MUNICIPALITIES:
        city = province
    if province is None and city in MUNICIPALITIES:
        province = city
    return province, city


def find_position(text: str) -> Optional[str]:
    """抽取岗位。

    顺序：① ``任/担任/岗位`` 引导 → ② ``XX书记助理`` 这类基层职务短语 →
    ③ 强职位关键词（排除"选调生"等身份词）。
    """
    for m in RE_POSITION.finditer(text):
        frag = m.group(1).strip(" ,;:、。；：-|")
        if frag in _POSITION_WEAK:
            continue
        if any(kw in frag for kw in POSITION_KEYWORDS) or frag.endswith(("员", "助理", "队员")):
            return frag

    for m in RE_POSITION_ASSIST.finditer(text):
        frag = _strip_trailing_identity(m.group(1).strip(" ,;:、。；：-|"))
        # 去掉前面的地名："鹿原镇三口村党总支书记助理" → "党总支书记助理"
        frag = re.sub(r"^.*[省市县区镇乡村街道]", "", frag) or frag
        frag = frag.strip(" ,;:、。；：-|")
        if len(frag) >= 3 and frag not in _POSITION_WEAK:
            return frag

    strong = [kw for kw in POSITION_KEYWORDS if kw not in _POSITION_WEAK]
    hits = _spans(text, strong)
    if hits:
        # 越靠近机构后缀的职位词越可信；此处取最长者
        return max(hits, key=lambda t: len(t[2]))[2]
    return None


def find_degree(text: str) -> Optional[str]:
    for kw in sorted(DEGREE_KEYWORDS, key=len, reverse=True):
        if kw in text:
            return kw
    return None


# --------------------------------------------------------------------------- #
# 区块切分与主流程
# --------------------------------------------------------------------------- #
def _cut_footer(cells: Sequence[str]) -> List[str]:
    """截断海报页脚：遇到页脚标志行后，其后的所有行都不再参与字段抽取。"""
    for i, cell in enumerate(cells):
        if is_footer_line(cell):
            return list(cells[:i])
    return list(cells)


def split_speaker_blocks(cells: Sequence[str]) -> List[List[str]]:
    """按"人名单元"把文本切分为多个主讲人区块。"""
    cells = _cut_footer(cells)
    idx = [i for i, cell in enumerate(cells) if find_name([cell])]
    if not idx:
        return [list(cells)]
    blocks: List[List[str]] = []
    for pos, start in enumerate(idx):
        end = idx[pos + 1] if pos + 1 < len(idx) else len(cells)
        blocks.append(list(cells[start:end]))
    return blocks


def extract_from_cells(
    cells: Sequence[str],
    context: Optional[Sequence[str]] = None,
) -> Optional[ExtractedRecord]:
    """从一组文本单元中抽出一条记录；无有效字段时返回 ``None``。

    :param context: 完整的文本单元列表（跨区块拼接去向行时用）。
    """
    text = "".join(cells)
    if not text.strip():
        return None

    cohort, cohort_year = normalize_cohort_detail(text)
    college, college_span = find_college(text)
    province, city = find_region(text, college_span)
    record = ExtractedRecord(
        name=find_name(cells),
        cohort=cohort,
        college=college,
        major=find_major(text, college_span),
        destination_org=find_destination(text, cells, context),
        city=city,
        province=province,
        position=find_position(text),
        degree=find_degree(text),
        evidence=text[:400],
    )
    if cohort_year:
        record.extra["cohort_year"] = cohort_year
    return record if record.is_meaningful else None


def extract_from_text(text: str, min_line_length: int = 2) -> List[ExtractedRecord]:
    """从纯文本（无坐标）中抽取记录列表。"""
    cells = clean_lines(text, min_line_length=min_line_length, remove_whitespace=True)
    records: List[ExtractedRecord] = []
    for block in split_speaker_blocks(cells):
        record = extract_from_cells(block, context=cells)
        if record is not None:
            records.append(record)
    return records


def extract_from_ocr_result(result: Any, min_line_length: int = 2) -> List[ExtractedRecord]:
    """从带坐标的 :class:`src.ocr.engine.OcrResult` 中，按阅读顺序抽取记录。

    相比 :func:`extract_from_text`，这里能利用文本框位置把海报按行/列还原，
    显著改善"姓名 + 学院 + 专业 + 去向"并排排版时的抽取效果。
    """
    lines = getattr(result, "lines", None) or []
    if not lines:
        return extract_from_text(result if isinstance(result, str) else "", min_line_length)

    from ..ocr.layout import group_rows

    cells: List[str] = []
    stopped = False
    for row in group_rows(lines):
        if stopped:
            break
        for line in row:
            cleaned = normalize_text(line.text).strip()
            if not cleaned or len(cleaned) < min_line_length:
                continue
            # 先按页脚截断，再过滤其它噪声行（顺序不能反，否则截断标志会被过滤掉）
            if is_footer_line(cleaned):
                stopped = True
                break
            if is_noise_line(cleaned):
                continue
            cells.append(cleaned)
    if not cells:
        return []

    records: List[ExtractedRecord] = []
    for block in split_speaker_blocks(cells):
        record = extract_from_cells(block, context=cells)
        if record is not None:
            records.append(record)
    return records


# --------------------------------------------------------------------------- #
# 通知标题辅助信息
# --------------------------------------------------------------------------- #
RE_SESSION_DATE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def region_from_title(title: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """从通知标题解析 ``(举办地区/省份, 活动日期)``。

    例：``11月27日|| MUC"共美"沙龙——湖南选调经验分享`` → ``("湖南省", "11月27日")``。
    """
    if not title:
        return None, None

    province: Optional[str] = None
    m = re.search(
        r"([\u4e00-\u9fa5]{2,8}?)(?:定向)?选调",
        title,
    )
    if m:
        token = m.group(1)
        for size in (4, 3, 2):
            candidate = token[-size:]
            if candidate in PROVINCE_ALIASES:
                province = PROVINCE_ALIASES[candidate]
                break
    if province is None:
        for alias, full in PROVINCE_ALIASES.items():
            if alias in title:
                province = full
                break

    session_date = None
    dm = RE_SESSION_DATE.search(title)
    if dm:
        session_date = f"{int(dm.group(1))}月{int(dm.group(2))}日"
    return province, session_date


__all__ = [
    "ExtractedRecord",
    "extract_from_cells",
    "extract_from_text",
    "extract_from_ocr_result",
    "find_name",
    "find_college",
    "find_major",
    "find_destination",
    "find_region",
    "find_position",
    "find_degree",
    "normalize_cohort",
    "normalize_cohort_detail",
    "pick_destination_cell",
    "split_speaker_blocks",
    "region_from_title",
]
