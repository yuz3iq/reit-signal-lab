"""
DART(금융감독원 전자공시) 공시 기준 배당수익률.

의도: attractiveness.py의 배당수익률은 WiseReport 페이지를 스크래핑한 "시가 기준"
수치다(시장 가격이 실시간으로 바뀌므로 매번 다른 값이 나올 수 있음). 이 모듈은 같은
개념의 배당수익률을 회사가 사업보고서/분기보고서에 법적으로 공시한 "배당에 관한
사항"(alotMatter API)에서 독립적으로 가져온다 -- 스크래핑 결과와 나란히 보여줄 수
있는, 출처가 분명한 두 번째 REAL 데이터. 두 수치가 크게 다르면(공시 시점 주가 vs
현재 시가 차이, 특별배당 포함 여부 등) 그 자체로 유용한 신호다.

DART corp_code(8자리, 상장 종목코드와는 다른 내부 고유번호)는 opendart.fss.or.kr의
corpCode.xml 벌크 다운로드(전체 상장사 ZIP)로만 조회 가능하다. 이 세션의 샌드박스는
그 다운로드를 처리할 수 없어(바이너리 zip), DART 웹사이트(dart.fss.or.kr)의
"기업개황" 검색으로 5종목 각각을 직접 찾아 아래에 정적으로 매핑했다 -- 대상이 5개
고정 종목뿐이라 정적 매핑으로 충분하며, corp_code는 상장폐지 등이 없는 한 바뀌지
않는다.
"""
import datetime

import requests

DART_BASE = "https://opendart.fss.or.kr/api"

# 종목코드 -> DART corp_code (dart.fss.or.kr 기업개황 검색으로 확인, 2026-09-17)
CORP_CODES = {
    "448730": "01688896",  # 삼성FN리츠
    "451800": "01669226",  # 한화리츠
    "417310": "01180118",  # 코람코더원리츠
    "338100": "01391033",  # NH프라임리츠
    "293940": "01276594",  # 신한알파리츠
}

# 사업보고서/3분기/반기/1분기 -- REIT마다 결산월이 달라 어떤 보고서에 최신 배당
# 데이터가 있는지 다르므로 전부 시도한다.
REPRT_CODES = ["11011", "11014", "11012", "11013"]


def _fetch_alotmatter(corp_code, bsns_year, reprt_code, dart_key, timeout=10):
    url = (
        f"{DART_BASE}/alotMatter.json?crtfc_key={dart_key}&corp_code={corp_code}"
        f"&bsns_year={bsns_year}&reprt_code={reprt_code}"
    )
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "000":
        return []
    return data.get("list", [])


def fetch_dart_dividend_yield(code, dart_key, years_back=2, timeout=10):
    """가장 최근에 공시된 '현금배당수익률(%)' 값을 반환.
    여러 (연도, 보고서유형) 조합을 조회해 stlm_dt(결산기준일)가 가장 최근인 값을 고른다.

    stock_knd 라벨은 종목마다 다르게 나타난다(예: 삼성FN리츠는 실제 값이
    stock_knd="보통주" 행에, NH프라임리츠는 같은 값이 stock_knd="-" 행에 들어있음 --
    보통주/우선주 구분 없이 단일 종류만 보고하는 회사는 "-"를 쓰는 것으로 보임).
    그래서 stock_knd로 필터링하지 않고, 숫자로 파싱되는(즉 실제 값이 채워진) 행을
    모두 후보로 삼되 "우선주"만 제외하고, 동일 stlm_dt 내에서는 "보통주" 라벨을
    우선한다. 조회 실패했거나 값이 없으면 None -- 추정치로 채우지 않음."""
    corp_code = CORP_CODES.get(code)
    if not corp_code:
        return None

    this_year = datetime.datetime.utcnow().year
    best = None  # (stlm_dt, rank, value_pct, rcept_no)
    for year in range(this_year, this_year - years_back - 1, -1):
        year_best = None
        for reprt_code in REPRT_CODES:
            try:
                rows = _fetch_alotmatter(corp_code, year, reprt_code, dart_key, timeout)
            except Exception:
                continue
            for row in rows:
                if row.get("se") != "현금배당수익률(%)":
                    continue
                stock_knd = (row.get("stock_knd") or "").strip()
                if stock_knd == "우선주":
                    continue  # 우선주 배당수익률은 제외, 보통주/구분없음 기준만 사용
                thstrm = (row.get("thstrm") or "").replace(",", "").strip()
                try:
                    val = float(thstrm)
                except ValueError:
                    continue
                stlm_dt = row.get("stlm_dt", "")
                rank = 1 if stock_knd == "보통주" else 0
                cand = (stlm_dt, rank, val, row.get("rcept_no"))
                if year_best is None or cand[:2] > year_best[:2]:
                    year_best = cand
        if year_best is not None:
            best = year_best
            break  # 이 연도에서 찾았으면 더 과거로 갈 필요 없음(최신 우선)

    if best is None:
        return None

    stlm_dt, _rank, val, rcept_no = best
    return {
        "value_pct": val,
        "stlm_dt": stlm_dt,  # YYYY-MM-DD, 결산기준일 (공시 시점 주가 기준으로 계산된 값)
        "rcept_no": rcept_no,
        "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
    }


if __name__ == "__main__":
    import json
    import os

    key = os.environ.get("DART_API_KEY", "")
    out = {code: fetch_dart_dividend_yield(code, key) for code in CORP_CODES}
    print(json.dumps(out, ensure_ascii=False, indent=2))
