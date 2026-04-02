# -*- coding: utf-8 -*-

import sqlite3

# =====================================================================
# 1. UI 스키마 (설정 및 조회 화면 정의)
# =====================================================================
def get_ui(core_api):
    sections = []
    default_secs = []
    try:
        # DB에서 영화(1) 및 TV쇼(2) 라이브러리 목록을 가져옵니다.
        rows = core_api['query']("SELECT id, name FROM library_sections WHERE section_type IN (1, 2) ORDER BY name")
        for r in rows:
            sec_val = str(r['id'])
            sections.append({"value": sec_val, "text": f"[{sec_val}] {r['name']}"})
            default_secs.append(sec_val) # 기본값으로 전체 ID를 리스트에 담음
    except: pass

    return {
        "title": "포스터 누락 항목 조회기",
        "description": "DB를 검색하여 포스터가 선택되지 않은 항목(빈 칸)을 리스트업합니다. 제목을 클릭해 Plex에서 수동으로 수정하세요.",
        "inputs": [
            {
                "id": "target_sections", 
                "type": "multi_select", # 다중 선택(전체 토글 지원) 형식으로 변경
                "label": "조회 대상 라이브러리", 
                "options": sections, 
                "default": default_secs # 초기 실행 시 모든 라이브러리가 선택된 상태
            }
        ],
        "buttons": [
            {
                "label": "누락 항목 조회 시작", 
                "action_type": "preview", 
                "icon": "fas fa-search", 
                "color": "#2f96b4"
            }
        ]
    }

# =====================================================================
# 2. 데이터 추출 (DB 검색 로직)
# =====================================================================
def get_target_issues(req_data, core_api, task=None):
    target_sections = req_data.get('target_sections', [])
    
    if not target_sections:
        if task: task.log("⚠️ 선택된 라이브러리가 없습니다.")
        return []

    # 선택된 라이브러리 ID들을 SQL 쿼리에 넣기 위해 포맷팅
    placeholders = ",".join("?" for _ in target_sections)
    
    query = f"""
        SELECT id, title, metadata_type, library_section_id,
               (SELECT name FROM library_sections WHERE id = library_section_id) as section_name
        FROM metadata_items 
        WHERE library_section_id IN ({placeholders})
        AND metadata_type IN (1, 2) 
        AND (user_thumb_url = '' OR user_thumb_url IS NULL)
        ORDER BY library_section_id ASC, title ASC
    """
    
    try:
        # 튜플 형태로 파라미터 전달
        rows = core_api['query'](query, tuple(target_sections))
        return rows
    except Exception as e:
        if task: task.log(f"❌ DB 조회 중 오류 발생: {e}")
        return []

# =====================================================================
# 3. 메인 라우터 (명령 전달)
# =====================================================================
def run(data, core_api):
    action = data.get('action_type', 'preview')

    if action == 'preview':
        return {"status": "success", "type": "async_task", "task_data": data}, 200

    return {"status": "error", "message": "조회 전용 툴입니다."}, 400

# =====================================================================
# 4. 백그라운드 워커 (결과 테이블 구성)
# =====================================================================
def worker(task_data, core_api, start_index):
    task = core_api['task']
    
    task.log("🔍 선택한 라이브러리들에서 포스터 누락 데이터를 수집 중입니다...")
    task.update_state('running', progress=30)
    
    rows = get_target_issues(task_data, core_api, task)
    
    table_data = []
    for r in rows:
        table_data.append({
            "rating_key": str(r['id']),
            "section_name": r['section_name'] or "Unknown",
            "title": r['title'] or f"Unknown (ID:{r['id']})",
            "type_label": "🎬 영화" if r['metadata_type'] == 1 else "📺 TV쇼",
            "status_html": "<span style='color:#bd362f; font-weight:bold;'>포스터 미선택</span>"
        })
    
    task.update_state('running', progress=80)

    res_payload = {
        "status": "success", 
        "type": "datatable",
        "summary_cards": [
            {
                "label": "발견된 누락 항목", 
                "value": f"{len(table_data)} 건", 
                "icon": "fas fa-exclamation-circle", 
                "color": "#bd362f"
            }
        ],
        "columns": [
            {"key": "section_name", "label": "섹션", "width": "15%", "sortable": True, "align": "center"},
            {"key": "type_label", "label": "유형", "width": "15%", "sortable": True, "align": "center"},
            {
                "key": "title", 
                "label": "제목 (클릭 시 Plex 이동)", 
                "width": "50%", 
                "type": "link", 
                "link_key": "rating_key", 
                "sortable": True
            },
            {"key": "status_html", "label": "상태", "width": "20%", "align": "center"}
        ],
        "data": table_data
    }
    
    core_api['cache'].save(res_payload)
    task.log(f"✅ 조회 완료! 총 {len(table_data)}건의 항목을 찾았습니다.")
    task.update_state('completed', progress=100)
