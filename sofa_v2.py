"""
소파 스펀지 재단 시뮬레이션 v3.0 — 통합 포탈 + ERP 연동
"""
import streamlit as st
import streamlit.components.v1 as components
import json, os, copy, math, requests
import pandas as pd
from datetime import datetime
from io import BytesIO


# ============================================================
# 1. 설정
# ============================================================
st.set_page_config(page_title="스펀지 재단 시뮬레이션", layout="wide", page_icon="iloom_LOGO.png")

BLOCK_W = 1212
BLOCK_H = 1970
BLOCK_DEPTH = 600
BLOCK_AREA = BLOCK_W * BLOCK_H

STORAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
# 경로에 특수문자/한글 포함 시 대비
try:
    os.makedirs(STORAGE_DIR, exist_ok=True)
except OSError:
    # 한글 경로 문제 시 사용자 홈 폴더에 저장
    STORAGE_DIR = os.path.join(os.path.expanduser("~"), ".sofa_sim_data")
    os.makedirs(STORAGE_DIR, exist_ok=True)
STORAGE_FILE = os.path.join(STORAGE_DIR, "residual_blocks.json")

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
# ERP API 설정 (.env에서 로드)
# ============================================================
from dotenv import load_dotenv
load_dotenv()

ERP_API_URL = os.getenv("ERP_API_URL", "https://dev-erp-api2.fursys.com/api/erp/v1/material-order/list")
ERP_AUTH_KEY = os.getenv("ERP_AUTH_KEY", "")
ERP_IDENTIFIER = os.getenv("ERP_IDENTIFIER", "erp_admin")

