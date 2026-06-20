from __future__ import annotations

import re

from pydantic import BaseModel

from novel.core.schemas import (
    Character,
    CharactersFile,
    EntityState,
    ForeshadowingFile,
    ForeshadowingThread,
    HiddenTruth,
    HiddenTruthsFile,
    Item,
    ItemsFile,
    Location,
    LocationsFile,
    MemoryChangeDomain,
    TimelineFile,
    WorldFile,
    WorldRule,
)


ALLOWED_MEMORY_FILES: dict[str, type[BaseModel]] = {
    "memory/state/timeline.json": TimelineFile,
    "memory/state/current_state.json": EntityState,
    "memory/canon/characters.json": CharactersFile,
    "memory/canon/locations.json": LocationsFile,
    "memory/canon/items.json": ItemsFile,
    "memory/canon/world.json": WorldFile,
    "memory/canon/hidden_truths.json": HiddenTruthsFile,
    "memory/canon/foreshadowing.json": ForeshadowingFile,
}

FILE_DOMAINS: dict[str, MemoryChangeDomain] = {
    "memory/canon/characters.json": "characters",
    "memory/canon/locations.json": "locations",
    "memory/canon/items.json": "items",
    "memory/canon/world.json": "world",
    "memory/canon/hidden_truths.json": "hidden_truths",
    "memory/canon/foreshadowing.json": "foreshadowing",
    "memory/state/current_state.json": "current_state",
    "memory/state/timeline.json": "timeline",
}

DOMAIN_FILES: dict[MemoryChangeDomain, str] = {domain: rel_path for rel_path, domain in FILE_DOMAINS.items()}

FILE_COLLECTION_KEYS: dict[str, str] = {
    "memory/canon/characters.json": "characters",
    "memory/canon/locations.json": "locations",
    "memory/canon/items.json": "items",
    "memory/canon/world.json": "world_rules",
    "memory/canon/hidden_truths.json": "hidden_truths",
    "memory/canon/foreshadowing.json": "foreshadowing_threads",
}

UNIQUE_ID_COLLECTIONS: dict[str, tuple[str, str]] = {
    "memory/canon/characters.json": ("characters", "character id"),
    "memory/canon/locations.json": ("locations", "location id"),
    "memory/canon/items.json": ("items", "item id"),
    "memory/canon/world.json": ("world_rules", "world rule id"),
    "memory/canon/hidden_truths.json": ("hidden_truths", "hidden truth id"),
    "memory/canon/foreshadowing.json": ("foreshadowing_threads", "foreshadowing thread id"),
    "memory/state/timeline.json": ("events", "timeline event id"),
}

STATE_COLLECTION_KEYS = {"character_states", "item_states", "location_states"}

SCANNED_IMPACT_SUFFIXES = {".json", ".md"}

COLLECTION_FIELD_HINTS: dict[str, list[str]] = {
    "memory/canon/characters.json": list(Character.model_fields),
    "memory/canon/locations.json": list(Location.model_fields),
    "memory/canon/items.json": list(Item.model_fields),
    "memory/canon/world.json": list(WorldRule.model_fields),
    "memory/canon/hidden_truths.json": list(HiddenTruth.model_fields),
    "memory/canon/foreshadowing.json": list(ForeshadowingThread.model_fields),
}

COLLECTION_SCHEMA_HINTS: dict[str, str] = {
    "memory/canon/characters.json": (
        "strict add value schema: Character {id, name, role, gender?: 男|女|未知|null, reader_visible_summary, aliases[], private_author_notes?, "
        "appearance: object|null, personality: object|null, relationships: Relationship[], abilities: Ability[], secrets: Secret[], tags[]}.\n"
        "Character.role is narrative role only: use Chinese narrative roles such as 主角, 主要人物, 配角, 次要人物.\n"
        "Use Character.gender for explicit gender facts such as 男性/女性; do not encode gender only as a tag.\n"
        "Never put family rank, sect identity, profession, or jianghu identity in role; phrases such as 谢家长女, 谢家次子, 张家幼女, 唐门二房之女, 江湖散人, 武当俗家弟子 must go into tags and summary/notes.\n"
        "Ability {name: string, description: string, limitations?: string|null}; never use string arrays for abilities.\n"
        "Secret {id: snake_case, visibility: reader_visible|hidden|partially_revealed, description: string, planned_reveal?: string|null}; never use string arrays for secrets."
    ),
    "memory/canon/locations.json": (
        "strict add value schema: Location {id, name, type, reader_visible_summary, private_author_notes?, "
        "parent_location_id?, connected_location_ids[], rules: LocationRule[], tags[]}.\n"
        "Location has no top-level description field. Public location description goes in reader_visible_summary; "
        "hidden/author-only notes go in private_author_notes; explicit rules go in rules[].\n"
        "LocationRule {id?: snake_case|null, description: string, visibility: reader_visible|hidden|partially_revealed}; never use string arrays for rules."
    ),
    "memory/canon/items.json": (
        "strict add value schema: Item {id, name, type, reader_visible_summary, private_author_notes?, origin?, special_properties: SpecialProperty[], tags[]}.\n"
        "SpecialProperty {description: string, visibility: reader_visible|hidden|partially_revealed}; never use string arrays for special_properties."
    ),
    "memory/canon/world.json": (
        "strict add value schema: WorldRule {id, name, description, visibility: reader_visible|hidden|partially_revealed, limitations[], known_by_character_ids[]}.\n"
        "Visibility enum is exactly reader_visible | hidden | partially_revealed; never use visible."
    ),
    "memory/canon/hidden_truths.json": (
        "strict add value schema: HiddenTruth {id, title, description, visibility: reader_visible|hidden|partially_revealed, "
        "importance: low|medium|high|critical, related_entity_ids[], planned_reveal: PlannedReveal|null, foreshadowing_ids[]}.\n"
        "PlannedReveal {chapter: integer >= 1, method?: string|null}; never use string values such as 后期 for planned_reveal.\n"
        "Importance enum is exactly low | medium | high | critical; never use major."
    ),
    "memory/canon/foreshadowing.json": (
        "strict add value schema: ForeshadowingThread {id, type, title, introduced_in_chapter: integer >= 1, description, status, "
        "importance: low|medium|high|critical, reader_visible?: bool|null, hidden_truth?, hidden_truth_id?, planned_payoff: PlannedPayoff|null, related_entity_ids[]}.\n"
        "PlannedPayoff {chapter: integer >= 1, description: string}; never use string values for planned_payoff or introduced_in_chapter."
    ),
}

