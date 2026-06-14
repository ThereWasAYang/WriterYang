from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.io import load_json_model
from novel.core.memory_repair import (
    _memory_repair_user_prompt,
    answer_setting_change_clarification,
    apply_memory_repair,
    generate_memory_change_clarification_decision,
    suggest_memory_repair,
    suggest_setting_change_interactive,
)
from novel.core.orchestrator import decide_ask_intent, route_audit_repair, route_revision_request
from novel.core.planning import ChapterPlanningOptions, plan_chapter
from novel.core.providers import ModelRequest, ProviderFactory
from novel.core.schemas import AgentConfig, AuditIssue, AuditReport, CharactersFile, LocationsFile
from novel.core.workspace import InitOptions, init_workspace


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_REAL_ENV = (
    "WRITERYANG_REAL_BASE_URL",
    "WRITERYANG_REAL_API_KEY",
    "WRITERYANG_REAL_MODEL",
)
DEEPSEEK_ENV = (
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_V4PRO_MODEL",
)
ZAI_ENV = (
    "ZAI_BASE_URL",
    "ZAI_API_KEY",
    "ZAI_MODEL",
)


pytestmark = pytest.mark.real_api


def test_real_provider_smoke() -> None:
    env = _real_env_or_skip()
    provider = ProviderFactory(env=env).create(
        AgentConfig(
            provider=env.get("WRITERYANG_REAL_PROVIDER", "openai_compatible"),
            base_url_env="WRITERYANG_REAL_BASE_URL",
            api_key_env="WRITERYANG_REAL_API_KEY",
            model=env["WRITERYANG_REAL_MODEL"],
            thinking={"type": "disabled"},
            temperature=0,
            timeout_seconds=60,
            max_retries=1,
        )
    )

    response = provider.generate(
        ModelRequest(
            system_prompt="你是一个连接测试助手。请严格只回复 OK。",
            user_prompt="回复 OK",
        )
    )

    assert response.content.strip()


def test_real_plan_chapter_smoke(tmp_path: Path) -> None:
    env = _real_env_or_skip()
    root = _real_project(tmp_path, env)
    provider = ProviderFactory(env=env).create(
        AgentConfig(
            provider=env.get("WRITERYANG_REAL_PROVIDER", "openai_compatible"),
            base_url_env="WRITERYANG_REAL_BASE_URL",
            api_key_env="WRITERYANG_REAL_API_KEY",
            model=env["WRITERYANG_REAL_MODEL"],
            thinking={"type": "disabled"},
            temperature=0.2,
            timeout_seconds=120,
            max_retries=1,
        )
    )

    result = plan_chapter(
        ChapterPlanningOptions(
            root=root,
            chapter_number=1,
            instruction="真实 API 冒烟测试：只生成一个短小、保守、符合 schema 的章节计划。",
        ),
        provider,
    )

    assert result.plan.chapter_number == 1
    assert result.plan_json_path.is_file()
    assert result.plan_markdown_path.is_file()


@pytest.mark.parametrize(
    ("instruction", "expected_route"),
    [
        ("把第1章结尾改成主角主动背叛师门，并改变后续动机。", "plot_replan"),
        ("第1章人物压迫感不够，增加铺垫，减少解释性文字。", "writer_rewrite"),
        ("把第1章第三段“他走得很快”改成“他走得像逃”。", "revision_patch"),
    ],
)
def test_real_deepseek_revision_route_decision(tmp_path: Path, instruction: str, expected_route: str) -> None:
    env = _real_env_or_skip()
    if env.get("WRITERYANG_REAL_PROVIDER") != "deepseek":
        pytest.skip("DeepSeek revision route smoke requires DeepSeek env in .env.real")
    root = _real_project(tmp_path, env)

    decision = route_revision_request(
        root,
        instruction,
        provider_name="config",
        chapter_numbers=[1],
        session_summary="真实 API 路由测试：只有一章极短测试上下文，不包含真实用户项目内容。",
    )

    assert decision.route == expected_route
    assert decision.chapter_numbers == [1]
    assert (root / "runs" / "model_io").is_dir()
    assert env["WRITERYANG_REAL_API_KEY"] not in "\n".join(
        path.read_text(encoding="utf-8") for path in (root / "runs" / "model_io").glob("*.json")
    )


