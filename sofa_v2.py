"""
소파 스펀지 재단 시뮬레이션 v3.0 — 통합 포탈 + ERP 연동
"""
import streamlit as st
import streamlit.components.v1 as components
import json, os, copy, math, requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from io import BytesIO

# 한국 시간 (UTC+9)
KST = timezone(timedelta(hours=9))
def now_kst():
    return datetime.now(KST)


# ============================================================
# 1. 설정
# ============================================================
st.set_page_config(page_title="스펀지 재단 시뮬레이션", layout="wide", page_icon="iloom_LOGO.png")

BLOCK_W = 1212
BLOCK_H = 1970
BLOCK_DEPTH = 600
BLOCK_AREA = BLOCK_W * BLOCK_H

STORAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
try:
    os.makedirs(STORAGE_DIR, exist_ok=True)
except OSError:
    STORAGE_DIR = os.path.join(os.path.expanduser("~"), ".sofa_sim_data")
    os.makedirs(STORAGE_DIR, exist_ok=True)
STORAGE_FILE = os.path.join(STORAGE_DIR, "residual_blocks.json")
PLAN_FILE = os.path.join(STORAGE_DIR, "current_plan.json")
DEPLOY_LOG_FILE = os.path.join(STORAGE_DIR, "deploy_history.json")

ITEM_MASTER = {
    'A': {'matname': '케렌시아 1인',         'matcd': 'OPFW005348-R000', 'matcol': 'XX', 'width': 550,  'depth': 480, 'height': 70,  'color': '#E74C3C'},
    'B': {'matname': '케렌시아 3인',         'matcd': 'OPFW003146-R000', 'matcol': 'XX', 'width': 1650, 'depth': 680, 'height': 150, 'color': '#3498DB'},
    'C': {'matname': '케렌시아 싱글',        'matcd': 'OPFW003149-R000', 'matcol': 'XX', 'width': 710,  'depth': 640, 'height': 150, 'color': '#2ECC71'},
    'D': {'matname': '케렌시아 오토만',      'matcd': 'OPFW003151-R000', 'matcol': 'XX', 'width': 660,  'depth': 580, 'height': 150, 'color': '#1ABC9C'},
    'E': {'matname': '카포네 1인',           'matcd': 'OPFW003091-R000', 'matcol': 'XX', 'width': 480,  'depth': 435, 'height': 105, 'color': '#F39C12'},
    'F': {'matname': '카포네 2인',           'matcd': 'OPFW003092-R000', 'matcol': 'XX', 'width': 480,  'depth': 755, 'height': 105, 'color': '#9B59B6'},
    'G': {'matname': '카포네 공용',          'matcd': 'OPFW003101-R000', 'matcol': 'XX', 'width': 480,  'depth': 375, 'height': 105, 'color': '#1E8449'},
    'H': {'matname': '카포네 코너 뒤팔걸이', 'matcd': 'OPFW003107-R000', 'matcol': 'XX', 'width': 790,  'depth': 465, 'height': 105, 'color': '#D4AC0D'},
    'I': {'matname': '카포네 코너 뒤',       'matcd': 'OPFW003108-R000', 'matcol': 'XX', 'width': 375,  'depth': 465, 'height': 105, 'color': '#8E44AD'},
    'J': {'matname': '카포네 코너 팔걸이쪽', 'matcd': 'OPFW003109-R000', 'matcol': 'XX', 'width': 700,  'depth': 465, 'height': 105, 'color': '#2980B9'},
}
for info in ITEM_MASTER.values():
    info['unit'] = BLOCK_DEPTH // info['height']

MIN_ITEM_DIM = min(min(i['width'], i['depth']) for i in ITEM_MASTER.values())

# mat_code → item code 역매핑
MATCODE_TO_ITEM = {}
for code, info in ITEM_MASTER.items():
    MATCODE_TO_ITEM[info['matcd']] = code

# ============================================================
# ERP API 설정 (Streamlit Cloud: st.secrets / 로컬: .env)
# ============================================================
def _get_secret(key, default=""):
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError, AttributeError):
        pass
    val = os.getenv(key)
    if val:
        return val
    return default

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ERP_BASE_URL = _get_secret("ERP_BASE_URL", "https://dev-erp-api2.fursys.com")
ERP_AUTH_KEY = _get_secret("ERP_AUTH_KEY", "dc1c3d99-0700-4472-816e-e3f1e9111823")
ERP_IDENTIFIER = _get_secret("ERP_IDENTIFIER", "external_partner")

