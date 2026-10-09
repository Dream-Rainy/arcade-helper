"""Conservative work association. Called inside the catalog write transaction."""

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations, product

from rapidfuzz import fuzz, process
from tortoise.expressions import F

from arcade_helper.aliases.models import identity_text, has_music_variant
from arcade_helper.catalog.importer import content_key
from arcade_helper.core.song_identity import parse_chart_title
from arcade_helper.storage.tortoise.catalog_models import GameSong, MusicWork
from arcade_helper.storage.tortoise.alias_models import AliasRule, AliasEvent, WorkCandidate


async def copy_blocks(db, old_work, new_work):
    for rule in await AliasRule.filter(work_id=old_work, blocked=True).using_db(db):
        key = content_key([str(new_work), rule.alias_key])
        row = await AliasRule.filter(rule_key=key).using_db(db).first()
        if row and (str(row.work_id), row.alias_key) != (str(new_work), rule.alias_key):
            raise ValueError("Rule key collision")
        await AliasRule.update_or_create(rule_key=key, using_db=db, defaults={
            "work_id": new_work, "alias_key": rule.alias_key, "blocked": True,
            "reason": rule.reason, "version": row.version + 1 if row else 1,
        })


async def merge_works(db, source_id, target_id, *, actor_id=None, request_id=None, reason="automatic"):
    if source_id == target_id:
        return
    await copy_blocks(db, source_id, target_id)
    members = await GameSong.filter(work_id=source_id).using_db(db).values_list("id", flat=True)
    await GameSong.filter(work_id=source_id).using_db(db).update(work_id=target_id, version=F("version") + 1)
    await MusicWork.filter(id=source_id).using_db(db).update(redirect_to=target_id, version=F("version") + 1)
    await MusicWork.filter(id=target_id).using_db(db).update(version=F("version") + 1)
    await AliasEvent.create(actor_id=actor_id, request_id=request_id, action="merge_works",
                            before={"work": str(source_id), "songs": members}, after={"work": str(target_id)},
                            reason=reason, using_db=db)


async def split_songs(db, song_ids, *, actor_id, request_id, reason):
    songs = await GameSong.filter(id__in=song_ids).using_db(db).order_by("id")
    if not songs or len({s.work_id for s in songs}) != 1:
        raise ValueError("Split requires entries from one work")
    old = songs[0].work_id
    others = await GameSong.filter(work_id=old).exclude(id__in=song_ids).using_db(db)
    if not others:
        raise ValueError("Split must leave at least one entry in the original work")
    work = await MusicWork.create(display_title=songs[0].title, artist=songs[0].artist, using_db=db)
    await copy_blocks(db, old, work.id)
    await GameSong.filter(id__in=song_ids).using_db(db).update(work=work, version=F("version") + 1)
    await MusicWork.filter(id=old).using_db(db).update(version=F("version") + 1)
    for left in songs:
        for right in others:
            a, b = sorted((left.id, right.id))
            await WorkCandidate.update_or_create(pair_key=content_key([a, b]), using_db=db, defaults={
                "left_id": a, "right_id": b, "status": "rejected", "reason": "manual_split", "target_versions": {},
            })
    await AliasEvent.create(actor_id=actor_id, request_id=request_id, action="split_work", before={"work": str(old)},
                            after={"work": str(work.id), "songs": song_ids}, reason=reason, using_db=db)


@dataclass(slots=True)
class _MatchSong:
    id: int
    work_id: object
    game_code: str
    title: str
    artist: str
    chart_variant: list | None
    version: int