@pytest.mark.parametrize(
    ("user_request", "expected_task"),
    [
        ("帮我搞下第2章，先整一章氛围压抑一点", "session_start"),
        ("第2章 event_wrong_current 其实是回忆，不是现在发生的，帮我修下记忆", "memory_repair_suggest"),
        ("把前两章导出成 markdown 看看", "export"),
    ],
)
def test_real_deepseek_ask_intent_decision(tmp_path: Path, user_request: str, expected_task: str) -> None:
    env = _real_env_or_skip()
    if env.get("WRITERYANG_REAL_PROVIDER") != "deepseek":
        pytest.skip("DeepSeek ask intent smoke requires DeepSeek env in .env.real")
    root = _real_project(tmp_path, env)

    decision = decide_ask_intent(root, user_request, provider_name="config")

    assert decision.task == expected_task
    assert decision.source == "model"


def test_real_deepseek_audit_repair_route_manual_review_for_unstructured_issue(tmp_path: Path) -> None:
    env = _real_env_or_skip()
    if env.get("WRITERYANG_REAL_PROVIDER") != "deepseek":
        pytest.skip("DeepSeek audit repair route smoke requires DeepSeek env in .env.real")
    root = _real_project(tmp_path, env)
    report = AuditReport(
        chapter_number=1,
        audited_file="polished.md",
        overall_status="needs_revision",
        summary="存在一个证据不足的问题。",
        issues=[
            AuditIssue(
                id="issue_unstructured",
                severity="medium",
                type="continuity_issue",
                description="可能有伏笔处理风险，但没有结构化证据。",
                evidence=[],
                suggested_fix="人工确认是否确实需要重写。",
            )
        ],
        passed_checks=[],
        created_at="2026-05-22T00:00:00Z",
    )

    decision = route_audit_repair(root, report, provider_name="config")

    assert decision.route in {"manual_review", "writer_rewrite", "revision_rewrite", "plot_replan"}
    assert decision.source == "model"


def test_real_deepseek_setting_change_clarifies_then_generates_pointer_proposal(tmp_path: Path) -> None:
    env = _real_env_or_skip()
    if env.get("WRITERYANG_REAL_PROVIDER") != "deepseek":
        pytest.skip("DeepSeek setting change clarification smoke requires DeepSeek env in .env.real")
    root = _real_project(tmp_path, env)
    prompt = _memory_repair_user_prompt(
        root,
        "把 char_lin_che 设定为林澈表面温和但做决定非常谨慎",
        change_kind="setting_change",
    )
    assert "/characters/-" in prompt
    assert "/characters/0/reader_visible_summary" in prompt
    assert "/world_rules/-" in prompt

    first = suggest_setting_change_interactive(
        root,
        "把某个人物的背景改一下，但目标和内容我还没想好",
        provider_name="config",
        stage="outline_discussion",
    )
    assert first.status == "needs_clarification"
    assert first.clarification is not None
    assert first.clarification.questions

    second = answer_setting_change_clarification(
        root,
        first.clarification.clarification_id,
        "目标是 char_lin_che，把 reader_visible_summary 改为：林澈表面温和但做决定非常谨慎。",
        provider_name="config",
    )
    assert second.status == "proposal_ready"
    assert second.proposal_result is not None
    assert second.proposal_result.proposal.operations
    assert second.proposal_result.proposal.operations[0].file == "memory/canon/characters.json"
    assert second.proposal_result.proposal.operations[0].path == "/characters/0/reader_visible_summary"
    assert env["WRITERYANG_REAL_API_KEY"] not in "\n".join(
        path.read_text(encoding="utf-8") for path in (root / "runs" / "model_io").glob("*.json")
    )