def _erp_post(endpoint, data_body):
    """ERP API 공통 호출"""
    url = f"{ERP_BASE_URL}{endpoint}"
    payload = {
        "authentication_key": ERP_AUTH_KEY,
        "identifier_id": ERP_IDENTIFIER,
        "data": data_body
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("_code") == 200:
            return result.get("data", []), None
        else:
            return None, f"API 오류 ({result.get('_code')}): {result.get('_message', '알 수 없는 오류')}"
    except requests.exceptions.ConnectionError:
        return None, "연결 실패: ERP 서버에 접근할 수 없습니다."
    except requests.exceptions.Timeout:
        return None, "시간 초과: 10초 이내에 응답이 없습니다."
    except Exception as e:
        return None, f"오류: {str(e)}"

def call_erp_query(storecd="PAN", pono=""):
    """구매의뢰 상세 조회"""
    return _erp_post("/api/erp/v1/mat-po-dtl/list", {"storecd": storecd, "pono": pono})

def call_erp_update_poqty(storecd, pono, matcd, matcol, po_seq, poqty, after_result):
    """구매의뢰수량 + 사후검사 결과 수정"""
    return _erp_post("/api/erp/v1/mat-po-dtl/update-poqty", {
        "storecd": storecd, "pono": pono, "matcd": matcd, "matcol": matcol,
        "po_seq": po_seq, "poqty": poqty, "after_result": after_result
    })

def call_erp_update_before_result(storecd, pono, before_result):
    """사전검사 결과 저장"""
    return _erp_post("/api/erp/v1/mat-po-hed/update-before-result", {
        "storecd": storecd, "pono": pono, "before_result": before_result
    })

def erp_data_to_dataframe(data):
    """ERP 응답 데이터를 DataFrame으로 변환"""
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    col_map = {
        'storecd': '사업장', 'pono': '구매의뢰번호', 'po_seq': '순번',
        'matcd': '자재코드', 'matcol': '컬러', 'matname': '자재명',
        'poqty': '의뢰수량', 'sizedtl': '사이즈', 'width': '가로',
        'depth': '세로', 'height': '높이'
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    return df

def match_erp_to_items(data):
    """ERP 구매의뢰 데이터를 ITEM_MASTER와 매칭 — 실제 개수(poqty) 그대로 반환"""
    qty_map = {code: 0 for code in ITEM_MASTER}
    for row in data:
        matcd = row.get('matcd', '')
        if matcd in MATCODE_TO_ITEM:
            item_code = MATCODE_TO_ITEM[matcd]
            poqty = int(row.get('poqty', 0))
            qty_map[item_code] += poqty
    return qty_map

# ============================================================
# 2. 패킹 알고리즘
# ============================================================
class FreeRect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h
    def area(self): return self.w * self.h
    def to_dict(self): return {'x': self.x, 'y': self.y, 'w': self.w, 'h': self.h}
    @classmethod
    def from_dict(cls, d): return cls(d['x'], d['y'], d['w'], d['h'])

class Block:
    def __init__(self, bid, free_rects=None, items=None, is_saved=False, saved_date='',
                 original_area=0, original_bb=None):
        self.id = bid
        self.items = items or []
        self.free_rects = free_rects or [FreeRect(0, 0, BLOCK_W, BLOCK_H)]
        self.is_saved = is_saved
        self.saved_date = saved_date
        self.original_area = original_area
        self.original_bb = original_bb  # [x, y, w, h] 최초 저장 시 고정

    def used_area(self):
        """실제 사용 면적 = 2D면적 × (실제개수/단위). 부분 조각은 비례 축소."""
        total = 0
        for i in self.items:
            area = i['w'] * i['h']
            unit = i.get('unit') or ITEM_MASTER[i['code']]['unit']
            cnt  = i.get('cnt', unit)
            total += area * (cnt / unit)
        return total

    def total_area(self):
        if self.is_saved and self.original_area > 0:
            return self.original_area
        return BLOCK_AREA

    def yield_pct(self):
        ta = self.total_area()
        return (self.used_area() / ta) * 100 if ta else 0

    def bounding_box(self):
        """배치도 그리기용 바운딩박스"""
        if not self.is_saved:
            return 0, 0, BLOCK_W, BLOCK_H
        if self.original_bb:
            return tuple(self.original_bb)
        # fallback: free_rects만으로 계산 (items 제외!)
        rects = [(fr.x, fr.y, fr.x+fr.w, fr.y+fr.h) for fr in self.free_rects]
        if not rects:
            return 0, 0, BLOCK_W, BLOCK_H
        min_x = min(r[0] for r in rects)
        min_y = min(r[1] for r in rects)
        max_x = max(r[2] for r in rects)
        max_y = max(r[3] for r in rects)
        return min_x, min_y, max_x - min_x, max_y - min_y

    def find_best_placement(self, iw, id_):
        best = None
        orients = [(iw, id_, False)]
        if iw != id_: orients.append((id_, iw, True))
        for w, h, rot in orients:
            for i, fr in enumerate(self.free_rects):
                if w <= fr.w and h <= fr.h:
                    sc = min(fr.w-w, fr.h-h)
                    if best is None or sc < best['score']:
                        best = {'ri': i, 'w': w, 'h': h, 'rot': rot, 'score': sc}
        return best

    def place_item(self, code, p, cnt=None):
        """cnt: 이 조각에 실제 들어가는 개수. None이면 unit(완전 조각)으로 처리."""
        fr = self.free_rects[p['ri']]
        w, h = p['w'], p['h']
        unit = ITEM_MASTER[code]['unit']
        actual_cnt = cnt if cnt is not None else unit
        self.items.append({'code': code, 'x': fr.x, 'y': fr.y, 'w': w, 'h': h,
                           'rot': p['rot'], 'cnt': actual_cnt, 'unit': unit})
        rw, rh = fr.w-w, fr.h-h
        rh_list, rv_list = [], []
        if rw > 0: rh_list.append(FreeRect(fr.x+w, fr.y, rw, fr.h))
        if rh > 0: rh_list.append(FreeRect(fr.x, fr.y+h, w, rh))
        if rh > 0: rv_list.append(FreeRect(fr.x, fr.y+h, fr.w, rh))
        if rw > 0: rv_list.append(FreeRect(fr.x+w, fr.y, rw, h))
        mh = max((r.area() for r in rh_list), default=0)
        mv = max((r.area() for r in rv_list), default=0)
        chosen = [r for r in (rh_list if mh >= mv else rv_list) if min(r.w, r.h) >= MIN_ITEM_DIM]
        self.free_rects.pop(p['ri'])
        self.free_rects.extend(chosen)

    def get_usable_free_rects(self):
        out = []
        for fr in self.free_rects:
            for info in ITEM_MASTER.values():
                if (info['width']<=fr.w and info['depth']<=fr.h) or (info['depth']<=fr.w and info['width']<=fr.h):
                    out.append(fr); break
        return out

    def to_dict(self):
        oa = self.original_area if self.original_area > 0 else sum(fr.area() for fr in self.free_rects)
        # 바운딩박스: 최초 저장 시 free_rects 기반으로 고정
        if self.original_bb:
            obb = list(self.original_bb)
        else:
            rects = [(fr.x, fr.y, fr.x+fr.w, fr.y+fr.h) for fr in self.free_rects]
            if rects:
                obb = [min(r[0] for r in rects), min(r[1] for r in rects),
                       max(r[2] for r in rects)-min(r[0] for r in rects),
                       max(r[3] for r in rects)-min(r[1] for r in rects)]
            else:
                obb = [0, 0, BLOCK_W, BLOCK_H]
        return {'id': self.id, 'items': self.items,
                'free_rects': [f.to_dict() for f in self.free_rects],
                'is_saved': self.is_saved, 'original_area': oa, 'original_bb': obb,
                'saved_date': self.saved_date or now_kst().strftime('%Y-%m-%d %H:%M')}

    @classmethod
    def from_dict(cls, d):
        frs = [FreeRect.from_dict(f) for f in d.get('free_rects',[])]
        obb = d.get('original_bb')
        oa = d.get('original_area', 0)
        # 이전 버전 데이터: original_bb가 없으면 free_rects에서 계산
        if not obb and frs:
            rects = [(fr.x, fr.y, fr.x+fr.w, fr.y+fr.h) for fr in frs]
            obb = [min(r[0] for r in rects), min(r[1] for r in rects),
                   max(r[2] for r in rects)-min(r[0] for r in rects),
                   max(r[3] for r in rects)-min(r[1] for r in rects)]
        if not oa and frs:
            oa = sum(fr.area() for fr in frs)
        return cls(d['id'], frs, d.get('items',[]), True, d.get('saved_date',''), oa, obb)

def _qty_to_pieces(order):
    """품목별 수량(개수 기반) → 배치할 조각 리스트 반환
    단위(unit) 미만 수량도 그대로 개수로 처리.
    예) 오토만 unit=4, qty=10 → 10개 배치 (블록 깊이 방향 패킹은 SVG 외부)
    """
    pieces = []
    for code, qty in order.items():
        if qty <= 0:
            continue
        unit = ITEM_MASTER[code]['unit']
        info = ITEM_MASTER[code]
        # qty개를 unit개씩 묶어 블록을 채움
        # 단, qty가 unit의 배수가 아닌 경우 마지막 조각을 부분 조각으로 처리
        full_cuts = qty // unit         # 완전한 단위 묶음 수 (블록 2D에 올라가는 조각)
        partial   = qty % unit          # 나머지 개수
        for _ in range(full_cuts):
            pieces.append((code, unit, False))   # (품목코드, 조각에 포함된 개수, 부분여부)
        if partial > 0:
            pieces.append((code, partial, True))
    # 넓은 면적 우선 정렬
    pieces.sort(key=lambda x: ITEM_MASTER[x[0]]['width']*ITEM_MASTER[x[0]]['depth'], reverse=True)
    return pieces

def pack_items(order, saved=None):
    blocks = [copy.deepcopy(s) for s in (saved or [])]
    pieces = _qty_to_pieces(order)
    bc = max((b.id for b in blocks), default=0)
    for code, cnt, is_partial in pieces:
        info = ITEM_MASTER[code]
        bb, bp = None, None
        for b in blocks:
            p = b.find_best_placement(info['width'], info['depth'])
            if p and (bp is None or p['score']<bp['score']): bb, bp = b, p
        if bb and bp:
            bb.place_item(code, bp, cnt=cnt)
        else:
            bc += 1; nb = Block(bc)
            p = nb.find_best_placement(info['width'], info['depth'])
            if p: nb.place_item(code, p, cnt=cnt); blocks.append(nb)
    return blocks

# ============================================================
# 3. 추천 엔진
# ============================================================
def get_recommendations(blocks):
    out = []
    for idx, b in enumerate(blocks):
        recs = []
        for code, info in ITEM_MASTER.items():
            p = b.find_best_placement(info['width'], info['depth'])
            if p:
                aa = p['w']*p['h']
                cy = b.yield_pct()
                ny = ((b.used_area()+aa)/BLOCK_AREA)*100
                recs.append({'code':code,'matname':info['matname'],'unit':info['unit'],
                             'width':info['width'],'depth':info['depth'],'height':info['height'],
                             'cy':cy,'ny':ny,'inc':ny-cy})
        recs.sort(key=lambda x: x['inc'], reverse=True)
        if recs: out.append({'bidx':idx, 'cy':b.yield_pct(), 'recs':recs[:3]})
    return out

# ============================================================
# 4. 저장/로드 (Google Sheets + 로컬 fallback)
# ============================================================
import gspread
from google.oauth2.service_account import Credentials

@st.cache_resource
def _get_gsheet():
    """Google Sheets 연결"""
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"])
        gc = gspread.authorize(creds)
        sheet_id = st.secrets.get("SHEET_ID", "")
        if not sheet_id:
            return None
        return gc.open_by_key(sheet_id)
    except Exception:
        return None

def _gs_read(tab_name):
    """시트 탭에서 JSON 문자열 읽기"""
    try:
        wb = _get_gsheet()
        if wb:
            ws = wb.worksheet(tab_name)
            val = ws.acell('A1').value
            if val:
                return json.loads(val)
    except Exception:
        pass
    return None

def _gs_write(tab_name, data):
    """시트 탭에 JSON 문자열 쓰기"""
    try:
        wb = _get_gsheet()
        if wb:
            ws = wb.worksheet(tab_name)
            ws.update('A1', [[json.dumps(data, ensure_ascii=False)]])
            return True
    except Exception:
        pass
    return False

# ── 잔여 블록 ──
def load_saved():
    # Google Sheets 우선
    data = _gs_read('residual_blocks')
    if data and isinstance(data, list):
        try:
            return [Block.from_dict(d) for d in data]
        except: pass
    # 로컬 fallback
    if os.path.exists(STORAGE_FILE):
        try:
            with open(STORAGE_FILE,'r',encoding='utf-8') as f:
                return [Block.from_dict(d) for d in json.load(f)]
        except: pass
    return []

def _save_raw(data):
    # Google Sheets 저장
    _gs_write('residual_blocks', data)
    # 로컬도 저장
    try:
        os.makedirs(STORAGE_DIR, exist_ok=True)
        with open(STORAGE_FILE,'w',encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except: pass

def add_saved(block):
    raw = []
    gs_data = _gs_read('residual_blocks')
    if gs_data and isinstance(gs_data, list):
        raw = gs_data
    elif os.path.exists(STORAGE_FILE):
        try:
            with open(STORAGE_FILE,'r',encoding='utf-8') as f: raw = json.load(f)
        except: pass
    nid = max((d['id'] for d in raw), default=0)+1
    d = block.to_dict(); d['id']=nid; d['is_saved']=True
    d['saved_date'] = now_kst().strftime('%Y-%m-%d %H:%M')
    raw.append(d); _save_raw(raw)

def remove_saved(bid):
    raw = []
    gs_data = _gs_read('residual_blocks')
    if gs_data and isinstance(gs_data, list):
        raw = gs_data
    elif os.path.exists(STORAGE_FILE):
        try:
            with open(STORAGE_FILE,'r',encoding='utf-8') as f: raw = json.load(f)
        except: pass
    _save_raw([d for d in raw if d['id']!=bid])

# ── 현장 배포 + 이력 ──
def save_plan(blocks, input_slots):
    """관리자가 확정한 재단 계획 저장 + 이력 기록"""
    now = now_kst().strftime('%Y-%m-%d %H:%M:%S')
    avg_y = sum(b.yield_pct() for b in blocks) / len(blocks) if blocks else 0
    total_u = sum(input_slots.get(c,0) * ITEM_MASTER[c]['unit'] for c in ITEM_MASTER if input_slots.get(c,0)>0)

    plan = {
        'saved_at': now,
        'input_slots': {c: input_slots.get(c, 0) for c in ITEM_MASTER},
        'blocks': [b.to_dict() for b in blocks],
    }
    # Google Sheets + 로컬
    _gs_write('current_plan', plan)
    try:
        os.makedirs(STORAGE_DIR, exist_ok=True)
        with open(PLAN_FILE, 'w', encoding='utf-8') as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
    except: pass

    # 배포 이력 — 블록별 기록
    block_logs = []
    for bi, b in enumerate(blocks):
        block_items = []
        for it in b.items:
            info = ITEM_MASTER.get(it['code'])
            if info:
                block_items.append({
                    'matcd': info['matcd'], 'matcol': info['matcol'],
                    'matname': info['matname'], 'unit': info['unit'],
                })
        label = "잔여" if b.is_saved else "Block"
        block_logs.append({
            'block_no': bi+1, 'block_type': label,
            'yield': round(b.yield_pct(), 1), 'items': block_items,
        })

    log_entry = {
        'deployed_at': now, 'n_blocks': len(blocks),
        'avg_yield': round(avg_y, 1), 'total_units': total_u,
        'blocks': block_logs,
    }

    # 이력 저장 — Google Sheets에 행 추가
    _gs_append_deploy_rows(block_logs, now, len(blocks), round(avg_y, 1), total_u)

    # 로컬 백업
    try:
        history = []
        if os.path.exists(DEPLOY_LOG_FILE):
            with open(DEPLOY_LOG_FILE,'r',encoding='utf-8') as f:
                history = json.load(f)
        history.append(log_entry)
        with open(DEPLOY_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except: pass

def load_plan():
    """저장된 재단 계획 로드"""
    plan = _gs_read('current_plan')
    if not plan:
        try:
            if os.path.exists(PLAN_FILE):
                with open(PLAN_FILE,'r',encoding='utf-8') as f:
                    plan = json.load(f)
        except: plan = None
    if plan:
        try:
            blocks = [Block.from_dict(d) for d in plan.get('blocks',[])]
            for b in blocks:
                b.is_saved = False
            return plan, blocks
        except: pass
    return None, []

def _gs_append_deploy_rows(block_logs, deployed_at, n_blocks, avg_yield, total_units):
    """deploy_history 시트에 행 단위로 추가 (블록×품목별 1행)"""
    try:
        wb = _get_gsheet()
        if not wb:
            return False
        ws = wb.worksheet('deploy_history')
        # 헤더가 없으면 추가
        first = ws.acell('A1').value
        if not first:
            ws.update('A1:I1', [['배포일시','블록수','평균수율(%)','총생산수량','블록','블록수율(%)','자재코드','품목명','생산수량']])

        # 행 추가
        rows = []
        for bl in block_logs:
            if bl.get('items'):
                for item in bl['items']:
                    rows.append([
                        deployed_at, n_blocks, avg_yield, total_units,
                        f"{bl['block_type']} #{bl['block_no']}", bl['yield'],
                        item.get('matcd',''), item.get('matname',''), item.get('unit','')
                    ])
            else:
                rows.append([
                    deployed_at, n_blocks, avg_yield, total_units,
                    f"{bl['block_type']} #{bl['block_no']}", bl['yield'],
                    '', '', ''
                ])
        if rows:
            ws.append_rows(rows, value_input_option='USER_ENTERED')
        return True
    except Exception:
        return False

def load_deploy_history_from_sheet():
    """deploy_history 시트에서 행 단위로 읽기"""
    try:
        wb = _get_gsheet()
        if not wb:
            return None
        ws = wb.worksheet('deploy_history')
        all_rows = ws.get_all_records()
        if all_rows:
            return all_rows
    except Exception:
        pass
    return None

def delete_deploy_rows_by_no(deploy_no):
    """특정 배포No의 행들을 시트에서 삭제"""
    try:
        wb = _get_gsheet()
        if not wb:
            return False
        ws = wb.worksheet('deploy_history')
        all_vals = ws.get_all_values()
        if len(all_vals) <= 1:
            return False  # 헤더만 있음

        # 배포일시 기준으로 그룹핑해서 No 매기기
        header = all_vals[0]
        data_rows = all_vals[1:]
        # 배포일시 목록 (유니크, 순서 유지)
        seen = []
        for r in data_rows:
            dt = r[0]
            if dt not in seen:
                seen.append(dt)
        if deploy_no < 1 or deploy_no > len(seen):
            return False
        target_dt = seen[deploy_no - 1]

        # 삭제할 행 인덱스 (1-based, 헤더=1)
        del_indices = [i+2 for i, r in enumerate(data_rows) if r[0] == target_dt]
        for idx in reversed(del_indices):
            ws.delete_rows(idx)
        return True
    except Exception:
        return False

# ============================================================
# 5. SVG 시각화
# ============================================================
DARK_TEXT_COLORS = {'#E74C3C','#3498DB','#2ECC71','#1ABC9C','#9B59B6','#1E8449','#8E44AD','#2980B9'}

def make_svg(block, idx, saved_label=None):
    uid = f"b{idx}"
    yv = block.yield_pct()
    yc = '#27AE60' if yv>=80 else '#F39C12' if yv>=60 else '#E74C3C'
    block_label = saved_label if saved_label else f"Block #{idx}"

    bb_x, bb_y, bb_w, bb_h = block.bounding_box()

    # 항상 동일한 viewBox: 전체 블록 기준 스케일
    PL, PT, PR, PB = 50, 30, 15, 20
    S = 0.26
    # viewBox는 항상 전체 블록 기준
    vw = int(BLOCK_W * S) + PL + PR
    vh = int(BLOCK_H * S) + PT + PB
    # 실제 그리는 블록 크기
    bw, bh = int(bb_w * S), int(bb_h * S)
    ox, oy = PL, PT
    bw, bh = int(bb_w*S), int(bb_h*S)
    ox, oy = PL, PT

    svg = [f'<svg viewBox="0 0 {vw} {vh}" xmlns="http://www.w3.org/2000/svg" '
           f'style="width:100%;max-width:340px;height:auto;display:block;margin:0 auto;'
           f'font-family:Malgun Gothic,NanumGothic,sans-serif;">']

    svg.append(f'<defs><pattern id="h{uid}" patternUnits="userSpaceOnUse" width="8" height="8" '
               f'patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" stroke="#CCC" stroke-width="1"/>'
               f'</pattern><clipPath id="c{uid}"><rect x="{ox}" y="{oy}" width="{bw}" height="{bh}"/></clipPath>'
               f'<marker id="arr{uid}" markerWidth="6" markerHeight="4" refX="5" refY="2" orient="auto">'
               f'<path d="M0,0 L6,2 L0,4" fill="#999"/></marker></defs>')

    # ── 타이틀 ──
    svg.append(f'<text x="{ox+bw/2}" y="{oy-10}" text-anchor="middle" font-size="16" font-weight="bold" fill="#333">'
               f'{block_label}  <tspan fill="{yc}" font-size="17">수율 {yv:.1f}%</tspan></text>')

    # ── 블록 외곽 ──
    svg.append(f'<rect x="{ox}" y="{oy}" width="{bw}" height="{bh}" fill="#F5F6F7" stroke="#34495E" stroke-width="2"/>')

    # ── 치수선: 가로 (하단) ──
    dy = oy + bh + 10
    svg.append(f'<line x1="{ox}" y1="{dy}" x2="{ox+bw}" y2="{dy}" stroke="#999" stroke-width="1" '
               f'marker-start="url(#arr{uid})" marker-end="url(#arr{uid})" '
               f'style="transform:scaleX(1)"/>')
    # 양 끝 꺾임선
    svg.append(f'<line x1="{ox}" y1="{oy+bh+2}" x2="{ox}" y2="{dy+4}" stroke="#999" stroke-width="0.7"/>')
    svg.append(f'<line x1="{ox+bw}" y1="{oy+bh+2}" x2="{ox+bw}" y2="{dy+4}" stroke="#999" stroke-width="0.7"/>')
    svg.append(f'<text x="{ox+bw/2}" y="{dy-2}" text-anchor="middle" font-size="11" fill="#777">{bb_w}</text>')

    # ── 치수선: 세로 (좌측) ──
    dx = ox - 12
    svg.append(f'<line x1="{dx}" y1="{oy}" x2="{dx}" y2="{oy+bh}" stroke="#999" stroke-width="1" '
               f'marker-start="url(#arr{uid})" marker-end="url(#arr{uid})"/>')
    svg.append(f'<line x1="{ox-2}" y1="{oy}" x2="{dx-4}" y2="{oy}" stroke="#999" stroke-width="0.7"/>')
    svg.append(f'<line x1="{ox-2}" y1="{oy+bh}" x2="{dx-4}" y2="{oy+bh}" stroke="#999" stroke-width="0.7"/>')
    svg.append(f'<text x="{dx-4}" y="{oy+bh/2}" text-anchor="middle" font-size="11" fill="#777" '
               f'transform="rotate(-90,{dx-4},{oy+bh/2})">{bb_h}</text>')

    # ── 클리핑 ──
    svg.append(f'<g clip-path="url(#c{uid})">')

    # 빈 영역
    for fr in block.free_rects:
        fx, fy = ox+int((fr.x-bb_x)*S), oy+int((fr.y-bb_y)*S)
        fw, fh = int(fr.w*S), int(fr.h*S)
        svg.append(f'<rect x="{fx}" y="{fy}" width="{fw}" height="{fh}" '
                   f'fill="url(#h{uid})" stroke="#CCC" stroke-width="0.8" stroke-dasharray="4,2"/>')
        if fw > 40 and fh > 20:
            svg.append(f'<text x="{fx+fw/2}" y="{fy+fh/2}" text-anchor="middle" dominant-baseline="middle" '
                       f'font-size="11" fill="#BBB" font-style="italic">{fr.w}×{fr.h}</text>')

    # 품목
    for it_idx, it in enumerate(block.items):
        info = ITEM_MASTER.get(it['code'])
        if not info: continue
        ix, iy = ox+int((it['x']-bb_x)*S), oy+int((it['y']-bb_y)*S)
        iw, ih = int(it['w']*S), int(it['h']*S)
        col = info['color']

        # 아이템별 클리핑 (텍스트 넘침 방지)
        cid = f"ic{uid}_{it_idx}"
        svg.append(f'<clipPath id="{cid}"><rect x="{ix}" y="{iy}" width="{iw}" height="{ih}"/></clipPath>')
        svg.append(f'<g clip-path="url(#{cid})">')

        svg.append(f'<rect x="{ix+1}" y="{iy+1}" width="{iw-2}" height="{ih-2}" '
                   f'fill="{col}" stroke="#2C3E50" stroke-width="1.3" rx="3" opacity="0.9"/>')

        cx, cy_ = ix+iw/2, iy+ih/2
        mn, mx = min(iw,ih), max(iw,ih)
        rot = ih > iw*1.3
        # 텍스트 방향의 가용 폭/높이
        tw = mx if rot else iw  # 텍스트 가용 폭
        th = mn if rot else ih  # 텍스트 가용 높이

        # 품명 글자수 기반 폰트 계산 (한글 1자 ≈ 0.7*fs)
        label = f"[{it['code']}] {info['matname']}"
        nchars = len(label)
        fs_by_w = int(tw * 0.85 / max(nchars * 0.7, 1))
        fs_by_h = int(th / 5)
        fs = max(10, min(18, fs_by_w, fs_by_h))
        fs2 = max(8, int(fs * 0.78))

        ra = f'transform="rotate(-90,{cx},{cy_})"' if rot else ''
        tc = "#FFF" if col in DARK_TEXT_COLORS else "#2C3E50"

        # 변 치수 (가로 상단, 세로 우측)
        dfs = max(8, min(11, int(mn/10)))
        if iw > 28:
            svg.append(f'<text x="{ix+iw/2}" y="{iy+dfs+1}" text-anchor="middle" '
                       f'font-size="{dfs}" fill="{tc}" opacity="0.55">← {it["w"]} →</text>')
        if ih > 28:
            svg.append(f'<text x="{ix+iw-3}" y="{iy+ih/2}" text-anchor="middle" '
                       f'font-size="{dfs}" fill="{tc}" opacity="0.55" '
                       f'transform="rotate(-90,{ix+iw-3},{iy+ih/2})">← {it["h"]} →</text>')

        # 중앙 텍스트
        svg.append(f'<g {ra}>')
        if th > 40:
            svg.append(f'<text x="{cx}" y="{cy_-fs*0.5}" text-anchor="middle" dominant-baseline="middle" '
                       f'font-size="{fs}" font-weight="bold" fill="{tc}">{label}</text>')
            detail = f'{info["width"]}×{info["depth"]}×{info["height"]} ({info["unit"]}개)'
            svg.append(f'<text x="{cx}" y="{cy_+fs*0.45}" text-anchor="middle" dominant-baseline="middle" '
                       f'font-size="{fs2}" fill="{tc}" opacity="0.85">{detail}</text>')
        else:
            svg.append(f'<text x="{cx}" y="{cy_}" text-anchor="middle" dominant-baseline="middle" '
                       f'font-size="{fs}" font-weight="bold" fill="{tc}">{label}</text>')
        svg.append('</g>')
        svg.append('</g>')  # item clip end

    svg.append('</g>')
    svg.append('</svg>')
    return '\n'.join(svg), vh  # SVG html + 실제 높이(px)


# ============================================================
# 6. 콜백 함수 (반드시 위젯 생성 전에 정의)
# ============================================================
def _place_in_block(bidx, code, unit):
    """추천 클릭: 해당 블록에만 끼워넣기 + 히스토리 저장"""
    # 현재 상태를 히스토리에 저장 (undo용)
    if '_blocks' in st.session_state:
        hist = st.session_state.get('_history', [])
        hist.append({
            'blocks': copy.deepcopy(st.session_state['_blocks']),
            'qty': {c: st.session_state.get(f"qty_{c}", 0) for c in ITEM_MASTER}
        })
        st.session_state['_history'] = hist[-10:]  # 최대 10단계
    st.session_state['_rec'] = {'bidx': bidx, 'code': code}
    st.session_state[f"qty_{code}"] = st.session_state.get(f"qty_{code}", 0) + 1

def _undo():
    """마지막 추천 취소"""
    hist = st.session_state.get('_history', [])
    if hist:
        prev = hist.pop()
        st.session_state['_blocks'] = prev['blocks']
        for c, v in prev['qty'].items():
            st.session_state[f"qty_{c}"] = v
        st.session_state['_history'] = hist

def _add_qty(code, unit):
    st.session_state[f"qty_{code}"] = st.session_state.get(f"qty_{code}", 0) + unit

def _reset_qty():
    for c in ITEM_MASTER:
        st.session_state[f"qty_{c}"] = 0
    st.session_state.pop('_blocks', None)
    st.session_state.pop('_history', None)

def _apply_erp_qty(qty_map):
    """ERP 조회 결과를 사이드바 수량에 반영"""
    for code, qty in qty_map.items():
        st.session_state[f"qty_{code}"] = qty
    st.session_state.pop('_blocks', None)
    st.session_state.pop('_history', None)



# ============================================================
# 7. CSS
# ============================================================
st.markdown("""<style>
body, .stApp { background: #FAF8F6 !important; }
.saved-card {
    background: #FFF8E1; border: 1px solid #FFCC80;
    border-radius: 8px; padding: 6px 10px; margin: 3px 0; font-size: 0.82rem;
}
div[data-testid="stSidebar"] { background: linear-gradient(180deg, #2C2825, #413C37); }
div[data-testid="stSidebar"] label, div[data-testid="stSidebar"] p,
div[data-testid="stSidebar"] span, div[data-testid="stSidebar"] h1,
div[data-testid="stSidebar"] h2, div[data-testid="stSidebar"] h3 { color: #F3F0ED !important; }

/* 상단 헤더에 브랜딩 삽입 */
header[data-testid="stHeader"] {
    background: #FAF8F6 !important;
    border-bottom: 1px solid #ECE8E4;
}
header[data-testid="stHeader"]::after {
    content: "FURSYS GROUP · iloom  |  소파 스펀지 재단 시뮬레이션";
    position: absolute;
    left: 20px;
    top: 50%;
    transform: translateY(-50%);
    font-size: 13px;
    font-weight: 600;
    color: #7A736B;
    letter-spacing: 0.5px;
}
.block-container { padding-top: 4rem !important; }
/* 사이드바 상단 여백 강제 제거 */
section[data-testid="stSidebar"] > div { padding-top: 0rem !important; margin-top: 0 !important; }
section[data-testid="stSidebar"] .block-container { padding-top: 0 !important; }
div[data-testid="stSidebarContent"] { padding-top: 0 !important; }
div[data-testid="stSidebarUserContent"] { padding-top: 0 !important; }
div[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"]:first-child { margin-top: -1rem; }
</style>""", unsafe_allow_html=True)


# ============================================================
# 8. 세션 초기화
# ============================================================
for code in ITEM_MASTER:
    if f"qty_{code}" not in st.session_state:
        st.session_state[f"qty_{code}"] = 0

# ── URL에서 pono 자동 감지 (ERP → 앱 링크) ──
# 반드시 사이드바(number_input 위젯) 렌더링 전에 session_state를 세팅해야
# StreamlitAPIException 방지
try:
    url_pono = st.query_params.get("pono", "")
except AttributeError:
    url_pono = st.experimental_get_query_params().get("pono", [""])[0]

if url_pono and st.session_state.get('_auto_pono') != url_pono:
    st.session_state['_auto_pono'] = url_pono
    with st.spinner(f"📡 구매의뢰 {url_pono} 자동 조회 중..."):
        data, err = call_erp_query("PAN", url_pono)
    if err:
        st.error(err)
    elif data:
        st.session_state['_erp_data'] = data
        st.session_state['_erp_pono'] = url_pono
        st.session_state['_erp_storecd'] = "PAN"
        qty_map = match_erp_to_items(data)
        for code, qty in qty_map.items():
            st.session_state[f"qty_{code}"] = qty   # 위젯 렌더 전이므로 직접 할당 OK
        saved_blocks_for_auto = load_saved()
        ld_auto = [copy.deepcopy(s) for s in saved_blocks_for_auto] if saved_blocks_for_auto else None
        auto_blocks = pack_items(qty_map, ld_auto)
        if auto_blocks:
            avg_y_before = sum(b.yield_pct() for b in auto_blocks) / len(auto_blocks)
            before_result = round(avg_y_before, 2)
            call_erp_update_before_result("PAN", url_pono, before_result)
            st.toast(f"✅ 구매의뢰 {url_pono} 자동 로드 | 사전수율 {before_result}% 전송 완료")
        st.rerun()


# ============================================================
# 9. 사이드바
# ============================================================
with st.sidebar:
    # 사이드바 버튼 폰트 축소 CSS
    st.markdown("""<style>
    section[data-testid="stSidebar"] button p { font-size: 0.72rem !important; }
    section[data-testid="stSidebar"] button { padding: 4px 6px !important; min-height: 0 !important; }
    </style>""", unsafe_allow_html=True)

    st.markdown("### ⚙️ 시스템 제어")
    sc1, sc2 = st.columns(2)
    with sc1:
        st.button("🧹 수량 초기화", use_container_width=True, on_click=_reset_qty)
    with sc2:
        def _toggle_log():
            st.session_state['_view_log'] = not st.session_state.get('_view_log', False)
        st.button("📊 수율 로그", key="show_log", use_container_width=True, on_click=_toggle_log)

    st.divider()
    st.markdown("### 📋 품목별 생산 수량 입력")
    st.caption("개수(ea) 직접 입력 — 단위 미만도 허용")
    input_slots = {}
    for code, info in ITEM_MASTER.items():
        unit = info['unit']
        val  = st.session_state.get(f"qty_{code}", 0)
        is_mismatch = val > 0 and val % unit != 0

        # 입력 + 단위 표시를 한 줄에 배치
        col_inp, col_tag = st.columns([3, 1])
        with col_inp:
            input_slots[code] = st.number_input(
                f"[{code}] {info['matname']}",
                min_value=0, step=1, key=f"qty_{code}",
                help=f"📦 {info['matcd']}\n📐 {info['width']}×{info['depth']}×{info['height']}mm",
                label_visibility="visible")
        with col_tag:
            if is_mismatch:
                blocks_needed = math.ceil(val / unit)
                waste = blocks_needed * unit - val
                st.markdown(
                    f"<div style='padding-top:28px;font-size:0.72rem;color:#E67E22;line-height:1.3;'>"
                    f"단위:{unit}개<br>⚠️+{waste}</div>",
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    f"<div style='padding-top:28px;font-size:0.72rem;color:#9E9E9E;'>"
                    f"단위:{unit}개</div>",
                    unsafe_allow_html=True)

    st.divider()
    st.markdown("### 📦 저장된 잔여 블록")
    saved_blocks = load_saved()
    use_saved = False
    if saved_blocks:
        st.caption(f"{len(saved_blocks)}개 저장됨")
        use_saved = st.toggle("🔄 잔여 블록 우선 사용", value=True, key="use_saved")
        for sb in saved_blocks:
            us = sb.get_usable_free_rects()
            ri = ", ".join(f"{r.w}×{r.h}" for r in us[:3]) if us else "없음"
            st.markdown(f'<div class="saved-card"><b>잔여 #{sb.id}</b> · {ri}<br>'
                        f'<small style="color:#999">{sb.saved_date}</small></div>',
                        unsafe_allow_html=True)
            if st.button(f"🗑️ 삭제 #{sb.id}", key=f"del_{sb.id}", use_container_width=True):
                remove_saved(sb.id); st.rerun()
    else:
        st.caption("저장된 잔여 블록 없음")

    st.divider()


# ============================================================
# 수율 로그 전체 화면
# ============================================================
if st.session_state.get('_view_log'):
    st.markdown("""<style>
    div[data-testid="stSidebar"] { display: none !important; }
    .block-container { max-width: 950px !important; padding-top: 3rem !important; }
    </style>""", unsafe_allow_html=True)

    st.markdown("### 📊 수율 배포 이력")

    def _close_log():
        st.session_state['_view_log'] = False
    st.button("← 시뮬레이션으로 돌아가기", key="back_from_log", on_click=_close_log)

    # Google Sheets에서 행 단위로 읽기 (순서 기반 파싱)
    # 시트 컬럼 순서: 배포일시(0) 블록수(1) 평균수율(2) 총생산수량(3)
    #                블록넘버(4) 블록당수율(5) 자재코드(6) 자재명(7) 생산수량(8)
    SHEET_COLS = ['배포일시','블록수','평균수율','총생산수량','블록넘버','블록당수율','자재코드','자재명','생산수량']
    sheet_rows = load_deploy_history_from_sheet()
    if sheet_rows:
        # get_all_records()는 dict 반환 → values()로 순서 기반 재구성
        rows_by_order = []
        for row in sheet_rows:
            vals = list(row.values())
            # 컬럼 수가 맞지 않으면 빈 값으로 채움
            while len(vals) < len(SHEET_COLS):
                vals.append('')
            rows_by_order.append(dict(zip(SHEET_COLS, vals[:len(SHEET_COLS)])))
        hist_df = pd.DataFrame(rows_by_order)

        # 배포No 추가 (배포일시 기준 그룹)
        unique_dates = []
        for dt in hist_df['배포일시']:
            if dt not in unique_dates:
                unique_dates.append(dt)
        hist_df['배포No'] = hist_df['배포일시'].map(lambda x: unique_dates.index(x)+1)

        display_cols = ['배포No','배포일시','블록넘버','블록당수율','자재코드','자재명','생산수량']
        st.dataframe(hist_df[display_cols], use_container_width=True, hide_index=True, height=500)

        n_deploys = len(unique_dates)
        try:
            hist_df['블록당수율'] = pd.to_numeric(hist_df['블록당수율'], errors='coerce')
            avg_yields = (hist_df.drop_duplicates(subset=['배포일시','블록넘버'])
                          .groupby('배포일시')['블록당수율'].mean())
            total_avg = avg_yields.mean() if len(avg_yields) > 0 else 0
        except Exception:
            total_avg = 0
        st.caption(f"총 {n_deploys}건 배포 | 평균 수율 {total_avg:.1f}%")

        dc1, dc2, dc3 = st.columns([2, 1, 1])
        with dc1:
            del_idx = st.number_input("삭제할 배포No 입력", min_value=1, max_value=n_deploys,
                                      value=1, step=1, key="del_log_no")
        with dc2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🗑️ 선택 삭제", key="del_log_btn", use_container_width=True):
                if delete_deploy_rows_by_no(del_idx):
                    st.toast(f"배포 #{del_idx} 삭제 완료")
                    _get_gsheet.clear()  # 캐시 초기화
                    st.rerun()
        with dc3:
            st.markdown("<br>", unsafe_allow_html=True)
            buf = BytesIO()
            hist_df[display_cols].to_excel(buf, index=False, engine='openpyxl')
            buf.seek(0)
            st.download_button("📥 엑셀 다운로드", data=buf,
                               file_name=f"수율이력_{now_kst().strftime('%Y%m%d')}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True)
    else:
        st.info("배포 이력이 없습니다. '현장 배포' 버튼을 누르면 기록됩니다.")

    st.stop()


# ============================================================
# 10. 뷰 모드 분기 (세션 기반)
# ============================================================
try:
    VIEW_MODE = st.query_params.get("view", "admin")
except AttributeError:
    params = st.experimental_get_query_params()
    VIEW_MODE = params.get("view", ["admin"])[0]

if VIEW_MODE == "floor":
    # ══════════════════════════════════════
    # 현장 전용 화면 (보기 전용)
    # ══════════════════════════════════════
    # 사이드바 숨김
    st.markdown("""<style>
        div[data-testid="stSidebar"] { display: none; }
        .block-container { max-width: 100% !important; padding: 1rem 2rem !important; }
        body, .stApp { background: #FAF8F6 !important; }
    </style>""", unsafe_allow_html=True)

    plan, floor_blocks = load_plan()
    if plan and floor_blocks:
        st.markdown(f"""
        <div style="text-align:center; padding:8px 0;">
            <div>
                <span style="font-size:12px; font-weight:700; letter-spacing:2px; color:#7A736B;">FURSYS GROUP</span>
                <span style="font-size:24px; font-weight:700; color:#dc2626; font-family:Georgia,serif; margin-left:8px;">iloom</span>
            </div>
            <h1 style="margin:8px 0 0; color:#1A1816; font-size:1.8rem;">📐 재단 배치도</h1>
            <p style="color:#A8A098; margin:4px 0; font-size:0.9rem;">배포 시각: {plan['saved_at']}
            &nbsp;|&nbsp; 블록 {len(floor_blocks)}개
            &nbsp;|&nbsp; 평균 수율 {sum(b.yield_pct() for b in floor_blocks)/len(floor_blocks):.1f}%</p>
        </div>""", unsafe_allow_html=True)

        # SVG를 크게 (max-width 제거)
        for row in range(math.ceil(len(floor_blocks)/3)):
            cols = st.columns(3)
            for ci in range(3):
                bi = row*3+ci
                if bi < len(floor_blocks):
                    with cols[ci]:
                        svg, svg_h = make_svg(floor_blocks[bi], bi+1)
                        svg = svg.replace('max-width:340px;', 'max-width:100%;')
                        components.html(
                            f'<div style="text-align:center;">{svg}</div>',
                            height=int(svg_h*1.15)+30, scrolling=False)

        # 인쇄 버튼만
        st.divider()
        pc1, pc2, pc3 = st.columns([4, 1, 4])
        with pc2:
            def _fp():
                st.session_state['_fpts'] = datetime.now().isoformat()
            st.button("🖨️ 인쇄", key="fp", use_container_width=True, on_click=_fp)

        fpts = st.session_state.pop('_fpts', None)
        if fpts:
            all_svgs_f = [make_svg(floor_blocks[i], i+1)[0].replace('max-width:340px;','') for i in range(len(floor_blocks))]
            svg_cells = ""
            for i, s in enumerate(all_svgs_f):
                safe = s.replace('\\','\\\\').replace('`','\\`').replace('${','\\${')
                svg_cells += f'<div class="cell">{safe}</div>'
                if (i+1) % 3 == 0:
                    svg_cells += '<div style="clear:both;"></div>'
            components.html("""<script>
            var w=window.open('','_blank','width=1100,height=800');
            if(w){w.document.write(`<!DOCTYPE html><html><head><title>재단 배치도</title>
            <style>*{margin:0;padding:0;box-sizing:border-box;}body{margin:8mm;font-family:Malgun Gothic,sans-serif;}
            h2{text-align:center;font-size:18pt;margin-bottom:6mm;}
            .cell{width:33.33%;display:inline-block;vertical-align:top;padding:2mm;}
            svg{width:100%!important;max-width:none!important;height:auto!important;display:block;}
            @page{size:A4 landscape;margin:5mm;}</style></head><body>
            <h2>소파 스펀지 재단 배치도</h2><div>""" + svg_cells.replace('`','\\`') + """</div>
            </body></html>`);w.document.close();setTimeout(function(){w.print();},500);}
            </script><!-- """ + fpts + """ -->""", height=0)

        st.caption("v3.0 | 현장 전용 화면 (보기 전용)")
    else:
        st.warning("⚠️ 배포된 재단 계획이 없습니다. 관리자가 먼저 '현장 배포' 버튼을 눌러야 합니다.")

    st.stop()  # 현장 모드는 여기서 끝

# ══════════════════════════════════════
# 관리자 화면
# ══════════════════════════════════════

total_items = sum(input_slots.values())

# ── ERP 구매의뢰 조회 (항상 표시) ──
with st.expander("📡 ERP 구매의뢰 조회 → 수량 자동 반영", expanded=False):
    erp_c1, erp_c2, erp_c3 = st.columns([1.5, 1.5, 1])
    with erp_c1:
        storecd = st.text_input("사업장코드", value="PAN", key="erp_store")
    with erp_c2:
        pono = st.text_input("구매의뢰번호", value="", key="erp_pono", placeholder="예: B20260200002")
    with erp_c3:
        st.markdown("<br>", unsafe_allow_html=True)
        erp_query = st.button("🔍 조회", key="erp_query", use_container_width=True)

    if erp_query:
        if not pono:
            st.warning("구매의뢰번호를 입력하세요.")
        else:
            with st.spinner("ERP 조회 중..."):
                data, err = call_erp_query(storecd, pono)
            if err:
                st.error(err)
            elif data:
                st.session_state['_erp_data'] = data
                st.session_state['_erp_pono'] = pono
                st.session_state['_erp_storecd'] = storecd
                st.success(f"✅ {len(data)}건 조회됨")
            else:
                st.warning("조회 결과가 없습니다.")

    erp_data = st.session_state.get('_erp_data')
    if erp_data:
        df = erp_data_to_dataframe(erp_data)
        df['품목매칭'] = df['자재코드'].map(
            lambda x: f"[{MATCODE_TO_ITEM[x]}] {ITEM_MASTER[MATCODE_TO_ITEM[x]]['matname']}"
            if x in MATCODE_TO_ITEM else "—")

        # ── 단위 불일치 표시 포함 df 출력 ──
        qty_map = match_erp_to_items(erp_data)
        def _unit_status(row):
            mc = row.get('자재코드', '') if isinstance(row, dict) else row['자재코드'] if '자재코드' in row.index else ''
            qty = int(row.get('의뢰수량', 0) if isinstance(row, dict) else row['의뢰수량'])
            if mc not in MATCODE_TO_ITEM:
                return "—"
            unit = ITEM_MASTER[MATCODE_TO_ITEM[mc]]['unit']
            if qty % unit == 0:
                return f"✅ {qty//unit}블록"
            else:
                blocks_n = math.ceil(qty / unit)
                waste = blocks_n * unit - qty
                return f"⚠️ {blocks_n}블록 (+{waste}개 여유)"
        df['단위검토'] = df.apply(_unit_status, axis=1)
        st.dataframe(df, use_container_width=True, hide_index=True)

        matched = {c: q for c, q in qty_map.items() if q > 0}
        if matched:
            parts = []
            for c, q in matched.items():
                unit = ITEM_MASTER[c]['unit']
                if q % unit == 0:
                    parts.append(f"[{c}] {q}개 ({q//unit}블록 ✅)")
                else:
                    blocks_n = math.ceil(q / unit)
                    waste = blocks_n * unit - q
                    parts.append(f"[{c}] {q}개 ({blocks_n}블록, +{waste}개 여유 ⚠️)")
            st.info("🔗 매칭 결과: " + " / ".join(parts))

        bc1, bc2, bc3 = st.columns(3)
        with bc1:
            if matched:
                st.button("📥 사이드바 수량에 반영", key="erp_apply", use_container_width=True,
                          on_click=_apply_erp_qty, args=(qty_map,))
            else:
                st.caption("매칭되는 품목이 없습니다")
        with bc2:
            buf = BytesIO()
            df.to_excel(buf, index=False, engine='openpyxl')
            buf.seek(0)
            st.download_button("📊 엑셀 다운로드", data=buf,
                               file_name=f"ERP구매의뢰_{now_kst().strftime('%Y%m%d_%H%M')}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True)

        # ══════════════════════════════════════════════════════
        # ── 수량 수정 기능 (update-poqty) ──
        # ══════════════════════════════════════════════════════
        st.divider()
        st.markdown("##### ✏️ 수량 수정 (ERP 전송)")
        st.caption("조회된 자재의 의뢰수량을 수정하고 ERP에 반영합니다.")

        # 수정 대상 선택 (자재코드+순번으로 구분)
        erp_pono_cur  = st.session_state.get('_erp_pono', '')
        erp_store_cur = st.session_state.get('_erp_storecd', 'PAN')

        # 자재 선택 selectbox용 옵션 구성
        row_options = {}
        for row in erp_data:
            matcd  = row.get('matcd', '')
            matcol = row.get('matcol', 'XX')
            po_seq = row.get('po_seq', 0)
            matname = row.get('matname') or (ITEM_MASTER[MATCODE_TO_ITEM[matcd]]['matname'] if matcd in MATCODE_TO_ITEM else matcd)
            label  = f"[순번{po_seq}] {matname} ({matcd})"
            row_options[label] = row

        if row_options:
            sel_label = st.selectbox("수정할 자재 선택", list(row_options.keys()), key="upd_sel")
            sel_row   = row_options[sel_label]
            cur_qty   = int(sel_row.get('poqty', 0))
            sel_unit  = ITEM_MASTER[MATCODE_TO_ITEM.get(sel_row.get('matcd',''), sel_row.get('matcd',''))]['unit'] \
                        if sel_row.get('matcd','') in MATCODE_TO_ITEM else 1

            upd_c1, upd_c2 = st.columns([2, 1])
            with upd_c1:
                new_qty = st.number_input(
                    f"수정 수량 (현재: {cur_qty}개 / 단위: {sel_unit}개)",
                    min_value=0, value=cur_qty, step=1, key="upd_qty")
            with upd_c2:
                st.markdown("<br>", unsafe_allow_html=True)
                upd_btn = st.button("📤 전송", key="upd_send", use_container_width=True)

            if new_qty > 0 and new_qty % sel_unit != 0:
                waste_upd = math.ceil(new_qty / sel_unit) * sel_unit - new_qty
                st.warning(f"⚠️ 단위 불일치 — +{waste_upd}개 여유공간 발생")
            elif new_qty > 0:
                st.success(f"✅ 단위 일치: {new_qty // sel_unit}블록")

            if upd_btn:
                # after_result: 현재 시뮬레이션 수율 자동 사용 (없으면 0)
                _cur_blocks = st.session_state.get('_blocks', [])
                _cur_yield  = round(sum(b.yield_pct() for b in _cur_blocks) / len(_cur_blocks), 2)                               if _cur_blocks else 0.0
                _, err = call_erp_update_poqty(
                    erp_store_cur, erp_pono_cur,
                    sel_row.get('matcd',''), sel_row.get('matcol','XX'),
                    sel_row.get('po_seq', 0), new_qty, _cur_yield)
                if err:
                    st.error(f"전송 실패: {err}")
                else:
                    for r in st.session_state['_erp_data']:
                        if r.get('po_seq') == sel_row.get('po_seq') and r.get('matcd') == sel_row.get('matcd'):
                            r['poqty'] = new_qty
                            break
                    st.success(f"✅ 수량 수정 전송 완료 — {sel_row.get('matcd','')} {cur_qty} → {new_qty}개 | 수율 {_cur_yield}%")
                    st.rerun()

if total_items > 0:
    ld = [copy.deepcopy(s) for s in saved_blocks] if use_saved and saved_blocks else None

    # ── 추천 클릭 시: 해당 블록에만 끼워넣기 ──
    rec_action = st.session_state.pop('_rec', None)

    if rec_action and '_blocks' in st.session_state:
        blocks = st.session_state['_blocks']
        bidx = rec_action['bidx']
        code = rec_action['code']
        if bidx < len(blocks):
            info = ITEM_MASTER[code]
            p = blocks[bidx].find_best_placement(info['width'], info['depth'])
            if p:
                blocks[bidx].place_item(code, p)
    else:
        # ── 일반: 전체 패킹 ──
        blocks = pack_items(input_slots, ld)

    # 블록 상태 저장 (다음 추천 클릭에 사용)
    st.session_state['_blocks'] = copy.deepcopy(blocks)

    active = sum(1 for v in input_slots.values() if v > 0)
    avg_y = sum(b.yield_pct() for b in blocks)/len(blocks) if blocks else 0
    total_u = sum(input_slots[c] for c in input_slots if input_slots[c]>0)  # 실제 개수 합산
    recs = get_recommendations(blocks)

    # ── 단위 불일치 분석 ──
    # 주의: aligned_slots도 개수 기반이므로 pack_items → _qty_to_pieces를 거치면
    #       내부에서 qty÷unit을 다시 나눠 결국 같은 블록 수가 됨.
    #       따라서 "단위 맞춤 시 수율"은 aligned_slots로 직접 pack_items를 호출하되,
    #       _qty_to_pieces가 완전한 단위로만 처리하게 aligned_qty = blocks_n*unit 로 세팅.
    #       이 값은 unit의 배수이므로 _qty_to_pieces 내 partial 조각이 0이 되어
    #       현재(qty개) vs 맞춤(blocks_n*unit개) 간 실제 차이가 발생함.
    mismatch_items = []
    for c, qty in input_slots.items():
        if qty <= 0:
            continue
        unit = ITEM_MASTER[c]['unit']
        if qty % unit != 0:
            blocks_n  = math.ceil(qty / unit)
            waste     = blocks_n * unit - qty
            aligned_qty = blocks_n * unit  # unit의 정확한 배수

            # 해당 품목의 부분 조각이 들어간 블록 수율 찾기
            partial_block_yields = []
            for b in blocks:
                for it in b.items:
                    if it['code'] == c and it.get('cnt', unit) < unit:
                        partial_block_yields.append(b.yield_pct())
                        break

            # 단위 맞춤 후 전체 평균 수율
            aligned_slots = {k: v for k, v in input_slots.items()}
            aligned_slots[c] = aligned_qty
            ld2 = [copy.deepcopy(s) for s in saved_blocks] if use_saved and saved_blocks else None
            aligned_blocks = pack_items(aligned_slots, ld2)
            aligned_y = (sum(b.yield_pct() for b in aligned_blocks) / len(aligned_blocks)
                         if aligned_blocks else 0)

            mismatch_items.append({
                'code': c, 'matname': ITEM_MASTER[c]['matname'],
                'qty': qty, 'unit': unit,
                'blocks_n': blocks_n, 'waste': waste,
                'aligned_qty': aligned_qty, 'aligned_y': aligned_y,
                'partial_block_yields': partial_block_yields,  # 부분조각 포함 블록 수율
            })

    # ── 메트릭 ──
    ycolor = '#27AE60' if avg_y>=80 else '#F39C12' if avg_y>=60 else '#E74C3C'
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("투입 블록", f"{len(blocks)}개")
    c2.metric("생산 품목수", f"{active}종")
    c3.metric("평균 수율", f"{avg_y:.1f}%")
    c4.metric("총 생산수량", f"{total_u}개")

    # ── 단위 불일치 경고 + 수율 비교 ──
    if mismatch_items:
        st.markdown("---")
        st.markdown("##### ⚠️ 단위 불일치 품목 — 수율 비교")
        mc_cols = st.columns(len(mismatch_items)) if len(mismatch_items) <= 4 else st.columns(4)
        for idx, mi in enumerate(mismatch_items[:4]):
            # avg_y: 현재 전체 평균 수율 / mi['aligned_y']: 단위 맞춤 후 전체 평균 수율
            delta_y = mi['aligned_y'] - avg_y
            with mc_cols[idx]:
                # 부분조각 블록 수율 표시 문자열
                pb_str = ""
                if mi['partial_block_yields']:
                    pb_vals = ", ".join(f"{y:.1f}%" for y in mi['partial_block_yields'])
                    pb_str = f"<br><span style='color:#E67E22;font-size:0.75rem;'>⚠️ 부분조각 블록 수율: {pb_vals}</span>"
                st.markdown(f"""
<div style="background:#FFF8E1;border:1px solid #FFCC80;border-radius:8px;padding:10px;font-size:0.82rem;">
<b>[{mi['code']}] {mi['matname']}</b><br>
현재: <b>{mi['qty']}개</b> ({mi['blocks_n']}블록, 여유 +{mi['waste']}개){pb_str}<br>
단위맞춤 <b>{mi['aligned_qty']}개</b>로 수정 시:<br>
<hr style="margin:4px 0;border:none;border-top:1px solid #FFE082;">
<span style="color:#7A736B;font-size:0.75rem;">전체 평균 수율</span>
<b>{avg_y:.1f}%</b> → <b style="color:{"#27AE60" if delta_y>=0 else "#E74C3C"}">{mi['aligned_y']:.1f}%</b>
<span style="color:{"#27AE60" if delta_y>=0 else "#E74C3C"}">({delta_y:+.1f}%)</span>
</div>""", unsafe_allow_html=True)
                # 원클릭으로 단위 맞춤 수량 반영
                def _align_qty(c=mi['code'], aq=mi['aligned_qty']):
                    st.session_state[f"qty_{c}"] = aq
                    st.session_state.pop('_blocks', None)
                st.button(f"✅ {mi['aligned_qty']}개로 맞추기",
                          key=f"align_{mi['code']}", use_container_width=True,
                          on_click=_align_qty)

    # ── 추천 품목 (콤팩트 그리드) ──
    if recs:
        st.markdown("##### 💡 빈 공간 추천 <small style='color:#AAA;font-weight:400'>(마우스 올리면 상세)</small>",
                    unsafe_allow_html=True)
        NC = 5  # 고정 5칸
        # 헤더
        hcols = st.columns(NC)
        for ci in range(NC):
            with hcols[ci]:
                if ci < len(recs):
                    ri = recs[ci]
                    yc = '#27AE60' if ri['cy']>=80 else '#F39C12' if ri['cy']>=60 else '#E74C3C'
                    st.markdown(f"<div style='text-align:center'><b>B#{ri['bidx']+1}</b> "
                                f"<span style='color:{yc}'>{ri['cy']:.0f}%</span></div>",
                                unsafe_allow_html=True)
        # 1~3순위
        for rank in range(3):
            rcols = st.columns(NC)
            for ci in range(NC):
                with rcols[ci]:
                    if ci < len(recs) and rank < len(recs[ci]['recs']):
                        rec = recs[ci]['recs'][rank]
                        em = ['🥇','🥈','🥉'][rank]
                        st.button(f"{em}[{rec['code']}]+{rec['inc']:.0f}%",
                                  key=f"r{recs[ci]['bidx']}_{rec['code']}",
                                  use_container_width=True,
                                  on_click=_place_in_block,
                                  args=(recs[ci]['bidx'], rec['code'], rec['unit']),
                                  help=f"[{rec['code']}] {rec['matname']}\n"
                                       f"{rec['width']}×{rec['depth']}×{rec['height']}mm · {rec['unit']}개\n"
                                       f"수율: {rec['cy']:.1f}% → {rec['ny']:.1f}%")

    st.divider()

    # ── 재단 배치도 + 버튼 ──
    hd1, hd2, hd3, hd4, hd5 = st.columns([5.5, 1.3, 1.3, 1.3, 1.3])
    with hd1:
        st.markdown("#### 📐 재단 배치도")
    with hd2:
        st.button("↩️ 되돌리기", key="undo_btn", use_container_width=True,
                  on_click=_undo, disabled=not bool(st.session_state.get('_history')))
    with hd3:
        def _trigger_print():
            st.session_state['_print_ts'] = datetime.now().isoformat()
        st.button("🖨️ 인쇄", key="print_btn", use_container_width=True, on_click=_trigger_print)
    with hd4:
        def _trigger_deploy():
            st.session_state['_deploy_ts'] = datetime.now().isoformat()
        st.button("📡 현장 배포", key="deploy_btn", use_container_width=True, on_click=_trigger_deploy)
    with hd5:
        erp_pono = st.session_state.get('_erp_pono')
        has_erp = bool(erp_pono and st.session_state.get('_erp_data'))
        def _trigger_confirm():
            st.session_state['_confirm_ts'] = datetime.now().isoformat()
        st.button("✅ ERP 확정", key="confirm_btn", use_container_width=True,
                  on_click=_trigger_confirm, disabled=not has_erp)

    # SVG 생성
    all_svgs = []
    all_heights = []
    saved_counter = 0
    for bi in range(len(blocks)):
        if blocks[bi].is_saved:
            saved_counter += 1
            label = f"잔여 #{saved_counter}"
        else:
            label = None
        svg_html, svg_h = make_svg(blocks[bi], bi+1, saved_label=label)
        all_svgs.append(svg_html)
        all_heights.append(svg_h)

    # 현장 배포: 저장 + 전체화면 새 창
    deploy_ts = st.session_state.pop('_deploy_ts', None)
    if deploy_ts:
        save_plan(blocks, input_slots)
        avg_y_val = sum(b.yield_pct() for b in blocks) / len(blocks) if blocks else 0
        svg_cells = ""
        for i, s in enumerate(all_svgs):
            safe = s.replace('\\','\\\\').replace('`','\\`').replace('${','\\${')
            safe = safe.replace('max-width:340px;', '')
            svg_cells += f'<div class="cell">{safe}</div>'
            if (i+1) % 3 == 0:
                svg_cells += '<div style="clear:both;"></div>'

        deploy_html = """
        <script>
        var w = window.open('', '_blank');
        if(w){
            w.document.write(`<!DOCTYPE html><html><head><title>재단 배치도 - 현장</title>
            <style>
                * { margin:0; padding:0; box-sizing:border-box; }
                body { background:#FAF8F6; font-family:Malgun Gothic,NanumGothic,sans-serif; padding:20px 30px; }
                .header { text-align:center; margin-bottom:16px; }
                .header .brand { font-size:12px; font-weight:700; letter-spacing:2px; color:#7A736B; }
                .header .iloom { font-size:22px; font-weight:700; color:#dc2626; font-family:Georgia,serif; margin-left:6px; }
                .header h1 { font-size:24px; margin:8px 0 4px; color:#1A1816; }
                .header .info { font-size:13px; color:#A8A098; }
                .cell { width:33.33%; display:inline-block; vertical-align:top; padding:6px; }
                svg { width:100%!important; max-width:none!important; height:auto!important; display:block; }
                .toolbar { text-align:center; margin-top:12px; }
                .toolbar button {
                    padding:10px 32px; border:none; border-radius:8px; font-size:14px;
                    font-weight:500; cursor:pointer; margin:0 6px;
                    font-family:Malgun Gothic,sans-serif;
                }
                .btn-print { background:#dc2626; color:white; }
                .btn-print:hover { background:#b91c1c; }
                @page { size:A4 landscape; margin:5mm; }
                @media print { .toolbar { display:none; } body { padding:5mm; } .cell { padding:1mm; } }
            </style></head><body>
            <div class="header">
                <span class="brand">FURSYS GROUP</span><span class="iloom">iloom</span>
                <h1>📐 재단 배치도</h1>
                <p class="info">배포: """ + deploy_ts[:19].replace('T',' ') + """ | 블록 """ + str(len(blocks)) + """개 | 평균 수율 """ + f"{avg_y_val:.1f}" + """%</p>
            </div>
            <div>""" + svg_cells.replace('`','\\`') + """</div>
            <div class="toolbar">
                <button class="btn-print" onclick="window.print()">🖨️ 인쇄</button>
            </div>
            </body></html>`);
            w.document.close();
        } else { alert('팝업 차단됨 - 허용해 주세요.'); }
        </script><!-- """ + deploy_ts + """ -->"""
        components.html(deploy_html, height=0)
        st.toast("✅ 현장 배포 완료! 새 창에서 배치도를 확인하세요.")

    # ERP 확정: 최종 확정 수량(poqty) + 시뮬레이션 수율(after_result) 함께 전송
    # poqty = 사이드바에서 최종 조정된 개수 (input_slots 기준)
    # after_result = 현재 시뮬레이션 평균 수율
    confirm_ts = st.session_state.pop('_confirm_ts', None)
    if confirm_ts:
        erp_pono    = st.session_state.get('_erp_pono', '')
        erp_storecd = st.session_state.get('_erp_storecd', 'PAN')
        erp_data    = st.session_state.get('_erp_data', [])
        avg_y_after  = sum(b.yield_pct() for b in blocks) / len(blocks) if blocks else 0
        after_result = round(avg_y_after, 2)

        success_count = 0
        errors = []
        debug_rows = []  # 전송 데이터 디버그용

        for row in erp_data:
            matcd  = str(row.get('matcd', ''))
            matcol = str(row.get('matcol', 'XX'))
            po_seq = int(row.get('po_seq', 0))

            # 사이드바 최종 수량 우선, fallback은 원본 발주 수량
            item_code = MATCODE_TO_ITEM.get(matcd)
            if item_code and item_code in input_slots:
                poqty = int(input_slots[item_code])
            else:
                poqty = int(row.get('poqty', 0))

            debug_rows.append({
                'storecd': erp_storecd, 'pono': erp_pono,
                'matcd': matcd, 'matcol': matcol,
                'po_seq': po_seq, 'poqty': poqty,
                'after_result': after_result
            })

            _, err = call_erp_update_poqty(
                erp_storecd, erp_pono, matcd, matcol, po_seq, poqty, after_result)
            if err:
                errors.append(f"{matcd}: {err}")
            else:
                success_count += 1

        # 전송 데이터 항상 표시 (성공/실패 모두)
        with st.expander("📋 전송 데이터 확인", expanded=bool(errors)):
            st.dataframe(debug_rows, use_container_width=True)

        if errors:
            st.error(f"⚠️ {len(errors)}건 오류: {'; '.join(errors[:3])}")
        if success_count > 0:
            st.toast(f"✅ ERP 확정 완료! {success_count}건 전송 | 최종수율 {after_result}%")

    # 인쇄: SVG만 담은 새 창
    print_ts = st.session_state.pop('_print_ts', None)
    if print_ts:
        svg_cells = ""
        for i, s in enumerate(all_svgs):
            safe_svg = s.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
            # max-width 제거하여 인쇄 시 확대
            safe_svg = safe_svg.replace('max-width:340px;', '')
            svg_cells += f'<div class="cell">{safe_svg}</div>'
            if (i+1) % 3 == 0:
                svg_cells += '<div style="clear:both;"></div>'

        js_code = """
        <script>
        var w = window.open('', '_blank', 'width=1100,height=800');
        if(w){
            w.document.write(`<!DOCTYPE html><html><head><title>재단 배치도</title>
            <style>
                * { margin:0; padding:0; box-sizing:border-box; }
                body { margin:8mm; font-family:Malgun Gothic,NanumGothic,sans-serif; }
                h2 { text-align:center; font-size:18pt; margin-bottom:6mm; }
                .cell {
                    width:33.33%; display:inline-block; vertical-align:top;
                    padding:2mm;
                }
                svg { width:100%!important; max-width:none!important; height:auto!important; display:block; }
                @page { size:A4 landscape; margin:5mm; }
                @media print {
                    body { margin:0; }
                    .cell { padding:1mm; }
                }
            </style></head><body>
            <h2>소파 스펀지 재단 배치도</h2>
            <div>""" + svg_cells.replace('`','\\`') + """</div>
            </body></html>`);
            w.document.close();
            setTimeout(function(){w.print();},500);
        } else {
            alert('팝업이 차단되었습니다. 팝업을 허용해 주세요.');
        }
        </script>
        """
        components.html(js_code + f'<!-- {print_ts} -->', height=0)

    # 화면 표시
    # 일반 블록 기준 높이 계산
    normal_h = int(BLOCK_H * 0.26) + 30 + 20 + 25  # bh + PT + PB + margin
    for row in range(math.ceil(len(blocks)/3)):
        cols = st.columns(3)
        for ci in range(3):
            bi = row*3+ci
            if bi < len(blocks):
                with cols[ci]:
                    h = min(int(all_heights[bi] * 1.1) + 25, normal_h)
                    components.html(
                        f'<div style="text-align:center;">{all_svgs[bi]}</div>',
                        height=h, scrolling=False)

    # ── 잔여 블록 관리 (콤팩트) ──
    st.divider()
    has_res = False
    residual_items = []
    for bi, b in enumerate(blocks):
        usable = b.get_usable_free_rects()
        if usable:
            has_res = True
            desc = " + ".join(f"{r.w}×{r.h}" for r in usable)
            fp = (sum(r.area() for r in usable)/BLOCK_AREA)*100
            residual_items.append((bi, usable, desc, fp))
        elif b.free_rects:
            wp = (sum(r.area() for r in b.free_rects)/BLOCK_AREA)*100
            if wp > 0.5:
                residual_items.append((bi, None, None, wp))

    if residual_items:
        st.markdown("##### 📦 잔여 블록 <small style='color:#AAA;font-weight:400'>(저장하지 않으면 폐기)</small>",
                    unsafe_allow_html=True)
        NC = 5
        rc = st.columns(NC)
        for idx, (bi, usable, desc, fp) in enumerate(residual_items[:NC]):
            with rc[idx]:
                if usable:
                    st.markdown(f"<small><b>B#{bi+1}</b> {desc} ({fp:.0f}%)</small>", unsafe_allow_html=True)
                    if st.button("💾 저장", key=f"sv_{bi}", use_container_width=True):
                        # 잔여 영역의 면적과 바운딩박스 계산
                        rects = [(r.x, r.y, r.x+r.w, r.y+r.h) for r in usable]
                        oa = sum(r.area() for r in usable)
                        obb = [min(r[0] for r in rects), min(r[1] for r in rects),
                               max(r[2] for r in rects)-min(r[0] for r in rects),
                               max(r[3] for r in rects)-min(r[1] for r in rects)]
                        sb = Block(0, [copy.deepcopy(r) for r in usable], [], True, '', oa, obb)
                        add_saved(sb)
                        st.toast(f"B#{bi+1} 저장!"); st.rerun()
                else:
                    st.markdown(f"<small style='color:#AAA'><s>B#{bi+1}</s> {fp:.0f}% 자동폐기</small>",
                                unsafe_allow_html=True)

else:
    st.session_state.pop('_blocks', None)
    st.session_state.pop('_history', None)
    st.markdown("""
    <div style="text-align:center; padding:40px 20px; color:#BDC3C7;">
        <div style="font-size:3.5rem;">📐</div>
        <h2 style="color:#95A5A6;">발주 수량을 입력하세요</h2>
        <p>👈 왼쪽 사이드바에서 품목별 수량 입력 시 시뮬레이션이 시작됩니다.</p>
        <p style="font-size:0.85rem; color:#CCC; margin-top:20px;">☎️ 문의: 생산팀 조상원</p>
    </div>""", unsafe_allow_html=True)


