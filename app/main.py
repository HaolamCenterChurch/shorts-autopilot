#!/usr/bin/env python3
"""Shorts Autopilot — 16:9 원본 영상으로 9:16 완성본 쇼츠를 만드는 데스크톱 앱.

pywebview 진입점.
"""
import json
import os
import subprocess
import sys
import threading
import traceback

import webview
from webview.dom import DOMEventHandler

from app.paths import UI_HTML, DEFAULT_OUTPUT_ROOT
from app.doctor import diagnose, auto_fix
from app.ai_adapter import list_backends, set_ai_cmd
from app.orchestrator import generate_plans, revise_plans, generate_subtitles, revise_any, produce_short_pipeline
from core.pipeline import transcribe_video_full, TranscribeCancelled


class Api:
    def __init__(self):
        self._window = None
        self._transcribe_proc = None
        self._transcribe_cancel = threading.Event()
        self._state = {
            "media": None,
            "workdir": None,
            "outdir": DEFAULT_OUTPUT_ROOT,
            "transcript_text": "",
            "plans": [],
            "current_plan": None,
            "subtitles": []
        }

    def set_window(self, w):
        self._window = w

    def get_doctor_status(self):
        """환경 및 의존성 진단."""
        return diagnose()

    def run_auto_fix(self):
        """의존성 자동 다운로드/설치 시도."""
        return auto_fix()

    def get_ai_backends(self):
        """AI 백엔드 목록 및 현재 설정 조회."""
        return list_backends()

    def set_ai_backend(self, cmd):
        """AI CLI 명령 변경."""
        try:
            return {"ok": True, "cmd": set_ai_cmd(cmd)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def choose_video(self):
        """파일 열기 대화상자로 영상 선택."""
        try:
            res = self._window.create_file_dialog(webview.FileDialog.OPEN)
            return {"path": res[0] if res else None}
        except Exception as e:
            return {"path": None, "error": f"{type(e).__name__}: {e}"}

    def transcribe(self, path, force=False):
        """원본 영상 전사 수행."""
        self._transcribe_cancel.clear()
        self._transcribe_proc = None
        try:
            base = os.path.splitext(os.path.basename(path))[0]
            workdir = os.path.join(self._state["outdir"], base, "_work")
            os.makedirs(workdir, exist_ok=True)

            def on_progress(percent, phase=""):
                if self._window:
                    payload = json.dumps({"percent": percent, "phase": phase})
                    self._window.evaluate_js(f"window.onTranscribeProgress({payload})")

            def on_process(proc):
                self._transcribe_proc = proc

            r = transcribe_video_full(
                path, workdir, force=bool(force), progress_cb=on_progress,
                on_process=on_process, cancel_event=self._transcribe_cancel,
            )
            self._state["media"] = path
            self._state["workdir"] = workdir
            self._state["transcript_text"] = r["text"]

            return {
                "ok": True,
                "text": r["text"],
                "cached": r["cached"],
                "workdir": workdir,
                "path": path
            }
        except TranscribeCancelled as e:
            return {"ok": False, "cancelled": True, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": str(e), "trace": traceback.format_exc()}
        finally:
            self._transcribe_proc = None

    def cancel_transcribe(self):
        """진행 중인 전사를 취소한다."""
        self._transcribe_cancel.set()
        proc = self._transcribe_proc
        if proc and proc.poll() is None:
            proc.terminate()
            return {"ok": True}
        return {"ok": False, "error": "진행 중인 전사가 없습니다."}

    def generate_plans(self):
        """OREO 기반 쇼츠 3안 기획 생성."""
        try:
            plans = generate_plans(self._state["transcript_text"])
            self._state["plans"] = plans
            return {"ok": True, "plans": plans}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def revise_plans(self, plans, instruction):
        """자연어 지시로 기획안 일괄/선택 갱신."""
        try:
            new_plans = revise_plans(self._state["transcript_text"], plans, instruction)
            self._state["plans"] = new_plans
            return {"ok": True, "plans": new_plans}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def generate_subtitles_for_plan(self, plan_index):
        """선택한 기획안의 대본 기반 자막 청크 생성."""
        try:
            plan = self._state["plans"][plan_index]
            script = plan.get("script", "")
            chunks = generate_subtitles(script)
            self._state["subtitles"] = chunks
            self._state["current_plan"] = plan
            return {"ok": True, "chunks": chunks}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def ai_assist_edit(self, target_type, content_data, prompt):
        """상시 AI 수정 패널 지원: 현재 화면에 표시된 어떤 산출물이든 자연어로 수정 요청."""
        try:
            res = revise_any(str(content_data), prompt, f"대상 유형: {target_type}")
            return {"ok": True, "result": res}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def render_short(self, plan_index):
        """확정된 기획안으로 9:16 완성본 쇼츠 렌더링."""
        try:
            plan = self._state["plans"][plan_index]
            base = os.path.splitext(os.path.basename(self._state["media"]))[0]
            outdir = os.path.join(self._state["outdir"], base)
            
            def log_progress(msg):
                if self._window:
                    self._window.evaluate_js(f"window.onRenderProgress({json.dumps(msg)})")

            res = produce_short_pipeline(
                self._state["media"],
                plan,
                self._state["workdir"],
                outdir,
                callback=log_progress
            )
            return {"ok": True, "result": res, "outdir": outdir}
        except Exception as e:
            return {"ok": False, "error": str(e), "trace": traceback.format_exc()}

    def open_folder(self, path):
        """폴더 열기 (macOS/Linux/Windows)."""
        if sys.platform == "darwin":
            subprocess.run(["open", path])
        elif sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.run(["xdg-open", path])
        return {"ok": True}


def main():
    api = Api()
    window = webview.create_window(
        "Shorts Autopilot — 16:9 to 9:16 Vertical Video Studio",
        UI_HTML,
        js_api=api,
        width=1080,
        height=800,
        min_size=(800, 600)
    )
    api.set_window(window)

    def on_drop(e):
        files = (e.get("dataTransfer") or {}).get("files") or []
        path = files[0].get("pywebviewFullPath") if files else None
        if path:
            window.evaluate_js(f"window.onFileDropped({json.dumps(path)})")
        else:
            window.evaluate_js("window.notify('파일 경로를 읽지 못했습니다. 파일 선택 버튼을 이용해 주세요.', 'error')")

    def bind_dnd():
        window.dom.document.events.dragover += DOMEventHandler(lambda e: None, prevent_default=True)
        window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True)

    window.events.loaded += bind_dnd
    webview.start()


if __name__ == "__main__":
    main()