def test_real_deepseek_setting_change_rich_request_is_ready(tmp_path: Path) -> None:
    env = _real_env_or_skip()
    if env.get("WRITERYANG_REAL_PROVIDER") != "deepseek":
        pytest.skip("DeepSeek setting change rich clarification regression requires DeepSeek env in .env.real")
    root = _real_project(tmp_path, env)

    decision = generate_memory_change_clarification_decision(
        root,
        "新增人物谢蛰雨，设定为栖霞山谢氏后人；隐藏真相是她知道桃花源旧族仍存在，开篇只埋线索不要揭晓。",
        provider_name="config",
        stage="outline_discussion",
    )

    assert decision.status == "ready"
    assert not decision.questions


def test_real_deepseek_setting_change_complex_proposal_preflights(tmp_path: Path) -> None:
    env = _real_env_or_skip()
    if env.get("WRITERYANG_REAL_PROVIDER") != "deepseek":
        pytest.skip("DeepSeek complex setting change preflight regression requires DeepSeek env in .env.real")
    root = _real_project(tmp_path, env)

    result = suggest_setting_change_interactive(
        root,
        (
            "新增人物顾听雪：顾家年轻剑客，擅长快剑，表面洒脱但暗中调查家族旧案。"
            "新增顾家背景：江南二流武林世家，开篇时保持低调。"
            "隐藏真相：顾听雪知道旧案与一条私运线索有关，开篇不要揭晓。"
            "伏笔：第一章只让他注意到一枚残缺账册印记，后续再 payoff。"
        ),
        provider_name="config",
        stage="outline_discussion",
    )

    assert result.status == "proposal_ready"
    assert result.proposal_result is not None
    assert result.proposal_result.proposal.operations
    apply_memory_repair(root, result.proposal_result.proposal_path)
    assert env["WRITERYANG_REAL_API_KEY"] not in "\n".join(
        path.read_text(encoding="utf-8") for path in (root / "runs" / "model_io").glob("*.json")
    )


def test_real_setting_change_pre_creation_timeline_backstory_omits_chapter_zero(tmp_path: Path) -> None:
    env = _real_env_or_skip()
    root = _real_project(tmp_path, env)

    result = suggest_memory_repair(
        root,
        (
            "请只向 memory/state/timeline.json 新增一个开篇前背景时间线事件："
            "徐家旧案发生在开篇前约十年，尚未在任何正文章节揭示，读者暂不可见。"
            "故事世界时间写入 story_position.time_label，不要写第 0 章。"
        ),
        provider_name="config",
        change_kind="setting_change",
        stage="pre_creation",
    )

    timeline_operations = [
        operation
        for operation in result.proposal.operations
        if operation.file == "memory/state/timeline.json"
        and operation.op in {"add", "replace"}
        and isinstance(operation.value, dict)
    ]
    assert timeline_operations
    for operation in timeline_operations:
        value = operation.value
        narrative = value.get("narrative_position")
        assert not (isinstance(narrative, dict) and narrative.get("chapter") == 0)
        assert isinstance(value.get("story_position"), dict)
        assert value["story_position"].get("time_label")
    apply_memory_repair(root, result.proposal_path)
    assert env["WRITERYANG_REAL_API_KEY"] not in "\n".join(
        path.read_text(encoding="utf-8") for path in (root / "runs" / "model_io").glob("*.json")
    )


def test_real_deepseek_setting_change_character_role_semantics(tmp_path: Path) -> None:
    env = _real_env_or_skip()
    if env.get("WRITERYANG_REAL_PROVIDER") != "deepseek":
        pytest.skip("DeepSeek setting change role semantic regression requires DeepSeek env in .env.real")
    root = _real_project(tmp_path, env)

    result = suggest_setting_change_interactive(
        root,
        (
            "新增主要人物：谢蛰雨，女性，谢家长女，擅长谢家剑法；"
            "谢怀云，男性，谢家次子，行事谨慎；"
            "白霜瀚，男性，江湖散人，暗中调查桃花源旧族。"
        ),
        provider_name="config",
        stage="outline_discussion",
    )

    assert result.status == "proposal_ready"
    assert result.proposal_result is not None
    proposal = result.proposal_result.proposal
    values = [
        operation.value
        for operation in proposal.operations
        if operation.file == "memory/canon/characters.json"
        and operation.path == "/characters/-"
        and isinstance(operation.value, dict)
    ]
    expected_tags = {"谢蛰雨": "谢家长女", "谢怀云": "谢家次子", "白霜瀚": "江湖散人"}
    values_by_name = {str(value.get("name")): value for value in values}
    for name, identity_tag in expected_tags.items():
        assert name in values_by_name
        value = values_by_name[name]
        assert value.get("role") not in {identity_tag, "谢家长女", "谢家次子", "江湖散人"}
        assert identity_tag in value.get("tags", [])

    apply_memory_repair(root, result.proposal_result.proposal_path)
    characters = load_json_model(root / "memory" / "canon" / "characters.json", CharactersFile)
    persisted = {character.name: character for character in characters.characters}
    for name, identity_tag in expected_tags.items():
        assert identity_tag in persisted[name].tags
    assert env["WRITERYANG_REAL_API_KEY"] not in "\n".join(
        path.read_text(encoding="utf-8") for path in (root / "runs" / "model_io").glob("*.json")
    )