async def match_catalog(db):
    songs = [_MatchSong(*row) for row in await GameSong.filter(active=True).using_db(db).order_by("id").values_list(
        "id", "work_id", "game_code", "title", "artist", "chart_variant", "version")]
    by_id = {s.id: s for s in songs}
    titles = defaultdict(list)
    exact_titles, artists, members = {}, {}, defaultdict(set)
    for song in songs:
        base = parse_chart_title(song.title, literal=song.chart_variant == [])[0]
        titles[identity_text(base)].append(song)
        exact_titles[song.id], artists[song.id] = identity_text(song.title), identity_text(song.artist)
        members[song.work_id].add(song.id)
    rejected = {tuple(sorted(pair)) for pair in await WorkCandidate.filter(status="rejected").using_db(db).values_list("left_id", "right_id")}
    rejected_neighbors = defaultdict(set)
    for a, b in rejected:
        rejected_neighbors[a].add(b)
        rejected_neighbors[b].add(a)

    def blocked(left, right):
        return any(not members[right.work_id].isdisjoint(rejected_neighbors.get(sid, ())) for sid in members[left.work_id])

    def compatible(left, right):
        return (left.game_code != right.game_code and left.chart_variant == right.chart_variant == []
                and exact_titles[left.id] == exact_titles[right.id]
                and bool(left.artist.strip()) and artists[left.id] == artists[right.id])

    pairs = set()
    pair_count = 0
    # ponytail: full scan preserves directed top-four; add an index if total CPU remains high.
    # Yield between small chunks of CPU work.
    for index, (title, group) in enumerate(titles.items()):
        for a, b in combinations(group, 2):
            pairs.add(tuple(sorted((a.id, b.id))))
            pair_count += 1
            if pair_count % 256 == 0:
                await asyncio.sleep(0)
        for similar, score, _ in process.extract(title, titles.keys(), scorer=fuzz.ratio, score_cutoff=90, limit=4):
            if similar != title:
                for a, b in product(group, titles[similar]):
                    pairs.add(tuple(sorted((a.id, b.id))))
                    pair_count += 1
                    if pair_count % 256 == 0:
                        await asyncio.sleep(0)
        if index % 32 == 0:
            await asyncio.sleep(0)
    music_ids = set()
    for index, (a, b) in enumerate(pairs):
        if index % 256 == 0:
            await asyncio.sleep(0)
        left, right = by_id[a], by_id[b]
        if left.work_id != right.work_id and (a, b) not in rejected and compatible(left, right) and not blocked(left, right):
            music_ids.update((a, b))
    variants = {}
    music_ids = sorted(music_ids)
    for offset in range(0, len(music_ids), 256):
        for sid, payload in await GameSong.filter(id__in=music_ids[offset:offset + 256]).using_db(db).values_list("id", "catalog_payload"):
            variants[sid] = has_music_variant(by_id[sid].title, payload)
    merged = 0
    for index, (a, b) in enumerate(sorted(pairs)):
        if index % 256 == 0:
            await asyncio.sleep(0)
        left, right = by_id[a], by_id[b]
        if left.work_id == right.work_id or (a, b) in rejected:
            continue
        is_blocked = blocked(left, right)
        certain = (
            not is_blocked and compatible(left, right)
            and not variants[a] and not variants[b]
        )
        if certain:
            old, target = right.work_id, left.work_id
            await merge_works(db, old, target)
            moved = members.pop(old)
            for sid in moved:
                by_id[sid].work_id = target
                by_id[sid].version += 1
            members[target].update(moved)
            await WorkCandidate.filter(pair_key=content_key([a, b])).using_db(db).update(status="accepted")
            merged += 1
        elif not is_blocked:
            existing = await WorkCandidate.filter(pair_key=content_key([a, b])).using_db(db).first()
            versions = {str(a): left.version, str(b): right.version}
            if existing is None or (existing.status == "pending" and existing.target_versions != versions):
                await WorkCandidate.update_or_create(pair_key=content_key([a, b]), using_db=db, defaults={
                    "left_id": a, "right_id": b, "reason": "variant_or_uncertain", "status": "pending",
                    "target_versions": versions,
                    "version": existing.version + 1 if existing else 1,
                })
    return merged