def call_erp_api(storecd="PAN", customcd="T01IAN", mat_list=None):
    """ERP 자재발주 조회 API 호출"""
    payload = {
        "authentication_key": ERP_AUTH_KEY,
        "identifier_id": ERP_IDENTIFIER,
        "data": {
            "storecd": storecd,
            "customcd": customcd,
        }
    }
    if mat_list:
        payload["data"]["mat_list"] = mat_list

    try:
        resp = requests.post(ERP_API_URL, json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("_code") == 200:
            return result.get("data", []), None
        else:
            return None, f"API 오류: {result.get('_message', '알 수 없는 오류')}"
    except requests.exceptions.ConnectionError:
        return None, "연결 실패: ERP 서버에 접근할 수 없습니다. (사내망 확인)"
    except requests.exceptions.Timeout:
        return None, "시간 초과: 10초 이내에 응답이 없습니다."
    except Exception as e:
        return None, f"오류: {str(e)}"

def erp_data_to_dataframe(data):
    """ERP 응답 데이터를 DataFrame으로 변환"""
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    col_map = {
        'storecd': '사업장', 'ordno': '발주번호', 'matcd': '자재코드',
        'matcol': '컬러', 'ordqty': '발주수량', 'inqty': '입고수량',
        'basedlvdt': '납기일', 'jobsts': '상태', 'pono': '구매오더'
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    if '납기일' in df.columns:
        df['납기일'] = pd.to_datetime(df['납기일']).dt.strftime('%Y-%m-%d')
    return df

def match_erp_to_items(data):
    """ERP 발주 데이터를 ITEM_MASTER와 매칭하여 품목별 발주수량 산출"""
    qty_map = {code: 0 for code in ITEM_MASTER}
    for row in data:
        matcd = row.get('matcd', '')
        if matcd in MATCODE_TO_ITEM:
            item_code = MATCODE_TO_ITEM[matcd]
            unit = ITEM_MASTER[item_code]['unit']
            ordqty = int(row.get('ordqty', 0))
            # 발주수량 ÷ unit = 필요 재단 횟수 (올림)
            cuts = math.ceil(ordqty / unit) if unit > 0 else 0
            qty_map[item_code] += cuts
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

    def used_area(self): return sum(i['w']*i['h'] for i in self.items)

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

    def place_item(self, code, p):
        fr = self.free_rects[p['ri']]
        w, h = p['w'], p['h']
        self.items.append({'code': code, 'x': fr.x, 'y': fr.y, 'w': w, 'h': h, 'rot': p['rot']})
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
                'saved_date': self.saved_date or datetime.now().strftime('%Y-%m-%d %H:%M')}

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

def pack_items(order, saved=None):
    blocks = [copy.deepcopy(s) for s in (saved or [])]
    items = sorted([c for c,n in order.items() for _ in range(n) if n>0],
                   key=lambda c: ITEM_MASTER[c]['width']*ITEM_MASTER[c]['depth'], reverse=True)
    bc = max((b.id for b in blocks), default=0)
    for code in items:
        info = ITEM_MASTER[code]
        bb, bp = None, None
        for b in blocks:
            p = b.find_best_placement(info['width'], info['depth'])
            if p and (bp is None or p['score']<bp['score']): bb, bp = b, p
        if bb and bp: bb.place_item(code, bp)
        else:
            bc += 1; nb = Block(bc)
            p = nb.find_best_placement(info['width'], info['depth'])
            if p: nb.place_item(code, p); blocks.append(nb)
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
# 4. 저장/로드
# ============================================================
def load_saved():
    if os.path.exists(STORAGE_FILE):
        try:
            with open(STORAGE_FILE,'r',encoding='utf-8') as f:
                return [Block.from_dict(d) for d in json.load(f)]
        except: return []
    return []

def _save_raw(data):
    os.makedirs(STORAGE_DIR, exist_ok=True)
    tmp = STORAGE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # atomic rename
    import shutil
    shutil.move(tmp, STORAGE_FILE)

def add_saved(block):
    raw = []
    if os.path.exists(STORAGE_FILE):
        with open(STORAGE_FILE,'r',encoding='utf-8') as f: raw = json.load(f)
    nid = max((d['id'] for d in raw), default=0)+1
    d = block.to_dict(); d['id']=nid; d['is_saved']=True
    d['saved_date'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    raw.append(d); _save_raw(raw)

def remove_saved(bid):
    if os.path.exists(STORAGE_FILE):
        with open(STORAGE_FILE,'r',encoding='utf-8') as f: raw = json.load(f)
        _save_raw([d for d in raw if d['id']!=bid])

# ── 현장 배포용 저장/로드 ──
PLAN_FILE = os.path.join(STORAGE_DIR, "current_plan.json")
DEPLOY_LOG_FILE = os.path.join(STORAGE_DIR, "deploy_history.json")

def save_plan(blocks, input_slots):
    """관리자가 확정한 재단 계획 저장 + 이력 기록"""
    os.makedirs(STORAGE_DIR, exist_ok=True)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    avg_y = sum(b.yield_pct() for b in blocks) / len(blocks) if blocks else 0
    total_u = sum(input_slots.get(c,0) * ITEM_MASTER[c]['unit'] for c in ITEM_MASTER if input_slots.get(c,0)>0)

    plan = {
        'saved_at': now,
        'input_slots': {c: input_slots.get(c, 0) for c in ITEM_MASTER},
        'blocks': [b.to_dict() for b in blocks],
    }
    with open(PLAN_FILE, 'w', encoding='utf-8') as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    # 배포 이력 추가 — 블록별 기록
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
        label = f"잔여" if b.is_saved else f"Block"
        block_logs.append({
            'block_no': bi+1,
            'block_type': label,
            'yield': round(b.yield_pct(), 1),
            'items': block_items,
        })

    log_entry = {
        'deployed_at': now,
        'n_blocks': len(blocks),
        'avg_yield': round(avg_y, 1),
        'total_units': total_u,
        'blocks': block_logs,
    }
    history = []
    if os.path.exists(DEPLOY_LOG_FILE):
        try:
            with open(DEPLOY_LOG_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except: pass
    history.append(log_entry)
    history = history[-50:]  # 최근 50건만 유지
    with open(DEPLOY_LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def load_plan():
    """저장된 재단 계획 로드"""
    if os.path.exists(PLAN_FILE):
        try:
            with open(PLAN_FILE, 'r', encoding='utf-8') as f:
                plan = json.load(f)
            blocks = [Block.from_dict(d) for d in plan.get('blocks', [])]
            # from_dict가 is_saved=True로 설정하므로 원복
            for b in blocks:
                b.is_saved = False
            return plan, blocks
        except Exception:
            return None, []
    return None, []

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
    input_slots = {}
    for code, info in ITEM_MASTER.items():
        input_slots[code] = st.number_input(
            f"[{code}] {info['matname']}  ({info['unit']}개/블록)",
            min_value=0, step=1, key=f"qty_{code}",
            help=f"📦 {info['matcd']}\n📐 {info['width']}×{info['depth']}×{info['height']}mm")

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
    .block-container { max-width: 950px !important; padding-top: 1.5rem !important; }
    </style>""", unsafe_allow_html=True)

    st.markdown("### 📊 수율 배포 이력")

    def _close_log():
        st.session_state['_view_log'] = False
    st.button("← 시뮬레이션으로 돌아가기", key="back_from_log", on_click=_close_log)

    if os.path.exists(DEPLOY_LOG_FILE):
        try:
            with open(DEPLOY_LOG_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
            if history:
                # 블록별 → 품목별로 행 전개
                rows = []
                for i, h in enumerate(history):
                    deploy_no = i + 1
                    dt = h['deployed_at']

                    # 새 포맷 (blocks 배열)
                    if 'blocks' in h:
                        for bl in h['blocks']:
                            if bl.get('items'):
                                for item in bl['items']:
                                    rows.append({
                                        '배포No': deploy_no, '배포일시': dt,
                                        '블록': f"{bl['block_type']} #{bl['block_no']}",
                                        '블록수율(%)': bl['yield'],
                                        '자재코드': item.get('matcd',''),
                                        '색상': item.get('matcol',''),
                                        '품목명': item.get('matname',''),
                                        '생산수량': item.get('unit', ''),
                                    })
                            else:
                                rows.append({
                                    '배포No': deploy_no, '배포일시': dt,
                                    '블록': f"{bl['block_type']} #{bl['block_no']}",
                                    '블록수율(%)': bl['yield'],
                                })
                    # 이전 포맷 호환
                    elif h.get('items_detail'):
                        for d in h['items_detail']:
                            rows.append({
                                '배포No': deploy_no, '배포일시': dt,
                                '블록': '-', '블록수율(%)': h.get('avg_yield',''),
                                '자재코드': d.get('matcd',''), '색상': d.get('matcol',''),
                                '품목명': d.get('matname',''), '생산수량': d.get('qty',''),
                            })
                    elif h.get('items'):
                        for k, v in h['items'].items():
                            if k in ITEM_MASTER:
                                info = ITEM_MASTER[k]
                                rows.append({
                                    '배포No': deploy_no, '배포일시': dt,
                                    '블록': '-', '블록수율(%)': h.get('avg_yield',''),
                                    '자재코드': info['matcd'], '색상': info['matcol'],
                                    '품목명': info['matname'], '생산수량': v,
                                })

                hist_df = pd.DataFrame(rows)
                display_cols = ['배포No','배포일시','블록','블록수율(%)','자재코드','색상','품목명','생산수량']
                display_cols = [c for c in display_cols if c in hist_df.columns]

                st.dataframe(hist_df[display_cols], use_container_width=True, hide_index=True, height=500)
                st.caption(f"총 {len(history)}건 배포 | 평균 수율 {sum(h['avg_yield'] for h in history)/len(history):.1f}%")

                # 삭제 + 다운로드
                dc1, dc2, dc3 = st.columns([2, 1, 1])
                with dc1:
                    del_idx = st.number_input("삭제할 No 입력", min_value=1, max_value=len(history),
                                              value=1, step=1, key="del_log_no")
                with dc2:
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("🗑️ 선택 삭제", key="del_log_btn", use_container_width=True):
                        actual_idx = len(history) - del_idx
                        if 0 <= actual_idx < len(history):
                            history.pop(actual_idx)
                            with open(DEPLOY_LOG_FILE, 'w', encoding='utf-8') as f:
                                json.dump(history, f, ensure_ascii=False, indent=2)
                            st.rerun()
                with dc3:
                    st.markdown("<br>", unsafe_allow_html=True)
                    buf = BytesIO()
                    hist_df[display_cols].to_excel(buf, index=False, engine='openpyxl')
                    buf.seek(0)
                    st.download_button("📥 엑셀 다운로드", data=buf,
                                       file_name=f"수율이력_{datetime.now().strftime('%Y%m%d')}.xlsx",
                                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                       use_container_width=True)
            else:
                st.info("배포 이력이 없습니다.")
        except Exception as e:
            st.error(f"이력 로드 오류: {e}")
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

# ── ERP 발주 조회 (항상 표시) ──
with st.expander("📡 ERP 발주 조회 → 수량 자동 반영", expanded=False):
    erp_c1, erp_c2, erp_c3 = st.columns([1.5, 1.5, 1])
    with erp_c1:
        storecd = st.text_input("사업장코드", value="PAN", key="erp_store")
    with erp_c2:
        customcd = st.text_input("거래처코드", value="T01IAN", key="erp_custom")
    with erp_c3:
        st.markdown("<br>", unsafe_allow_html=True)
        erp_query = st.button("🔍 조회", key="erp_query", use_container_width=True)

    if erp_query:
        with st.spinner("ERP 조회 중..."):
            data, err = call_erp_api(storecd, customcd)
        if err:
            st.error(err)
        elif data:
            st.session_state['_erp_data'] = data
            st.success(f"✅ {len(data)}건 조회됨")
        else:
            st.warning("조회 결과가 없습니다.")

    erp_data = st.session_state.get('_erp_data')
    if erp_data:
        df = erp_data_to_dataframe(erp_data)
        df['품목'] = df['자재코드'].map(lambda x: f"[{MATCODE_TO_ITEM[x]}] {ITEM_MASTER[MATCODE_TO_ITEM[x]]['matname']}" if x in MATCODE_TO_ITEM else "—")
        st.dataframe(df, use_container_width=True, hide_index=True)

        qty_map = match_erp_to_items(erp_data)
        matched = {c: q for c, q in qty_map.items() if q > 0}
        if matched:
            summary = " / ".join(f"[{c}] {ITEM_MASTER[c]['matname']}: {q}" for c, q in matched.items())
            st.info(f"🔗 매칭 결과 (발주수량÷unit 올림): {summary}")

        bc1, bc2 = st.columns(2)
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
                               file_name=f"ERP발주조회_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True)

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
    total_u = sum(input_slots[c]*ITEM_MASTER[c]['unit'] for c in input_slots if input_slots[c]>0)
    recs = get_recommendations(blocks)

    # ── 메트릭 ──
    ycolor = '#27AE60' if avg_y>=80 else '#F39C12' if avg_y>=60 else '#E74C3C'
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("투입 블록", f"{len(blocks)}개")
    c2.metric("생산 품목수", f"{active}종")
    c3.metric("평균 수율", f"{avg_y:.1f}%")
    c4.metric("총 생산수량", f"{total_u}개")

    # ── 추천 품목 (콤팩트 그리드) ──
    if recs:
        st.markdown("##### 💡 빈 공간 추천 <small style='color:#AAA;font-weight:400'>",
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
    hd1, hd2, hd3, hd4 = st.columns([7, 1.3, 1.3, 1.3])
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
        <h2 style="color:#95A5A6;">발주 수량을 입력하세요</h2>
        <p>👈 왼쪽 사이드바에서 품목별 수량 입력 시 시뮬레이션이 시작됩니다.</p>
        <p style="font-size:0.85rem; color:#CCC; margin-top:20px;">☎️ 문의: 생산팀 조상원</p>
    </div>""", unsafe_allow_html=True)