def test_real_setting_change_duplicate_character_id_uses_existing_path(tmp_path: Path) -> None:
    env = _real_env_or_skip()
    root = _real_project(tmp_path, env)
    _write_twenty_one_real_characters(root)
    target_summary = "白霜瀚开篇伪装成云游书生，暗中调查桃花源旧族线索。"
    prompt = _memory_repair_user_prompt(
        root,
        f"请只修改已有人物白霜瀚（id=char_bai_shuanghan）的 reader_visible_summary 为：{target_summary} 不要新增人物。",
        change_kind="setting_change",
    )
    assert "existing[20]: id=char_bai_shuanghan; name/title=白霜瀚; path=/characters/20" in prompt

    result = suggest_setting_change_interactive(
        root,
        f"请只修改已有人物白霜瀚（id=char_bai_shuanghan）的 reader_visible_summary 为：{target_summary} 不要新增人物。",
        provider_name="config",
        stage="outline_discussion",
    )

    assert result.status == "proposal_ready"
    assert result.proposal_result is not None
    proposal = result.proposal_result.proposal
    assert any(
        operation.file == "memory/canon/characters.json"
        and operation.op == "replace"
        and operation.path.startswith("/characters/20")
        for operation in proposal.operations
    )
    assert not any(
        operation.file == "memory/canon/characters.json"
        and operation.op == "add"
        and operation.path == "/characters/-"
        and isinstance(operation.value, dict)
        and operation.value.get("id") == "char_bai_shuanghan"
        for operation in proposal.operations
    )

    apply_memory_repair(root, result.proposal_result.proposal_path)
    characters = load_json_model(root / "memory" / "canon" / "characters.json", CharactersFile)
    persisted = [character for character in characters.characters if character.id == "char_bai_shuanghan"]
    assert len(persisted) == 1
    assert "云游书生" in persisted[0].reader_visible_summary
    assert env["WRITERYANG_REAL_API_KEY"] not in "\n".join(
        path.read_text(encoding="utf-8") for path in (root / "runs" / "model_io").glob("*.json")
    )


def test_real_setting_change_location_description_path_uses_location_summary(tmp_path: Path) -> None:
    env = _real_env_or_skip()
    root = _real_project(tmp_path, env)
    _write_taohuayuan_location(root)
    target_summary = "桃花源村隐藏在山中，由秦朝避乱者组建，有奇门遁甲大阵守护。"
    prompt = _memory_repair_user_prompt(
        root,
        f"请只修改已有地点桃花源村（id=loc_taohuayuan_village）的 reader_visible_summary 为：{target_summary} 不要新增地点。",
        change_kind="setting_change",
    )
    assert "Location has no top-level description field" in prompt
    assert "/locations/0/reader_visible_summary" in prompt

    result = suggest_setting_change_interactive(
        root,
        f"请只修改已有地点桃花源村（id=loc_taohuayuan_village）的 reader_visible_summary 为：{target_summary} 不要新增地点。",
        provider_name="config",
        stage="outline_discussion",
    )

    assert result.status == "proposal_ready"
    assert result.proposal_result is not None
    proposal = result.proposal_result.proposal
    assert not any(
        operation.file == "memory/canon/locations.json" and operation.path.endswith("/description")
        for operation in proposal.operations
    )
    assert any(
        operation.file == "memory/canon/locations.json"
        and operation.op == "replace"
        and operation.path == "/locations/0/reader_visible_summary"
        for operation in proposal.operations
    )

    apply_memory_repair(root, result.proposal_result.proposal_path)
    locations = load_json_model(root / "memory" / "canon" / "locations.json", LocationsFile)
    persisted = [location for location in locations.locations if location.id == "loc_taohuayuan_village"]
    assert len(persisted) == 1
    assert "奇门遁甲大阵" in persisted[0].reader_visible_summary
    raw_locations = json.loads((root / "memory" / "canon" / "locations.json").read_text(encoding="utf-8"))
    assert "description" not in raw_locations["locations"][0]
    assert env["WRITERYANG_REAL_API_KEY"] not in "\n".join(
        path.read_text(encoding="utf-8") for path in (root / "runs" / "model_io").glob("*.json")
    )


