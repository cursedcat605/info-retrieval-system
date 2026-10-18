"""手工表格导入：清洗、去重与可重建性。"""
from __future__ import annotations

import json

import pytest

from src.database.manual import (
    build_record,
    find_duplicate,
    import_file,
    manual_image_key,
    normalize_city,
    normalize_province,
    read_table_rows,
    sync_manual_sources,
)
from src.database.repo import connect, replace_records, stats, upsert_image


def _write_csv(path, lines):
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _existing_record(name="张三", destination="湖北省武汉市某局", province="湖北省",
                     cohort="2023届", cohort_year=2023, image="notice-1", block_index=0):
    return {
        "image": image,
        "block_index": block_index,
        "name": name,
        "cohort": cohort,
        "cohort_year": cohort_year,
        "destination_org": destination,
        "province": province,
        "city": "武汉市",
        "confidence": 0.9,
        "field_count": 5,
        "raw_text": "OCR",
        "evidence": "OCR",
    }


def test_normalize_region_and_missing_values():
    assert normalize_province("云南") == "云南省"
    assert normalize_province("云南省") == "云南省"
    assert normalize_province("-") is None
    assert normalize_city("云南省昆明市") == "昆明市"
    assert normalize_city("北京市") == "北京市"
    assert normalize_city("-") is None


def test_read_csv_skips_second_header_row(tmp_path):
    path = _write_csv(
        tmp_path / "sample.csv",
        [
            "姓名,学院,专业,去向单位,岗位,城市,省份,届别,学历",
            "name,college,major,destination_org,position,city,province,cohort,degree",
            "张三,信息学院,-,湖北省武汉市某局,-,武汉市,湖北,2023届,硕士研究生",
            "李四,-,-,-,-,-,云南,2024届,-",
        ],
    )
    rows = list(read_table_rows(path))
    assert [row["name"] for _, row in rows] == ["张三", "李四"]
    assert rows[1][1]["province"] == "云南"


def test_build_record_infers_region_and_normalizes_cohort(tmp_path):
    path = _write_csv(
        tmp_path / "sample.csv",
        ["姓名,去向单位,省份,届别", "王五,云南省昆明市某局,-,2024届"],
    )
    _row_number, row = next(read_table_rows(path))
    record = build_record(row, manual_image_key(path), 2, path.name)
    assert record["name"] == "王五"
    assert record["province"] == "云南省"
    assert record["city"] == "昆明市"
    assert (record["cohort"], record["cohort_year"]) == ("2024届", 2024)
    assert record["field_count"] == 4
    assert "第 2 行" in record["evidence"]


def test_find_duplicate_prefers_org_then_province_and_respects_cohort():
    existing = {
        "id": 1,
        "name": "张三",
        "destination_org": "湖北省武汉市某局",
        "province": "湖北省",
        "city": "武汉市",
        "cohort_year": 2023,
    }
    same_org = {"name": "张三", "destination_org": "武汉市某局", "province": "湖北省",
                "cohort_year": 2024}
    assert find_duplicate(same_org, [existing])[0] is existing

    same_region = {"name": "张三", "destination_org": "湖北省某厅", "province": "湖北省",
                   "cohort_year": 2023}
    assert find_duplicate(same_region, [existing])[0] is existing

    different_cohort = {"name": "张三", "destination_org": "湖北省某厅", "province": "湖北省",
                        "cohort_year": 2020}
    assert find_duplicate(different_cohort, [existing]) == (None, None)

    different_person = {"name": "李四", "destination_org": "湖北省武汉市某局",
                        "province": "湖北省", "cohort_year": 2023}
    assert find_duplicate(different_person, [existing]) == (None, None)


