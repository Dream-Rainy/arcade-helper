from __future__ import annotations

"""LXNS 相关 URL/路径常量。

统一收口 `https://maimai.lxns.net` 的硬编码，方便后续替换环境或改版。
"""

from urllib.parse import urlencode

LXNS_BASE_URL = "https://maimai.lxns.net"


def _base(base_url: str | None = None) -> str:
    return (base_url or LXNS_BASE_URL).rstrip("/")


def _with_query(url: str, params: dict[str, object | None]) -> str:
    query = {
        key: ("true" if value is True else "false" if value is False else value)
        for key, value in params.items()
        if value is not None
    }
    if not query:
        return url
    return f"{url}?{urlencode(query)}"

# ---- public site / config ----


def site_config_url(*, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/site/config"


def maimai_song_list_url(*, notes: bool = True, base_url: str | None = None) -> str:
    qs = urlencode({"notes": "true" if notes else "false"})
    return f"{_base(base_url)}/api/v0/maimai/song/list?{qs}"


def maimai_song_url(song_id: int, *, version: int | None = None, base_url: str | None = None) -> str:
    return _with_query(
        f"{_base(base_url)}/api/v0/maimai/song/{song_id}",
        {"version": version},
    )


def maimai_song_collections_url(song_id: int, *, base_url: str | None = None) -> str:
    """获取乐曲的收藆信息（奖杯、称号等）"""
    return f"{_base(base_url)}/api/v0/maimai/song-collections/{song_id}"


def maimai_collection_list_url(
    collection_type: str,
    *,
    version: int | None = None,
    required: bool | None = None,
    base_url: str | None = None,
) -> str:
    return _with_query(
        f"{_base(base_url)}/api/v0/maimai/{collection_type}/list",
        {"version": version, "required": required},
    )


def maimai_collection_url(
    collection_type: str,
    collection_id: int,
    *,
    version: int | None = None,
    base_url: str | None = None,
) -> str:
    return _with_query(
        f"{_base(base_url)}/api/v0/maimai/{collection_type}/{collection_id}",
        {"version": version},
    )


def maimai_alias_list_url(*, base_url: str | None = None) -> str:
    """获取 maimai 乐曲别名列表（LXNS 数据源）"""
    return f"{_base(base_url)}/api/v0/maimai/alias/list"


def chunithm_alias_list_url(*, base_url: str | None = None) -> str:
    """获取 chunithm 乐曲别名列表（LXNS 数据源）"""
    return f"{_base(base_url)}/api/v0/chunithm/alias/list"


def chunithm_song_list_url(*, notes: bool = True, base_url: str | None = None) -> str:
    qs = urlencode({"notes": "true" if notes else "false"})
    return f"{_base(base_url)}/api/v0/chunithm/song/list?{qs}"


def chunithm_song_url(song_id: int, *, version: int | None = None, base_url: str | None = None) -> str:
    return _with_query(
        f"{_base(base_url)}/api/v0/chunithm/song/{song_id}",
        {"version": version},
    )


def chunithm_collection_list_url(
    collection_type: str,
    *,
    version: int | None = None,
    base_url: str | None = None,
) -> str:
    return _with_query(
        f"{_base(base_url)}/api/v0/chunithm/{collection_type}/list",
        {"version": version},
    )


def chunithm_collection_url(
    collection_type: str,
    collection_id: int,
    *,
    version: int | None = None,
    base_url: str | None = None,
) -> str:
    return _with_query(
        f"{_base(base_url)}/api/v0/chunithm/{collection_type}/{collection_id}",
        {"version": version},
    )


# ---- player (public) ----

def maimai_player_url(friend_code: str, *, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/maimai/player/{friend_code}"


def maimai_player_best_url(
    friend_code: str,
    *,
    song_id: int | None = None,
    song_name: str | None = None,
    level_index: int | str | None = None,
    song_type: str | None = None,
    base_url: str | None = None,
) -> str:
    return _with_query(
        f"{_base(base_url)}/api/v0/maimai/player/{friend_code}/best",
        {
            "song_id": song_id,
            "song_name": song_name,
            "level_index": level_index,
            "song_type": song_type,
        },
    )


def maimai_player_bests_url(friend_code: str, *, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/maimai/player/{friend_code}/bests"


def maimai_player_ap_bests_url(friend_code: str, *, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/maimai/player/{friend_code}/bests/ap"


def maimai_player_song_bests_url(
    friend_code: str,
    *,
    song_id: int | None = None,
    song_name: str | None = None,
    song_type: str | None = None,
    base_url: str | None = None,
) -> str:
    return _with_query(
        maimai_player_bests_url(friend_code, base_url=base_url),
        {"song_id": song_id, "song_name": song_name, "song_type": song_type},
    )


def maimai_player_recents_url(friend_code: str, *, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/maimai/player/{friend_code}/recents"


def maimai_player_scores_url(friend_code: str, *, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/maimai/player/{friend_code}/scores"


def maimai_player_heatmap_url(friend_code: str, *, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/maimai/player/{friend_code}/heatmap"


def maimai_player_trend_url(
    friend_code: str,
    *,
    version: int | None = None,
    base_url: str | None = None,
) -> str:
    return _with_query(
        f"{_base(base_url)}/api/v0/maimai/player/{friend_code}/trend",
        {"version": version},
    )


def maimai_player_score_history_url(
    friend_code: str,
    *,
    song_id: int | None = None,
    song_type: str | None = None,
    level_index: int | str | None = None,
    base_url: str | None = None,
) -> str:
    return _with_query(
        f"{_base(base_url)}/api/v0/maimai/player/{friend_code}/score/history",
        {"song_id": song_id, "song_type": song_type, "level_index": level_index},
    )


def maimai_player_collection_url(
    friend_code: str,
    collection_type: str,
    collection_id: int,
    *,
    base_url: str | None = None,
) -> str:
    return f"{_base(base_url)}/api/v0/maimai/player/{friend_code}/{collection_type}/{collection_id}"


def maimai_player_by_qq_url(qq: str, *, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/maimai/player/qq/{qq}"


def chunithm_player_by_qq_url(qq: str, *, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/chunithm/player/qq/{qq}"


def chunithm_player_url(friend_code: str, *, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/chunithm/player/{friend_code}"


def chunithm_player_best_url(
    friend_code: str,
    *,
    song_id: int | None = None,
    song_name: str | None = None,
    level_index: int | str | None = None,
    base_url: str | None = None,
) -> str:
    return _with_query(
        f"{_base(base_url)}/api/v0/chunithm/player/{friend_code}/best",
        {"song_id": song_id, "song_name": song_name, "level_index": level_index},
    )


def chunithm_player_bests_url(friend_code: str, *, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/chunithm/player/{friend_code}/bests"


def chunithm_player_song_bests_url(
    friend_code: str,
    *,
    song_id: int | None = None,
    song_name: str | None = None,
    base_url: str | None = None,
) -> str:
    return _with_query(
        chunithm_player_bests_url(friend_code, base_url=base_url),
        {"song_id": song_id, "song_name": song_name},
    )


def chunithm_player_recents_url(friend_code: str, *, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/chunithm/player/{friend_code}/recents"


def chunithm_player_scores_url(friend_code: str, *, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/chunithm/player/{friend_code}/scores"


def chunithm_player_heatmap_url(friend_code: str, *, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/chunithm/player/{friend_code}/heatmap"


def chunithm_player_trend_url(
    friend_code: str,
    *,
    version: int | None = None,
    base_url: str | None = None,
) -> str:
    return _with_query(
        f"{_base(base_url)}/api/v0/chunithm/player/{friend_code}/trend",
        {"version": version},
    )


def chunithm_player_score_history_url(
    friend_code: str,
    *,
    song_id: int | None = None,
    level_index: int | str | None = None,
    base_url: str | None = None,
) -> str:
    return _with_query(
        f"{_base(base_url)}/api/v0/chunithm/player/{friend_code}/score/history",
        {"song_id": song_id, "level_index": level_index},
    )


def chunithm_player_collection_url(
    friend_code: str,
    collection_type: str,
    collection_id: int,
    *,
    base_url: str | None = None,
) -> str:
    return f"{_base(base_url)}/api/v0/chunithm/player/{friend_code}/{collection_type}/{collection_id}"


# ---- user endpoints (OAuth required) ----


def user_maimai_player_url(*, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/user/maimai/player"


def user_chunithm_player_url(*, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/user/chunithm/player"


def user_maimai_scores_url(*, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/user/maimai/player/scores"


def user_chunithm_scores_url(*, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/user/chunithm/player/scores"


# ---- OAuth ----


def oauth_authorize_url(*, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/oauth/authorize"


def oauth_authorize_with_params(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    scope: str = "",
    response_type: str = "code",
    base_url: str | None = None,
) -> str:
    qs = {
        "response_type": response_type,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if scope:
        qs["scope"] = scope
    return f"{oauth_authorize_url(base_url=base_url)}?{urlencode(qs)}"


def oauth_token_url(*, base_url: str | None = None) -> str:
    return f"{_base(base_url)}/api/v0/oauth/token"


# ---- 第三方别名数据源 ----

YUZUCHAN_BASE_URL = "https://www.yuzuchan.moe"


def yuzuchan_maimai_alias_url() -> str:
    """柚子查 maimai 别名数据接口"""
    return f"{YUZUCHAN_BASE_URL}/api/maimaidx/maimaidxalias"