COLLECTION_PATH_FILES: dict[str, str] = {collection_key: rel_path for rel_path, collection_key in FILE_COLLECTION_KEYS.items()}

POINTER_PATH_FILES: dict[str, str] = {
    **COLLECTION_PATH_FILES,
    "events": "memory/state/timeline.json",
    "character_states": "memory/state/current_state.json",
    "item_states": "memory/state/current_state.json",
    "location_states": "memory/state/current_state.json",
}

SETTING_CHANGE_MAPPING_RULES = """设定变更默认映射规则：
- 文件、字段、visibility 和 JSON Pointer 由系统根据下方结构负责选择，不要要求用户提供。
- 新人物/明确姓名默认写入 memory/canon/characters.json，新增路径使用 /characters/-。
- Character.role 只表示叙事角色；新增人物默认使用中文叙事角色值：主角、主要人物、配角、次要人物。
- 用户说“主要人物”时默认 role="主要人物"；明确主角用 role="主角"；明确次要/背景用 role="次要人物"；未明确时用 role="配角"。
- 用户明确“男性/女性/性别”时，优先写入 Character.gender（男/女），不要只追加到 tags。
- 家族身份、门派身份、排行、职业/江湖身份必须写入 tags，并可写入 reader_visible_summary 或 private_author_notes；不要把“谢家长女”“谢家次子”“张家幼女”“唐门二房之女”“江湖散人”“武当俗家弟子”等写入 role。
- 新地点、宅邸、村庄、宫殿、门派驻地默认写入 memory/canon/locations.json，新增路径使用 /locations/-。
- 地点公开描述写入 reader_visible_summary；隐藏/作者私有说明写入 private_author_notes；地点规则写入 rules[]；Location 顶层没有 description 字段，不要使用 /locations/{i}/description。
- 家族、门派、势力背景、时代背景、武学体系、世界规则默认写入 memory/canon/world.json，新增路径使用 /world_rules/-。
- 物品、武器、信物、法器默认写入 memory/canon/items.json，新增路径使用 /items/-。
- 隐藏设定、真相、秘密、暂不揭晓内容默认写入 memory/canon/hidden_truths.json，visibility 默认 hidden，新增路径使用 /hidden_truths/-。
- 伏笔、线索、开篇埋线默认写入 memory/canon/foreshadowing.json，新增路径使用 /foreshadowing_threads/-。
- 只有 exact id、exact name 或 exact alias 匹配时才修改已有实体；不要把新姓名近似联想到现有角色。
- 无精确匹配且用户没有明确要求替换/删除/合并时，按新增实体处理。
"""

NARRATIVE_CHARACTER_ROLES = {
    "主角",
    "主人公",
    "男主",
    "女主",
    "主要人物",
    "核心人物",
    "配角",
    "重要配角",
    "次要人物",
    "背景人物",
    "反派",
    "对手",
    "盟友",
    "导师",
    "线索人物",
    "群像主角",
}

CHARACTER_ROLE_IDENTITY_PATTERNS = (
    re.compile(r"([\u4e00-\u9fff]{1,4}(?:家|氏)[长次二三四五六七八九十幼少庶嫡]?[子女])"),
    re.compile(r"([\u4e00-\u9fff]{0,8}[一二三四五六七八九十]房之[子女])"),
    re.compile(r"([\u4e00-\u9fff]{1,8}(?:门|派|宗|宫|教|帮|寨|庄|阁|楼|堂|会)(?:弟子|门人|传人|少主|掌门|门主|长老|护法|客卿))"),
    re.compile(
        r"(江湖散人|武林散人|俗家弟子|弟子|散人|剑客|刀客|刺客|医师|药师|捕快|镖师|商人|匠人|书生|先生|客卿|护卫|侍女|仆从|丫鬟|公子|小姐|少侠|侠客|道士|和尚|僧人|术士|修士|家主|门主|掌门|长老|少主|后人|族人|遗孤)"
    ),
)
