# -*- coding: utf-8 -*-

import os
import time
import json
import sqlite3
import logging
from plexapi.server import PlexServer

# =====================================================================
# 디스코드 알림 템플릿
# =====================================================================
DEFAULT_DISCORD_TEMPLATE = """**✅ 포스터 복구 작업이 완료되었습니다.**

**[📊 종합 통계]**
- 총 소요 시간: {elapsed_time}
- 검사된 라이브러리: {lib_name}
- 복구 완료: {cnt_fix} 건
"""

# =====================================================================
# 1. UI 스키마 (설정 화면)
# =====================================================================
def get_ui(core_api):
    sections = []
    try:
        # DB에서 라이브러리 목록을 가져와 드롭다운 생성
        rows = core_api['query']("SELECT id, name FROM library_sections WHERE section_type IN (1, 2) ORDER BY name")
        for r in rows:
            sections.append({"value": str(r['id']), "text": f"[{r['id']}] {r['name']}"})
    except: pass

    return {
        "title": "포스터 복구 스캐너",
        "description": "DB를 검색하여 포스터가 선택되지 않은 항목을 찾아 첫 번째 포스터로 자동 연결합니다.",
        "inputs": [
            {"id": "target_section", "type": "select", "label": "조회 대상 라이브러리", "options": sections, "default": sections[0]['value'] if sections else ""},
            {"id": "opt_force_refresh", "type": "checkbox", "label": "포스터 데이터가 전혀 없을 경우 메타데이터 새로고침 실행", "default": True}
        ],
        "settings_inputs": [
            {"id": "s_h1", "type": "header", "label": "<i class='fas fa-tachometer-alt'></i> 실행 속도"},
            {"id": "sleep_time", "type": "number", "label": "항목 처리 후 대기 시간 (초)", "default": 0.5},
            {"id": "discord_enable", "type": "checkbox", "label": "디스코드 알림 활성화", "default": True},
            {"id": "discord_webhook", "type": "text", "label": "웹훅 URL", "placeholder": "https://discord.com/api/webhooks/..."}
        ],
        "buttons": [
            {"label": "누락 항목 조회 (Preview)", "action_type": "preview", "icon": "fas fa-search", "color": "#2f96b4"},
            {"label": "즉시 전체 복구 시작", "action_type": "execute_instant", "icon": "fas fa-magic", "color": "#e5a00d"}
        ]
    }

# =====================================================================
# 2. 데이터 추출 (DB 기반 검색)
# =====================================================================
def get_target_issues(req_data, core_api, task=None):
    section_id = req_data.get('target_section')
    
    # 쿼리: user_thumb_url이 비어있는 항목 검색
    query = """
        SELECT id, title, metadata_type, user_thumb_url, library_section_id
        FROM metadata_items 
        WHERE library_section_id = ? 
        AND metadata_type IN (1, 2) 
        AND (user_thumb_url = '' OR user_thumb_url IS NULL)
    """
    
    try:
        rows = core_api['query'](query, (section_id,))
        targets = {}
        for r in rows:
            targets[str(r['id'])] = {
                "title": r['title'],
                "type": r['metadata_type'],
                "section_id": r['library_section_id']
            }
        return targets
    except Exception as e:
        if task: task.log(f"❌ DB 조회 실패: {e}")
        return {}

# =====================================================================
# 3. 메인 라우터
# =====================================================================
def run(data, core_api):
    action = data.get('action_type', 'preview')

    if action in ['preview', 'execute_instant']:
        return {"status": "success", "type": "async_task", "task_data": data}, 200

    if action == 'execute':
        # 테이블에서 선택된 개별 항목 실행 또는 전체 실행
        task_data = data.copy()
        if data.get('_is_single'):
            task_data['target_items'] = [{
                'rating_key': data.get('rating_key'),
                'title': data.get('title'),
                'fix_type': 'poster_fix'
            }]
        return {"status": "success", "type": "async_task", "task_data": task_data}, 200

    return {"status": "error", "message": "지원하지 않는 명령입니다."}, 400

# =====================================================================
# 4. 백그라운드 워커 (실제 작업 로직)
# =====================================================================
def worker(task_data, core_api, start_index):
    task = core_api['task']
    action = task_data.get('action_type')
    
    # [Preview 모드] 테이블 리스트 생성
    if action == 'preview':
        task.log("🔍 DB에서 포스터 누락 항목을 검색 중입니다...")
        targets = get_target_issues(task_data, core_api, task)
        
        table_data = []
        for rk, info in targets.items():
            table_data.append({
                "rating_key": rk,
                "title": info['title'],
                "type_name": "영화" if info['type'] == 1 else "TV쇼",
                "status": "<span style='color:#bd362f;'>포스터 없음</span>",
                "fix_type": "poster_fix"
            })
            
        res_payload = {
            "status": "success", "type": "datatable",
            "summary_cards": [{"label": "누락된 항목", "value": f"{len(table_data)} 건", "icon": "fas fa-image", "color": "#bd362f"}],
            "columns": [
                {"key": "rating_key", "label": "ID", "width": "10%"},
                {"key": "title", "label": "제목", "width": "60%", "type": "link", "link_key": "rating_key"},
                {"key": "status", "label": "상태", "width": "20%", "align": "center"},
                {"key": "action", "label": "실행", "width": "10%", "type": "action_btn", "action_type": "execute"}
            ],
            "data": table_data
        }
        core_api['cache'].save(res_payload)
        task.update_state('completed', progress=100)
        return

    # [Execute 모드] 실제 복구 수행
    work_start_time = time.time()
    cnt_fix = 0
    
    # 대상 항목 결정
    if 'target_items' in task_data:
        items = task_data['target_items']
    else:
        targets = get_target_issues(task_data, core_api, task)
        items = [{"rating_key": k, "title": v['title']} for k, v in targets.items()]

    total = len(items)
    task.log(f"🚀 총 {total}건의 포스터 복구 작업을 시작합니다.")
    
    plex = core_api['get_plex']()
    sleep_time = float(task_data.get('sleep_time', 0.5))

    for idx, item in enumerate(items, 1):
        if task.is_cancelled(): break
        
        rk = item['rating_key']
        title = item['title']
        task.log(f"[{idx}/{total}] 🖼️ '{title}' 복구 시도 중...")
        
        try:
            plex_item = plex.fetchItem(rk)
            posters = plex_item.posters()
            
            if posters:
                plex_item.setPoster(posters[0])
                task.log(f"   ✅ 첫 번째 포스터 선택 완료.")
                cnt_fix += 1
            elif task_data.get('opt_force_refresh'):
                plex_item.refresh()
                task.log(f"   🔍 포스터 리스트가 비어있어 메타데이터 새로고침을 요청했습니다.")
            
            time.sleep(sleep_time)
        except Exception as e:
            task.log(f"   ❌ 오류 발생: {e}")

        task.update_state('running', progress=idx, total=total)

    elapsed_sec = int(time.time() - work_start_time)
    elapsed_str = f"{elapsed_sec}초"
    
    task.log(f"✅ 작업 완료! (총 {cnt_fix}건 복구)")
    
    # 알림 발송
    if task_data.get('discord_enable'):
        tool_vars = {"elapsed_time": elapsed_str, "lib_name": "선택된 라이브러리", "cnt_fix": str(cnt_fix)}
        core_api['notify']("포스터 복구 완료", DEFAULT_DISCORD_TEMPLATE, "#51a351", tool_vars)

    task.update_state('completed', progress=total)