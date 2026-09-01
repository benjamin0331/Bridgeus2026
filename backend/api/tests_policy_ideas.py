"""pytest tests：join.gov.tw 提案快照的匯入與讀取。

Run from backend/:
    pytest api/tests_policy_ideas.py -v

消費者是 Godot 大廳第二隻教學青蛙的台詞（godot/Entities/npc/npc_frog2.gd）。
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.test import APIClient

from api.models import PolicyIdea

User = get_user_model()

TAIPEI = ZoneInfo("Asia/Taipei")


def _idea(idx, *, endorse=100):
    return {
        "id": f"uuid-{idx}",
        "title": f"提案 {idx}",
        "outline": "說明" * 10,
        "url": f"https://join.gov.tw/idea/detail/uuid-{idx}",
        "status": "FirstSigned",
        "endorseCount": endorse,
        "endorseGoal": 5000,
        "categories": ["內政"],
        "organizations": ["內政部"],
        "publishDate": "2026-07-05 00:05:11",
    }


def _write_json(tmp_path, *, hot=None, latest=None, fetched="2026-07-20 17:50:05"):
    payload = {"fetchedAt": fetched, "source": "https://join.gov.tw/idea/search"}
    if hot is not None:
        payload["熱門"] = {"totalResults": len(hot), "ideas": hot}
    if latest is not None:
        payload["最新"] = {"totalResults": len(latest), "ideas": latest}
    path = tmp_path / "join_ideas.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


# --- 匯入指令 --------------------------------------------------------------


@pytest.mark.django_db
def test_import_creates_rows_in_source_order(tmp_path):
    """rank 照 JSON 的順序，不重算——那個順序就是平臺自己的「熱門」排法。"""
    path = _write_json(tmp_path, hot=[_idea(0), _idea(1), _idea(2)])

    call_command("import_join_ideas", path)

    rows = list(PolicyIdea.objects.filter(section=PolicyIdea.Section.HOT))
    assert [r.rank for r in rows] == [0, 1, 2]
    assert [r.title for r in rows] == ["提案 0", "提案 1", "提案 2"]
    assert rows[0].categories == ["內政"]
    assert rows[0].organizations == ["內政部"]


@pytest.mark.django_db
def test_import_is_idempotent(tmp_path):
    path = _write_json(tmp_path, hot=[_idea(0), _idea(1)])

    call_command("import_join_ideas", path)
    call_command("import_join_ideas", path)

    assert PolicyIdea.objects.filter(section=PolicyIdea.Section.HOT).count() == 2


@pytest.mark.django_db
def test_reimport_replaces_the_whole_section(tmp_path):
    """快照語意：新的一批進來，上一批沒出現的要消失。

    留著殘骸的話「熱門前三名」會變成四筆五筆，而讀取端沒有任何依據可以判斷哪些
    是舊的——所以刪除是這支指令的功能，不是副作用。
    """
    # 兩份 JSON 要分開放：_write_json 的檔名是固定的，同一個目錄會被蓋掉。
    (tmp_path / "a").mkdir()
    first = _write_json(tmp_path / "a", hot=[_idea(0), _idea(1)])
    (tmp_path / "b").mkdir()
    second = _write_json(tmp_path / "b", hot=[_idea(1, endorse=999), _idea(2)])

    call_command("import_join_ideas", first)
    call_command("import_join_ideas", second)

    rows = list(PolicyIdea.objects.filter(section=PolicyIdea.Section.HOT))
    assert [r.external_id for r in rows] == ["uuid-1", "uuid-2"]
    # 留下來的那筆要被更新，不是原封不動
    assert rows[0].rank == 0
    assert rows[0].endorse_count == 999


@pytest.mark.django_db
def test_sections_do_not_clobber_each_other(tmp_path):
    """同一則提案可以同時在熱門與最新，兩邊各存一筆、互不影響。"""
    path = _write_json(tmp_path, hot=[_idea(0)], latest=[_idea(0), _idea(9)])

    call_command("import_join_ideas", path)

    assert PolicyIdea.objects.filter(section=PolicyIdea.Section.HOT).count() == 1
    assert PolicyIdea.objects.filter(section=PolicyIdea.Section.LATEST).count() == 2


@pytest.mark.django_db
def test_naive_timestamps_are_read_as_taipei_time(tmp_path):
    """JSON 的時間是台灣網站上的台灣時間，專案的 TIME_ZONE 是 UTC。

    用 make_aware 的預設時區會把台北時間當成 UTC，整批資料早八小時——那不會報錯，
    只會讓「資料有多新」這件事默默錯掉。
    """
    path = _write_json(tmp_path, hot=[_idea(0)], fetched="2026-07-20 17:50:05")

    call_command("import_join_ideas", path)

    row = PolicyIdea.objects.get(section=PolicyIdea.Section.HOT)
    assert row.fetched_at == datetime(2026, 7, 20, 17, 50, 5, tzinfo=TAIPEI)
    assert row.publish_date == datetime(2026, 7, 5, 0, 5, 11, tzinfo=TAIPEI)


@pytest.mark.django_db
def test_dry_run_writes_nothing(tmp_path):
    path = _write_json(tmp_path, hot=[_idea(0)])

    call_command("import_join_ideas", path, "--dry-run")

    assert PolicyIdea.objects.count() == 0


@pytest.mark.django_db
def test_import_rejects_json_without_known_sections(tmp_path):
    path = _write_json(tmp_path)

    with pytest.raises(CommandError):
        call_command("import_join_ideas", path)


@pytest.mark.django_db
def test_import_rejects_missing_file():
    with pytest.raises(CommandError):
        call_command("import_join_ideas", "/nope/does-not-exist.json")


@pytest.mark.django_db
def test_titles_are_whitespace_normalised(tmp_path):
    """平臺上的標題偶爾夾著換行與尾空白（實測過），進表前就清乾淨，
    免得每個消費者各自 strip 一次。"""
    item = _idea(0)
    item["title"] = "  建立急救召喚獸\n到宅支援制度  "
    path = _write_json(tmp_path, hot=[item])

    call_command("import_join_ideas", path)

    assert PolicyIdea.objects.get().title == "建立急救召喚獸 到宅支援制度"


# --- 讀取端點 --------------------------------------------------------------


def _auth_client():
    user = User.objects.create_user(username="frog-reader", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_endpoint_requires_authentication():
    assert APIClient().get("/api/policy-ideas/").status_code == 401


@pytest.mark.django_db
def test_endpoint_returns_hot_section_ordered_by_rank(tmp_path):
    call_command(
        "import_join_ideas",
        _write_json(tmp_path, hot=[_idea(0), _idea(1)], latest=[_idea(9)]),
    )

    response = _auth_client().get("/api/policy-ideas/")

    assert response.status_code == 200
    assert [row["title"] for row in response.data] == ["提案 0", "提案 1"]
    assert response.data[0]["endorse_count"] == 100
    # outline 刻意不吐（動輒兩三百字，青蛙的對話框放不下）
    assert "outline" not in response.data[0]


@pytest.mark.django_db
def test_endpoint_limit_truncates(tmp_path):
    call_command(
        "import_join_ideas",
        _write_json(tmp_path, hot=[_idea(i) for i in range(5)]),
    )

    response = _auth_client().get("/api/policy-ideas/?limit=2")

    assert response.status_code == 200
    assert [row["rank"] for row in response.data] == [0, 1]


@pytest.mark.django_db
def test_endpoint_limit_is_clamped_not_rejected(tmp_path):
    """limit 超出範圍夾住而不是回 400：呼叫端要的是「給我幾則」，
    寫了個離譜的數字沒有理由讓青蛙整段開不了口。"""
    call_command(
        "import_join_ideas",
        _write_json(tmp_path, hot=[_idea(i) for i in range(3)]),
    )
    client = _auth_client()

    assert len(client.get("/api/policy-ideas/?limit=999").data) == 3
    assert len(client.get("/api/policy-ideas/?limit=0").data) == 1


@pytest.mark.django_db
def test_endpoint_rejects_unknown_section():
    assert _auth_client().get("/api/policy-ideas/?section=nope").status_code == 400


@pytest.mark.django_db
def test_endpoint_rejects_non_integer_limit():
    assert _auth_client().get("/api/policy-ideas/?limit=abc").status_code == 400


@pytest.mark.django_db
def test_endpoint_returns_empty_list_when_nothing_imported():
    """還沒匯入過就是空陣列，不是 404——青蛙那邊靠「空的」走備援台詞。"""
    response = _auth_client().get("/api/policy-ideas/")

    assert response.status_code == 200
    assert response.data == []
