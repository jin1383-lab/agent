import streamlit as st
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import isodate
import pandas as pd
import json

# Gemini API 연동을 위한 최신 정식 라이브러리인 google-genai를 사용합니다.
try:
    from google import genai
    from google.genai import types
except ImportError:
    st.error("⚠️ Gemini 에이전트 기능을 위해 'pip install google-genai' 라이브러리가 필요합니다. 요구사항에 추가해주세요.")

# --- 페이지 설정 ---
st.set_page_config(
    page_title="YouTube Gemini Agent Chatbot V1.0",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- 세션 상태 초기화 ---
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []  # 대화 기록 저장
if "agent_raw_data" not in st.session_state:
    st.session_state.agent_raw_data = []  # 유튜브 수집 데이터 임시 저장

# --- 헬퍼 함수: 숫자 및 시간 변환 ---
def format_duration(seconds):
    m, s = divmod(seconds, 60)
    return f"{m}분 {s}초" if m > 0 else f"{s}초"

def format_num(n):
    if n >= 100000000: return f"{n / 100000000:.1f}억"
    if n >= 10000: return f"{n / 10000:.1f}만"
    return f"{n:,}"

# --- 핵심 기능: 유튜브 데이터 검색 엔진 ---
def search_youtube_core(api_key, keyword, region_code="KR", date_option="최근 1달"):
    try:
        youtube = build("youtube", "v3", developerKey=api_key)
        
        # 기간 변환
        now = datetime.utcnow()
        delta_map = {"최근 3일": 3, "최근 1주일": 7, "최근 1달": 30, "최근 3개월": 90, "전체": None}
        days = delta_map.get(date_option, 30)
        published_after = (now - timedelta(days=days)).isoformat() + "Z" if days else None

        # Step 1: Search API
        search_kwargs = {"part": "snippet", "q": keyword, "type": "video", "maxResults": 10}
        if region_code: search_kwargs["regionCode"] = region_code
        if published_after: search_kwargs["publishedAfter"] = published_after
        
        search_res = youtube.search().list(**search_kwargs).execute()
        video_ids = [item["id"]["videoId"] for item in search_res.get("items", [])]
        
        if not video_ids:
            return []
            
        # Step 2: Videos API
        video_res = youtube.videos().list(part="statistics,snippet,contentDetails", id=",".join(video_ids)).execute()
        
        # Step 3: Channels API
        channel_ids = list(set([item["snippet"]["channelId"] for item in video_res.get("items", [])]))
        channel_res = youtube.channels().list(part="statistics", id=",".join(channel_ids)).execute()
        channel_map = {c["id"]: int(c["statistics"].get("subscriberCount", 1)) for c in channel_res.get("items", [])}
        
        # 데이터 정제 및 가공
        parsed_list = []
        for item in video_res.get("items", []):
            views = int(item["statistics"].get("viewCount", 0))
            subs = channel_map.get(item["snippet"]["channelId"], 1)
            if subs == 0: subs = 1
            iso_duration = item["contentDetails"].get("duration", "PT0S")
            duration_sec = int(isodate.parse_duration(iso_duration).total_seconds())
            
            parsed_list.append({
                "id": item["id"],
                "title": item["snippet"]["title"],
                "channelTitle": item["snippet"]["channelTitle"],
                "viewCount": views,
                "subCount": subs,
                "duration": duration_sec,
                "viralScore": (views / subs) * 100,
                "url": f"https://youtube.com/watch?v={item['id']}"
            })
        return parsed_list
    except Exception as e:
        return f"유튜브 데이터 수집 중 에러 발생: {e}"

# --- 사이드바 제어 패널 (설정 및 Key 관리) ---
with st.sidebar:
    st.title("🤖 Gemini Agent")
    st.markdown("---")
    
    # API 키 관리 (Secrets 자동 탐색 및 수동 입력 백업 구조)
    yt_key = st.secrets.get("YOUTUBE_API_KEY", st.text_input("🔑 YouTube API Key", type="password"))
    gemini_key = st.secrets.get("GEMINI_API_KEY", st.text_input("✨ Gemini API Key", type="password"))
    
    if yt_key and gemini_key:
        st.success("✅ Gemini 에이전트 가동 준비 완료!")
    else:
        st.warning("⚠️동작을 위해 YouTube 키와 Gemini API 키가 모두 필요합니다.")

    st.markdown("---")
    if st.button("🧹 대화 기록 초기화", use_container_width=True):
        st.session_state.chat_history = []
        st.session_state.agent_raw_data = []
        st.rerun()

# --- 메인 챗봇 인터페이스 UI ---
st.title("💬 유튜브 분석 Gemini 에이전트")
st.caption("질문을 입력하면 제미나이가 스스로 최적의 단어를 정해 유튜브를 분석합니다.")

# 1. 기존 대화 내용 출력
for chat in st.session_state.chat_history:
    with st.chat_message(chat["role"]):
        st.write(chat["content"])

# 2. 사용자 질문 처리
if user_input := st.chat_input("예: 요즘 난리난 무선 게이밍 마우스 트렌드 알려줘"):
    
    # 유저 메시지 렌더링 및 기록
    with st.chat_message("user"):
        st.write(user_input)
    st.session_state.chat_history.append({"role": "user", "content": user_input})
    
    if not yt_key or not gemini_key:
        with st.chat_message("assistant"):
            st.error("⚠️ 사이드바에 YouTube API Key와 Gemini API Key를 입력해야 진행할 수 있습니다.")
    else:
        # Gemini 에이전트 분석 시작
        with st.chat_message("assistant"):
            with st.spinner("Gemini가 질문에서 검색 키워드를 가공하는 중..."):
                try:
                    # 최신 google-genai SDK 방식으로 클라이언트 선언
                    client = genai.Client(api_key=gemini_key)
                    
                    system_instruction = (
                        "당신은 유튜브 트렌드 분석가입니다. 유저의 질문을 받으면 유튜브에서 시장 조사를 하기 가장 적합한 '단 하나의 검색어'를 만들어야 합니다. "
                        "반드시 오직 단어 형태의 JSON 구조로만 응답하세요. 다른 설명 문장은 절대 금지합니다.\n"
                        "JSON 양식: {\"extracted_keyword\": \"가공된 키워드\"}"
                    )
                    
                    # Gemini 1.5 Flash 모델 호출 및 정밀 JSON 구조 고정
                    response = client.models.generate_content(
                        model='gemini-1.5-flash',
                        contents=user_input,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            response_mime_type="application/json",
                        ),
                    )
                    
                    # 결과 해석
                    result_json = json.loads(response.text)
                    search_word = result_json.get("extracted_keyword", user_input)
                    
                    st.write(f"✨ **Gemini 판단 키워드:** `{search_word}` 데이터를 기반으로 분석을 전개합니다.")
                    
                except Exception as e:
                    st.error(f"Gemini 키워드 추출 실패: {e}")
                    search_word = user_input

            # 유튜브 데이터 수집 진행
            with st.spinner(f"'{search_word}' 실시간 유튜브 데이터 수집 엔진 가동..."):
                youtube_results = search_youtube_core(yt_key, search_word)
                
            if isinstance(youtube_results, list) and youtube_results:
                st.session_state.agent_raw_data = youtube_results
                
                # 수집된 통계 지표를 바탕으로 브리핑 리포트 지시
                with st.spinner("Gemini가 실시간 지표 분석 및 리포트 작성 중..."):
                    data_summary = ""
                    for i, v in enumerate(youtube_results[:5]):
                        data_summary += f"- 제목: {v['title']} / 채널: {v['channelTitle']} / 조회수: {v['viewCount']} / 성과지수: {v['viralScore']:.1f}%\n"
                    
                    report_prompt = (
                        f"당신은 프로페셔널한 유튜브 데이터 분석 에이전트입니다. 아래 제공된 최신 수집 통계를 바탕으로 사용자의 원래 질문인 '{user_input}'에 정성껏 답하는 종합 분석 브리핑 리포트를 가독성 있게 작성해 주세요.\n\n"
                        f"[유튜브 실시간 수집 지표]\n{data_summary}\n"
                        "구독자 대비 조회수가 폭발한 '성과지수'가 높은 영상들의 성공 요인을 간단히 짚어보고, 현재 소비자들이 이 키워드에서 어떤 포인트에 열광하는지 트렌드를 깔끔하게 마크다운 형태로 리포팅해 주세요. 3줄 핵심 요약 요약도 포함해 주세요."
                    )
                    
                    final_report = client.models.generate_content(
                        model='gemini-1.5-flash',
                        contents=report_prompt
                    )
                    
                    ai_analysis_report = final_report.text
                    
                    # 최종 리포트 출력 및 저장
                    st.write(ai_analysis_report)
                    st.session_state.chat_history.append({"role": "assistant", "content": ai_analysis_report})
                    
                    # 시각적 보완용 주요 영상 추천 레이아웃 출력
                    st.markdown("---")
                    st.subheader(f"📊 Gemini 추천 핵심 영상 리스트")
                    cols = st.columns(3)
                    for idx, item in enumerate(youtube_results[:3]):
                        with cols[idx]:
                            with st.container(border=True):
                                st.markdown(f"**[{item['title']}]({item['url']})**")
                                st.caption(f"👤 {item['channelTitle']}")
                                st.caption(f"📈 성과지수: {item['viralScore']:.1f}%")
                                st.metric(label="조회수", value=format_num(item['viewCount']))
            else:
                error_msg = f"❌ '{search_word}' 키워드 검색 결과가 없거나 유튜브 할당량 초과 에러가 발생해 리포트를 작성하지 못했습니다."
                st.write(error_msg)
                st.session_state.chat_history.append({"role": "assistant", "content": error_msg})
