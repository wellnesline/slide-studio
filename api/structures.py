"""話の構成(起承転結)レジストリ。deckgen genres の chapter_flow / storyline_framework を土台に、
slide-studio 用に凝縮した narrative arc。recommend が1つ選び、organize がこの章立てを背骨にする。
"""
from __future__ import annotations

STRUCTURES = [
    {"id": "consulting", "name": "コンサル提案", "framework": "状況→課題→問い→答え(SCQA)",
     "desc": "課題を分析して打ち手を示す提案",
     "chapters": ["表紙", "要点サマリー", "現状", "課題の核心", "打ち手の提案", "仕組み・進め方", "期待できる変化", "次の一手"]},
    {"id": "service-intro", "name": "サービス紹介", "framework": "課題→解決→証拠→行動(PAS)",
     "desc": "サービスの価値を課題起点で伝える",
     "chapters": ["表紙", "よくある課題", "解決するもの", "特徴・強み", "他社との比較", "導入の効果", "料金", "導入の流れ", "次の一手"]},
    {"id": "case-study", "name": "導入事例", "framework": "課題→打ち手→効果(BAB)",
     "desc": "実例で効果を示す",
     "chapters": ["表紙", "導入前の課題", "選んだ打ち手", "得られた効果", "実績のまとめ", "次の一手"]},
    {"id": "pitch", "name": "ピッチ", "framework": "問題→解決→伸び",
     "desc": "投資家・意思決定者向けの提案",
     "chapters": ["フック", "解くべき問題", "解決策", "市場・機会", "なぜ我々か", "実績・トラクション", "お願い(Ask)"]},
    {"id": "education", "name": "教育・解説", "framework": "順を追って理解",
     "desc": "順番に理解させる解説",
     "chapters": ["表紙", "全体像", "前提", "要点その1", "要点その2", "手順・実践", "まとめ"]},
    {"id": "report", "name": "報告・まとめ", "framework": "要点→現状→示唆",
     "desc": "状況を整理して共有する",
     "chapters": ["表紙", "要点", "現状の数値", "分かったこと", "課題と示唆", "次の一手"]},
]
_BY_ID = {s["id"]: s for s in STRUCTURES}


def get(sid: str):
    return _BY_ID.get(sid)


def default():
    return STRUCTURES[0]
