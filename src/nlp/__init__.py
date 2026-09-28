"""中文 NLP 模块：领域词典 + 结构化字段抽取 + 派生特征。

子模块分工：

* :mod:`~src.nlp.gazetteer` —— 领域词典（省份/城市/高校/专业/岗位关键词）；
* :mod:`~src.nlp.field_extractor` —— 从 OCR 文本抽取结构化字段；
* :mod:`~src.nlp.pinyin` —— 姓名拼音（支撑 ``zhangsan`` → ``张三``）；
* :mod:`~src.nlp.position_category` —— 岗位类别 / 学历层次归一化；
* :mod:`~src.nlp.derive` —— 把上述派生值回填到数据库。
"""

from . import derive, gazetteer, pinyin, position_category
from .field_extractor import (
    ExtractedRecord,
    extract_from_cells,
    extract_from_ocr_result,
    extract_from_text,
    find_college,
    find_degree,
    find_destination,
    find_major,
    find_name,
    find_position,
    find_region,
    normalize_cohort,
    region_from_title,
    split_speaker_blocks,
)

__all__ = [
    "ExtractedRecord",
    "derive",
    "extract_from_cells",
    "extract_from_ocr_result",
    "extract_from_text",
    "find_college",
    "find_degree",
    "find_destination",
    "find_major",
    "find_name",
    "find_position",
    "find_region",
    "gazetteer",
    "normalize_cohort",
    "pinyin",
    "position_category",
    "region_from_title",
    "split_speaker_blocks",
]