def test_import_file_skips_existing_and_merges_same_file_rows(tmp_path):
    db_path = tmp_path / "test.sqlite"
    conn = connect(db_path)
    try:
        upsert_image(conn, {"image": "notice-1", "stem": "notice-1"})
        replace_records(conn, "notice-1", [_existing_record()])
        conn.commit()

        path = _write_csv(
            tmp_path / "sample.csv",
            [
                "姓名,学院,专业,去向单位,岗位,城市,省份,届别,学历",
                "name,college,major,destination_org,position,city,province,cohort,degree",
                "张三,信息学院,-,湖北省武汉市某局,-,武汉市,湖北,2023届,硕士研究生",
                "刘六,-,计算机,石家庄市委党校,-,石家庄市,河北,2021届,-",
                "刘六,-,计算机,中共石家庄市委党校,定向选调生,石家庄市,河北,2021届,-",
                "赵七,-,-,-,-,-,北京市,2022届,大学本科",
            ],
        )
        summary = import_file(conn, path, manual_dir=tmp_path / "imported")
        assert summary["imported"] == 2
        assert summary["skipped_duplicates"] == 1
        assert summary["merged_duplicates"] == 1

        rows = conn.execute(
            "SELECT name, destination_org, position, province, city, field_count "
            "FROM selects_records WHERE name IN ('刘六', '赵七') ORDER BY name"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["name"] == "刘六"
        assert rows[0]["destination_org"] == "中共石家庄市委党校"
        assert rows[0]["position"] == "定向选调生"
        assert rows[1]["province"] == "北京市"
        assert rows[1]["city"] == "北京市"

        # 手工来源不计入图片数
        assert stats(conn)["images"] == 1
        assert stats(conn)["records"] == 3

        sidecar = json.loads((tmp_path / "imported" / "sample.csv.json").read_text(encoding="utf-8"))
        assert sidecar["kind"] == "manual-source"
        assert len(sidecar["records"]) == 2
    finally:
        conn.close()


def test_sync_manual_sources_reloads_and_prunes(tmp_path):
    db_path = tmp_path / "test.sqlite"
    manual_dir = tmp_path / "imported"
    conn = connect(db_path)
    try:
        source_path = _write_csv(
            tmp_path / "sample.csv",
            ["姓名,去向单位,省份,届别", "王五,云南省昆明市某局,云南,2024届"],
        )
        import_file(conn, source_path, manual_dir=manual_dir)
        assert conn.execute("SELECT COUNT(*) FROM selects_records").fetchone()[0] == 1

        conn.execute("DELETE FROM selects_records")
        conn.execute("DELETE FROM images")
        conn.commit()
        summary = sync_manual_sources(conn, manual_dir)
        assert summary["sources"] == 1
        assert summary["written"] == 1
        assert conn.execute("SELECT COUNT(*) FROM selects_records").fetchone()[0] == 1

        (manual_dir / "sample.csv.json").unlink()
        summary = sync_manual_sources(conn, manual_dir)
        assert summary["pruned_sources"] == 1
        assert conn.execute("SELECT COUNT(*) FROM selects_records").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 0
    finally:
        conn.close()


def test_import_file_dry_run_does_not_write(tmp_path):
    db_path = tmp_path / "test.sqlite"
    conn = connect(db_path)
    try:
        path = _write_csv(
            tmp_path / "sample.csv",
            ["姓名,去向单位,省份,届别", "王五,云南省昆明市某局,云南,2024届"],
        )
        summary = import_file(conn, path, dry_run=True)
        assert summary["dry_run"] is True
        assert summary["imported"] == 1
        assert conn.execute("SELECT COUNT(*) FROM selects_records").fetchone()[0] == 0
        assert not (tmp_path / "imported").exists()
    finally:
        conn.close()


def test_import_file_rejects_empty_table(tmp_path):
    conn = connect(tmp_path / "test.sqlite")
    try:
        path = _write_csv(tmp_path / "empty.csv", ["姓名,去向单位", "-,-"])
        with pytest.raises(ValueError):
            import_file(conn, path)
    finally:
        conn.close()