def test_real_setting_change_duplicate_hidden_truth_id_uses_existing_path(tmp_path: Path) -> None:
    env = _real_env_or_skip()
    root = _real_project(tmp_path, env)
    _write_hidden_truth_real_context(root)
    target_description = "桃花源村由秦朝避乱者组建，谢家每代仅两人知晓其存在。"

    result = suggest_setting_change_interactive(
        root,
        (
            "请只修改已有隐藏真相 truth_taohuayuan_village 的 description 为："
            f"{target_description} 不要新增隐藏真相或伏笔。"
        ),
        provider_name="config",
        stage="outline_discussion",
    )

    assert result.status == "proposal_ready"
    assert result.proposal_result is not None
    proposal = result.proposal_result.proposal
    assert proposal.operations
    assert not any(
        operation.op == "add"
        and operation.file == "memory/canon/hidden_truths.json"
        and isinstance(operation.value, dict)
        and operation.value.get("id") == "truth_taohuayuan_village"
        for operation in proposal.operations
    )
    assert not any(
        operation.op == "add"
        and operation.file == "memory/canon/foreshadowing.json"
        and isinstance(operation.value, dict)
        and operation.value.get("id") == "thread_taohuayuan"
        for operation in proposal.operations
    )

    apply_memory_repair(root, result.proposal_result.proposal_path)
    hidden_truths = json.loads((root / "memory" / "canon" / "hidden_truths.json").read_text(encoding="utf-8"))
    foreshadowing = json.loads((root / "memory" / "canon" / "foreshadowing.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in hidden_truths["hidden_truths"]].count("truth_taohuayuan_village") == 1
    assert [item["id"] for item in foreshadowing["foreshadowing_threads"]].count("thread_taohuayuan") == 1
    assert target_description in json.dumps(hidden_truths, ensure_ascii=False)
    assert env["WRITERYANG_REAL_API_KEY"] not in "\n".join(
        path.read_text(encoding="utf-8") for path in (root / "runs" / "model_io").glob("*.json")
    )


def _real_env_or_skip() -> dict[str, str]:
    env = dict(os.environ)
    env.update(_read_env_file(ROOT / ".env.real"))
    if not all(env.get(name) for name in REQUIRED_REAL_ENV) and all(env.get(name) for name in DEEPSEEK_ENV):
        env["WRITERYANG_REAL_BASE_URL"] = env["DEEPSEEK_BASE_URL"]
        env["WRITERYANG_REAL_API_KEY"] = env["DEEPSEEK_API_KEY"]
        env["WRITERYANG_REAL_MODEL"] = env["DEEPSEEK_V4PRO_MODEL"]
        env["WRITERYANG_REAL_PROVIDER"] = "deepseek"
    if not all(env.get(name) for name in REQUIRED_REAL_ENV) and all(env.get(name) for name in ZAI_ENV):
        env["WRITERYANG_REAL_BASE_URL"] = env["ZAI_BASE_URL"]
        env["WRITERYANG_REAL_API_KEY"] = env["ZAI_API_KEY"]
        env["WRITERYANG_REAL_MODEL"] = env["ZAI_MODEL"]
        env["WRITERYANG_REAL_PROVIDER"] = "zai"
    missing = [name for name in REQUIRED_REAL_ENV if not env.get(name)]
    if missing:
        pytest.skip(f"missing real API environment variables: {', '.join(missing)}")
    return env


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip("'").strip('"')
        if name:
            values[name] = value
    return values


def _real_project(tmp_path: Path, env: dict[str, str]) -> Path:
    root = tmp_path / "real_project"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text(
        "# 灵感\n\n雨夜旧车站里传来已经停播多年的广播声，主角循声进入候车厅。\n",
        encoding="utf-8",
    )
    proposal_path = tmp_path / "canon_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    assert apply_canon_proposal(root, proposal_path).validation_report.ok
    _write_real_agents_config(root / "config" / "agents.yaml", env)
    (root / ".env").write_text(
        "\n".join(
            [
                f"WRITERYANG_REAL_BASE_URL={env['WRITERYANG_REAL_BASE_URL']}",
                f"WRITERYANG_REAL_API_KEY={env['WRITERYANG_REAL_API_KEY']}",
                f"WRITERYANG_REAL_MODEL={env['WRITERYANG_REAL_MODEL']}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


def _write_twenty_one_real_characters(root: Path) -> None:
    characters = [
        {
            "id": f"char_existing_{index:02d}",
            "name": f"既有人物{index:02d}",
            "role": "配角",
            "reader_visible_summary": f"既有人物{index:02d}的旧设定。",
        }
        for index in range(20)
    ]
    characters.append(
        {
            "id": "char_bai_shuanghan",
            "name": "白霜瀚",
            "role": "主要人物",
            "reader_visible_summary": "白霜瀚的旧设定。",
        }
    )
    (root / "memory" / "canon" / "characters.json").write_text(
        json.dumps({"characters": characters}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_taohuayuan_location(root: Path) -> None:
    (root / "memory" / "canon" / "locations.json").write_text(
        json.dumps(
            {
                "locations": [
                    {
                        "id": "loc_taohuayuan_village",
                        "name": "桃花源村",
                        "type": "村庄",
                        "reader_visible_summary": "桃花源村的旧设定。",
                        "private_author_notes": "谢蛰雨可以进出。",
                        "parent_location_id": None,
                        "connected_location_ids": [],
                        "rules": [],
                        "tags": ["隐藏"],
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_hidden_truth_real_context(root: Path) -> None:
    (root / "memory" / "canon" / "hidden_truths.json").write_text(
        json.dumps(
            {
                "hidden_truths": [
                    {
                        "id": "truth_taohuayuan_village",
                        "title": "桃花源村",
                        "description": "桃花源村的旧隐藏真相。",
                        "visibility": "hidden",
                        "importance": "high",
                        "related_entity_ids": [],
                        "planned_reveal": None,
                        "foreshadowing_ids": ["thread_taohuayuan"],
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "memory" / "canon" / "foreshadowing.json").write_text(
        json.dumps(
            {
                "foreshadowing_threads": [
                    {
                        "id": "thread_taohuayuan",
                        "type": "clue",
                        "title": "桃花源村伏笔",
                        "introduced_in_chapter": 1,
                        "description": "桃花源村旧伏笔。",
                        "status": "active",
                        "importance": "high",
                        "reader_visible": False,
                        "hidden_truth": "桃花源村",
                        "hidden_truth_id": "truth_taohuayuan_village",
                        "planned_payoff": None,
                        "related_entity_ids": [],
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_real_agents_config(path: Path, env: dict[str, str]) -> None:
    default_config = {
        "provider": env.get("WRITERYANG_REAL_PROVIDER", "openai_compatible"),
        "base_url_env": "WRITERYANG_REAL_BASE_URL",
        "api_key_env": "WRITERYANG_REAL_API_KEY",
        "model": env["WRITERYANG_REAL_MODEL"],
        "reasoning": "low",
        "thinking": {"type": "disabled"},
        "max_context_tokens": 64000,
        "temperature": 0,
        "timeout_seconds": 120,
        "max_retries": 1,
    }
    data = {
        "default": default_config,
        "agents": {
            "plot": {
                "inherit_default": True,
                "temperature": 0.2,
            },
            "orchestrator": {
                "inherit_default": True,
                "temperature": 0,
            }
        }
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
